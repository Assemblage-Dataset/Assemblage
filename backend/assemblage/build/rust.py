"""The Rust (cargo / rustc) build strategy with pluggable codegen backends.

``RustBuildStrategy`` implements the same :class:`~assemblage.build.strategy.BuildStrategy`
lifecycle the Linux gcc/clang strategy does — ``prepare``/``build``/``find_binaries``/
``debug_info`` — but drives ``cargo`` instead of make/cmake:

- **prepare** requires a ``Cargo.toml`` at the clone root and records the workspace
  member package ids (``cargo metadata --no-deps``), so build-script and dependency
  artifacts can be filtered out later.
- **build** maps ``build_mode`` x ``compiler_flag`` onto cargo profile env-vars
  (``CARGO_PROFILE_*``), selects the codegen backend via ``RUSTFLAGS
  -Zcodegen-backend=...``, and parses cargo's ``--message-format=json`` stream for the
  workspace ``bin``/``example``/``cdylib`` artifacts. ``--locked`` is used when a
  ``Cargo.lock`` exists, retried once unlocked on failure.
- **find_binaries** returns the recorded artifact paths, falling back to a shallow
  ``target/<profile>/`` walk only when the JSON produced nothing on a clean exit.
- **debug_info** reuses the shared DWARF extractor byte-identically, then post-processes
  each function entry in the strategy: batch demangling through ``rustfilt`` and origin
  tagging (in_repo / dependency / stdlib / other).

A codegen backend is modelled as a small :class:`RustCodegenAdapter` (flags + env +
:class:`DebugInfoCaps` descriptor), not a new worker type.
"""

import json
import logging
import os
import shlex
import subprocess
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass

from assemblage.build.commands import CommandResult, run_command
from assemblage.build.discovery import is_elf_executable
from assemblage.build.linux import work_base_path
from assemblage.build.strategy import BuildStrategy
from assemblage.dwarf.extract import extract_dwarf_info
from assemblage.enums import BuildStatus, RustCodegenBackend
from assemblage.settings import BuilderSettings

logger = logging.getLogger(__name__)

# The nightly the image pins (docker/rust/Dockerfile ARG RUST_TOOLCHAIN). Kept as a
# module default so a non-container run still probes a sensible toolchain; the image
# also exports RUST_TOOLCHAIN, which overrides this. Passed as `cargo +<pin>` so a
# repo's rust-toolchain.toml can't divert the build off the backend-capable nightly.
DEFAULT_RUST_TOOLCHAIN = "nightly-2026-06-15"

_FIXED_RUSTFLAGS = ["-Csymbol-mangling-version=v0"]

# cargo profile name -> the output subdirectory cargo actually writes under target/.
_PROFILE_DIR = {"dev": "debug", "release": "release"}

_OPT_LEVEL_BY_FLAG: dict[str, str] = {
    "-O0": "0",
    "-O1": "1",
    "-O2": "2",
    "-O3": "3",
    "-Os": "s",
    "-Oz": "z",
}


def toolchain() -> str:
    """The pinned nightly to build with (env ``RUST_TOOLCHAIN`` or the default)."""
    return os.environ.get("RUST_TOOLCHAIN", DEFAULT_RUST_TOOLCHAIN)


def opt_level_for_flag(flag: str) -> str:
    """Map a ``-O*`` compiler flag to a cargo ``opt-level`` value (fail on unknown)."""
    try:
        return _OPT_LEVEL_BY_FLAG[flag]
    except KeyError:
        raise ValueError(f"no cargo opt-level mapping for compiler flag {flag!r}") from None


# --- codegen backends --------------------------------------------------------


@dataclass(frozen=True)
class DebugInfoCaps:
    """What debug info a codegen backend is expected to emit today."""

    functions: bool
    lines: bool
    variables: bool
    maturity: str  # "stable" | "experimental"


class RustCodegenAdapter(ABC):
    """A codegen backend as flags + env + a capability descriptor."""

    name: RustCodegenBackend
    caps: DebugInfoCaps

    @abstractmethod
    def rustflags(self) -> list[str]:
        """Backend-selecting ``RUSTFLAGS`` fragments (empty for the default LLVM)."""

    def extra_env(self) -> dict[str, str]:
        """Extra process env the backend needs (none by default)."""
        return {}


class LLVMAdapter(RustCodegenAdapter):
    name = RustCodegenBackend.LLVM
    caps = DebugInfoCaps(functions=True, lines=True, variables=True, maturity="stable")

    def rustflags(self) -> list[str]:
        return []


class CraneliftAdapter(RustCodegenAdapter):
    name = RustCodegenBackend.CRANELIFT
    caps = DebugInfoCaps(functions=True, lines=True, variables=False, maturity="experimental")

    def rustflags(self) -> list[str]:
        return ["-Zcodegen-backend=cranelift"]


class GccAdapter(RustCodegenAdapter):
    name = RustCodegenBackend.GCC
    # R4 empirics (2026-07-16 backend smoke on rust-golden, nightly-2026-06-15):
    # cg_gcc builds exit 0 with non-empty DWARF (functions + line entries confirmed
    # at -O0 and -O2 — the service ships ENABLED). variables=False: variable info was
    # NOT verified and cg_gcc emits no full lexical scopes (design §3.1); its source-
    # file/origin attribution is also weaker than llvm/cranelift (the smoke resolved
    # zero in_repo origins). Maturity stays experimental.
    caps = DebugInfoCaps(functions=True, lines=True, variables=False, maturity="experimental")

    def rustflags(self) -> list[str]:
        return ["-Zcodegen-backend=gcc"]


_ADAPTERS: dict[RustCodegenBackend, RustCodegenAdapter] = {
    RustCodegenBackend.LLVM: LLVMAdapter(),
    RustCodegenBackend.CRANELIFT: CraneliftAdapter(),
    RustCodegenBackend.GCC: GccAdapter(),
}


def make_adapter(backend: RustCodegenBackend) -> RustCodegenAdapter:
    """The adapter singleton for a codegen backend."""
    return _ADAPTERS[backend]


# --- pure helpers (unit-tested without a real toolchain) ---------------------


def cargo_env(
    *,
    build_mode: str,
    compiler_flag: str,
    adapter: RustCodegenAdapter,
    cargo_home: str,
    target_dir: str,
) -> dict[str, str]:
    """Assemble the cargo profile/backend env for one build.

    ``Debug`` -> the ``dev`` profile (debug=2, debug-assertions on by default);
    ``RelWithDebInfo``/``Release`` -> the ``release`` profile with ``debug`` 2 or 0 and
    an explicit ``strip=none`` so Release keeps its symtab (matching the C corpus).
    """
    opt = opt_level_for_flag(compiler_flag)
    profile = "dev" if build_mode == "Debug" else "release"
    env: dict[str, str] = {}
    if profile == "dev":
        env["CARGO_PROFILE_DEV_OPT_LEVEL"] = opt
    else:
        env["CARGO_PROFILE_RELEASE_OPT_LEVEL"] = opt
        env["CARGO_PROFILE_RELEASE_DEBUG"] = "2" if build_mode == "RelWithDebInfo" else "0"
        env["CARGO_PROFILE_RELEASE_STRIP"] = "none"
    env["CARGO_INCREMENTAL"] = "0"
    env["CARGO_HOME"] = cargo_home
    env["CARGO_TARGET_DIR"] = target_dir
    env.update(adapter.extra_env())
    env["RUSTFLAGS"] = " ".join([*adapter.rustflags(), *_FIXED_RUSTFLAGS])
    return env


def parse_cargo_artifacts(stdout: str, member_ids: frozenset[str]) -> list[str]:
    """Executable/cdylib paths from cargo's JSON stream, workspace members only.

    Keeps ``compiler-artifact`` messages whose ``package_id`` is a workspace member and
    whose target ``kind`` includes ``bin``/``example``/``cdylib`` — dropping build-script
    (``custom-build``) and dependency artifacts. An empty ``member_ids`` disables the
    membership filter (used only when ``cargo metadata`` failed).
    """
    paths: list[str] = []
    seen: set[str] = set()
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("reason") != "compiler-artifact":
            continue
        if member_ids and str(msg.get("package_id", "")) not in member_ids:
            continue
        target = msg.get("target") or {}
        kinds = set(target.get("kind") or [])
        if not kinds & {"bin", "example", "cdylib"}:
            continue
        candidates: list[str] = []
        executable = msg.get("executable")
        if executable:
            candidates.append(str(executable))
        for fname in msg.get("filenames") or []:
            if str(fname).endswith(".so"):
                candidates.append(str(fname))
        for candidate in candidates:
            if candidate not in seen:
                seen.add(candidate)
                paths.append(candidate)
    return paths


def rendered_diagnostics(stdout: str, stderr: str) -> str:
    """The human-readable build output: cargo's rendered diagnostics + stderr."""
    rendered: list[str] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("reason") == "compiler-message":
            text = (msg.get("message") or {}).get("rendered")
            if text:
                rendered.append(str(text))
    return "\n".join([*rendered, stderr]).strip()


def fallback_binaries(target_dir: str, profile_dir: str) -> set[str]:
    """Shallow ``target/<profile>/`` top-level ELF scan (never deps/ or build/)."""
    top = os.path.join(target_dir, profile_dir)
    found: set[str] = set()
    try:
        entries = os.listdir(top)
    except OSError:
        return found
    for name in entries:
        full = os.path.join(top, name)
        if os.path.isfile(full) and is_elf_executable(full):
            found.add(full)
    return found


def classify_origin(source_file: str, clone_dir: str, cargo_home: str) -> str:
    """Tag a function's source path: in_repo / dependency / stdlib / other.

    rustc records paths relative to ``DW_AT_comp_dir`` (the clone dir for repo
    crates, ``/rustc/<hash>`` for the precompiled std), and the shared extractor
    preserves the DWARF path verbatim (it stays byte-identical to the C path,
    whose golden is frozen). So absolute prefixes are matched first, then a
    workspace-relative path that actually resolves to a file **under the clone
    dir** is ``in_repo`` (std's ``library/...`` and registry-relative paths do
    not resolve there and fall through to ``other``).
    """
    if not source_file:
        return "other"
    if source_file.startswith("/rustc/"):
        return "stdlib"
    if source_file.startswith(os.path.join(cargo_home, "registry", "src")):
        return "dependency"
    if os.path.isabs(source_file):
        if source_file.startswith((clone_dir, os.path.realpath(clone_dir))):
            return "in_repo"
        return "other"
    if os.path.isfile(os.path.join(clone_dir, source_file)):
        return "in_repo"
    if source_file.startswith("library/"):
        # cg_gcc emits the precompiled std's paths RELATIVE (library/core/...,
        # library/alloc/...) instead of llvm's /rustc/<hash>/library/... form.
        # Checked after the clone-dir resolution above, so a repo that really
        # contains such a file still wins as in_repo.
        return "stdlib"
    return "other"


def demangle_names(names: list[str]) -> list[str]:
    """Batch-demangle mangled symbols through one ``rustfilt`` subprocess.

    On any failure (rustfilt missing, non-zero exit, line-count mismatch) the mangled
    names are returned unchanged so extraction never loses a function entry.
    """
    if not names:
        return []
    try:
        proc = subprocess.run(
            ["rustfilt"],
            input="\n".join(names) + "\n",
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("rustfilt demangling failed: %s", e)
        return list(names)
    if proc.returncode != 0:
        logger.warning("rustfilt exited %d: %s", proc.returncode, proc.stderr)
        return list(names)
    out = proc.stdout.splitlines()
    if len(out) != len(names):
        logger.warning(
            "rustfilt returned %d lines for %d names; keeping mangled", len(out), len(names)
        )
        return list(names)
    return out


# --- prepared token ----------------------------------------------------------


@dataclass(frozen=True)
class RustPrepared:
    """``prepare`` -> ``build`` token: workspace member ids, or a failure reason."""

    member_ids: frozenset[str]
    failure: str | None = None


# --- strategy ----------------------------------------------------------------


class RustBuildStrategy(BuildStrategy):
    def __init__(self, settings: BuilderSettings) -> None:
        self.platform = "linux"
        self.compiler = "rustc"
        self.language = "rust"
        self.build_mode = settings.build_mode
        self.base_path = work_base_path(settings)
        self.toolchain = toolchain()
        self.adapter = make_adapter(settings.codegen_backend)
        self.codegen_backend = str(self.adapter.name)
        self.backend_caps: dict[str, object] = asdict(self.adapter.caps)
        self.cargo_home = settings.cargo_home
        self.build_timeout_s = settings.build_timeout_s

        # One `rustc -vV` probe: the version line fills the identity slots, the full
        # text is kept for the Toolchain metadata key.
        self.toolchain_vv = self._probe_toolchain()
        version_line = self.toolchain_vv.splitlines()[0] if self.toolchain_vv else ""
        self.compiler_version: str | None = version_line or None
        self.toolset_version: str | None = version_line or None

        self._artifacts: list[str] = []
        self._locked = False
        self._last_returncode = 0
        self._target_dir = ""
        self._profile = "release"

        try:
            perms = os.stat(self.base_path)
            self.output_dir_uid = perms.st_uid
            self.output_dir_gid = perms.st_gid
        except OSError:
            self.output_dir_uid = 0
            self.output_dir_gid = 0

    @property
    def cargo_locked(self) -> bool:
        """Whether the most recent build used ``--locked`` (for metadata)."""
        return self._locked

    def _probe_toolchain(self) -> str:
        try:
            result = run_command(f"rustc +{self.toolchain} -vV", timeout=60)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("rustc version probe failed: %s", e)
            return ""
        if result.returncode == 0:
            return result.stdout.decode(errors="ignore").strip()
        logger.warning(
            "rustc version probe returned %d: %s",
            result.returncode,
            result.stderr.decode(errors="ignore"),
        )
        return ""

    def _profile_name(self) -> str:
        return "dev" if self.build_mode == "Debug" else "release"

    def prepare(self, clone_dir: str, compiler_flag: str) -> object | None:
        if not os.path.isfile(os.path.join(clone_dir, "Cargo.toml")):
            return RustPrepared(frozenset(), failure="not a cargo project")
        return RustPrepared(self._workspace_members(clone_dir))

    def _workspace_members(self, clone_dir: str) -> frozenset[str]:
        cmd = f"cargo +{self.toolchain} metadata --format-version 1 --no-deps"
        result = run_command(cmd, timeout=self.build_timeout_s, cwd=clone_dir)
        if result.returncode != 0:
            logger.warning("cargo metadata failed: %s", result.stderr.decode(errors="ignore")[:500])
            return frozenset()
        try:
            data = json.loads(result.stdout.decode(errors="ignore"))
        except json.JSONDecodeError as e:
            logger.warning("cargo metadata JSON parse failed: %s", e)
            return frozenset()
        return frozenset(str(member) for member in data.get("workspace_members", []))

    def build(
        self, clone_dir: str, compiler_flag: str, prepared: object | None
    ) -> tuple[str, BuildStatus]:
        prep = prepared if isinstance(prepared, RustPrepared) else RustPrepared(frozenset())
        if prep.failure:
            return prep.failure, BuildStatus.FAILED

        self._profile = self._profile_name()
        self._target_dir = os.path.join(clone_dir, "target")
        env = cargo_env(
            build_mode=self.build_mode,
            compiler_flag=compiler_flag,
            adapter=self.adapter,
            cargo_home=self.cargo_home,
            target_dir=self._target_dir,
        )

        lock_exists = os.path.isfile(os.path.join(clone_dir, "Cargo.lock"))
        result = self._run_cargo(clone_dir, env, locked=lock_exists)
        used_locked = lock_exists
        if result.returncode != 0 and lock_exists:
            logger.info("locked cargo build failed, retrying without --locked")
            result = self._run_cargo(clone_dir, env, locked=False)
            used_locked = False

        self._locked = used_locked
        self._last_returncode = result.returncode
        stdout = result.stdout.decode(errors="ignore")
        self._artifacts = parse_cargo_artifacts(stdout, prep.member_ids)

        self.own_dir(os.path.dirname(clone_dir))
        status = BuildStatus.SUCCESS if result.returncode == 0 else BuildStatus.FAILED
        return rendered_diagnostics(stdout, result.stderr.decode(errors="ignore")), status

    def _run_cargo(self, clone_dir: str, env: dict[str, str], *, locked: bool) -> CommandResult:
        env_prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(env.items()))
        cmd = (
            f"{env_prefix} cargo +{self.toolchain} build --workspace --bins --examples "
            f"--profile {self._profile_name()} --message-format=json-render-diagnostics"
        )
        if locked:
            cmd += " --locked"
        return run_command(cmd, timeout=self.build_timeout_s, cwd=clone_dir)

    def find_binaries(self, path: str) -> set[str]:
        found = {p for p in self._artifacts if os.path.isfile(p)}
        if found:
            return found
        # Fallback ONLY when the JSON produced nothing but the build exited 0.
        if self._last_returncode == 0 and self._target_dir:
            return fallback_binaries(self._target_dir, _PROFILE_DIR[self._profile])
        return set()

    def debug_info(self, clone_dir: str, original_files: list[str]) -> list[dict[str, object]]:
        bin_files = {f for f in self.find_binaries(clone_dir) if f not in original_files}
        if not bin_files:
            return []
        items: list[dict[str, object]] = []
        for binfile in bin_files:
            try:
                # source_root=clone_dir lets the extractor read the embedded
                # source_code text for rustc's comp_dir-relative repo paths
                # (the C path passes no source_root, keeping its golden frozen).
                item = extract_dwarf_info(binfile, source_root=clone_dir)
            except Exception as e:
                logger.warning(
                    "DWARF extraction failed for %s: %s: %s", binfile, type(e).__name__, e
                )
                continue
            if item:
                self._postprocess_item(item, clone_dir)
                items.append(item)
        if not items:
            logger.info("No DWARF debug info found in any binary")
        return items

    def _postprocess_item(self, item: dict[str, object], clone_dir: str) -> None:
        """Add ``demangled_name`` + ``origin`` to each extracted function entry."""
        functions = item.get("functions")
        if not isinstance(functions, list):
            return
        names = [str(func.get("function_name", "")) for func in functions]
        demangled = demangle_names(names)
        for func, name in zip(functions, demangled, strict=False):
            func["demangled_name"] = name
            func["origin"] = classify_origin(
                str(func.get("source_file", "")), clone_dir, self.cargo_home
            )

    def own_dir(self, path: str) -> None:
        """Chown a produced tree to the base-path owner (container permissions fix-up)."""
        for root, dirs, files in os.walk(path):
            for name in (*dirs, *files):
                try:
                    os.chown(os.path.join(root, name), self.output_dir_uid, self.output_dir_gid)
                except OSError:
                    pass
        try:
            os.chown(path, self.output_dir_uid, self.output_dir_gid)
        except OSError:
            pass
