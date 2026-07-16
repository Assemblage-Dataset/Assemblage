"""Unit tests for the Rust build strategy (no network, no docker, no toolchain).

Covers the pure pieces (flag map, cargo profile env, cargo-JSON artifact parsing,
origin classification, rustflags/caps, the S3 prefix) and the strategy-level bits that
need construction (find_binaries fallback, rustfilt demangle post-processing,
registration fields). The rustc version probe is mocked at construction so no real
toolchain is required.
"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from assemblage.build import rust
from assemblage.build.commands import CommandResult
from assemblage.build.rust import (
    CraneliftAdapter,
    GccAdapter,
    LLVMAdapter,
    RustBuildStrategy,
    cargo_env,
    classify_origin,
    demangle_names,
    fallback_binaries,
    opt_level_for_flag,
    parse_cargo_artifacts,
)
from assemblage.enums import RustCodegenBackend
from assemblage.settings import BuilderSettings
from assemblage.storage.layout import rust_artifact_prefix

_RUST_ENV = {"compiler": "rustc", "language": "rust"}


def _make_strategy(**env: str) -> RustBuildStrategy:
    """Build a RustBuildStrategy with the rustc -vV probe mocked out."""
    probe = CommandResult(b"rustc 1.98.0-nightly (abc 2026-06-14)\nhost: x", b"", 0)
    with (
        patch.dict(os.environ, {**_RUST_ENV, **env}, clear=False),
        patch.object(rust, "run_command", return_value=probe),
    ):
        return RustBuildStrategy(BuilderSettings())


class TestOptLevelMap(unittest.TestCase):
    def test_known_flags(self):
        self.assertEqual(opt_level_for_flag("-O0"), "0")
        self.assertEqual(opt_level_for_flag("-O1"), "1")
        self.assertEqual(opt_level_for_flag("-O2"), "2")
        self.assertEqual(opt_level_for_flag("-O3"), "3")
        self.assertEqual(opt_level_for_flag("-Os"), "s")
        self.assertEqual(opt_level_for_flag("-Oz"), "z")

    def test_unknown_flag_raises(self):
        with self.assertRaises(ValueError):
            opt_level_for_flag("-Ofast")
        with self.assertRaises(ValueError):
            opt_level_for_flag("")


class TestCargoEnv(unittest.TestCase):
    def _env(self, build_mode: str, flag: str = "-O2") -> dict[str, str]:
        return cargo_env(
            build_mode=build_mode,
            compiler_flag=flag,
            adapter=LLVMAdapter(),
            cargo_home="/cargo",
            target_dir="/clone/target",
        )

    def test_relwithdebinfo_release_profile(self):
        env = self._env("RelWithDebInfo")
        self.assertEqual(env["CARGO_PROFILE_RELEASE_OPT_LEVEL"], "2")
        self.assertEqual(env["CARGO_PROFILE_RELEASE_DEBUG"], "2")
        self.assertEqual(env["CARGO_PROFILE_RELEASE_STRIP"], "none")
        self.assertNotIn("CARGO_PROFILE_DEV_OPT_LEVEL", env)

    def test_release_debug0_but_strip_none(self):
        env = self._env("Release")
        self.assertEqual(env["CARGO_PROFILE_RELEASE_DEBUG"], "0")
        self.assertEqual(env["CARGO_PROFILE_RELEASE_STRIP"], "none")

    def test_debug_dev_profile_no_strip_no_debug_override(self):
        env = self._env("Debug", "-O0")
        self.assertEqual(env["CARGO_PROFILE_DEV_OPT_LEVEL"], "0")
        self.assertNotIn("CARGO_PROFILE_RELEASE_STRIP", env)
        self.assertNotIn("CARGO_PROFILE_RELEASE_DEBUG", env)
        # DEBUG=0 must appear ONLY on the Release path.
        self.assertNotIn("CARGO_PROFILE_DEV_DEBUG", env)

    def test_fixed_env_and_rustflags(self):
        env = self._env("RelWithDebInfo")
        self.assertEqual(env["CARGO_INCREMENTAL"], "0")
        self.assertEqual(env["CARGO_HOME"], "/cargo")
        self.assertEqual(env["CARGO_TARGET_DIR"], "/clone/target")
        self.assertEqual(env["RUSTFLAGS"], "-Csymbol-mangling-version=v0")

    def test_cranelift_rustflags_in_env(self):
        env = cargo_env(
            build_mode="RelWithDebInfo",
            compiler_flag="-O0",
            adapter=CraneliftAdapter(),
            cargo_home="/cargo",
            target_dir="/t",
        )
        self.assertEqual(
            env["RUSTFLAGS"], "-Zcodegen-backend=cranelift -Csymbol-mangling-version=v0"
        )

    def test_unknown_flag_fails_fast(self):
        with self.assertRaises(ValueError):
            self._env("RelWithDebInfo", "-Owhat")


class TestAdapters(unittest.TestCase):
    def test_llvm_full_stable(self):
        a = LLVMAdapter()
        self.assertEqual(a.name, RustCodegenBackend.LLVM)
        self.assertEqual(a.rustflags(), [])
        self.assertTrue(a.caps.functions and a.caps.lines and a.caps.variables)
        self.assertEqual(a.caps.maturity, "stable")

    def test_cranelift_no_variables_experimental(self):
        a = CraneliftAdapter()
        self.assertEqual(a.rustflags(), ["-Zcodegen-backend=cranelift"])
        self.assertTrue(a.caps.functions and a.caps.lines)
        self.assertFalse(a.caps.variables)
        self.assertEqual(a.caps.maturity, "experimental")

    def test_gcc_all_claimed_experimental(self):
        a = GccAdapter()
        self.assertEqual(a.rustflags(), ["-Zcodegen-backend=gcc"])
        self.assertTrue(a.caps.functions and a.caps.lines and a.caps.variables)
        self.assertEqual(a.caps.maturity, "experimental")


# One workspace bin (kept), one build-script-build custom-build (dropped), one
# non-workspace dependency artifact (dropped by membership).
_CARGO_STREAM = "\n".join(
    [
        '{"reason":"compiler-artifact","package_id":"path+file:///w/app#0.1.0",'
        '"target":{"kind":["custom-build"],"name":"build-script-build"},'
        '"executable":null,"filenames":["/w/target/release/build/app-1/build-script-build"]}',
        '{"reason":"compiler-artifact","package_id":"registry+https://x#libc@0.2.0",'
        '"target":{"kind":["lib"],"name":"libc"},"executable":null,'
        '"filenames":["/w/target/release/deps/liblibc.rlib"]}',
        '{"reason":"compiler-artifact","package_id":"path+file:///w/app#0.1.0",'
        '"target":{"kind":["bin"],"name":"app"},'
        '"executable":"/w/target/release/app","filenames":["/w/target/release/app"]}',
        '{"reason":"build-finished","success":true}',
        "   ",
        "not json at all",
    ]
)


class TestParseCargoArtifacts(unittest.TestCase):
    def test_only_workspace_bin_survives(self):
        members = frozenset({"path+file:///w/app#0.1.0"})
        self.assertEqual(parse_cargo_artifacts(_CARGO_STREAM, members), ["/w/target/release/app"])

    def test_cdylib_so_captured(self):
        members = frozenset({"path+file:///w/lib#0.1.0"})
        stream = (
            '{"reason":"compiler-artifact","package_id":"path+file:///w/lib#0.1.0",'
            '"target":{"kind":["cdylib"],"name":"lib"},"executable":null,'
            '"filenames":["/w/target/release/liblib.so","/w/target/release/liblib.d"]}'
        )
        self.assertEqual(parse_cargo_artifacts(stream, members), ["/w/target/release/liblib.so"])

    def test_empty_members_disables_filter(self):
        # With no member set the dep membership filter is skipped; the custom-build
        # and lib kinds are still dropped, so only the bin survives.
        self.assertEqual(
            parse_cargo_artifacts(_CARGO_STREAM, frozenset()), ["/w/target/release/app"]
        )


class TestClassifyOrigin(unittest.TestCase):
    def test_in_repo(self):
        self.assertEqual(
            classify_origin("/tmp/projects/o/p/src/main.rs", "/tmp/projects/o/p", "/cargo"),
            "in_repo",
        )

    def test_dependency(self):
        self.assertEqual(
            classify_origin(
                "/cargo/registry/src/index.crates.io-abc/libc-0.2.0/src/lib.rs",
                "/tmp/projects/o/p",
                "/cargo",
            ),
            "dependency",
        )

    def test_stdlib(self):
        self.assertEqual(
            classify_origin("/rustc/3daae5e42/library/core/src/option.rs", "/clone", "/cargo"),
            "stdlib",
        )

    def test_other_and_empty(self):
        self.assertEqual(classify_origin("/usr/lib/foo.rs", "/clone", "/cargo"), "other")
        self.assertEqual(classify_origin("", "/clone", "/cargo"), "other")

    def test_relative_in_repo(self):
        # rustc records repo paths relative to DW_AT_comp_dir (the clone dir); a
        # relative path that resolves to a file under the clone dir is in_repo.
        with tempfile.TemporaryDirectory() as clone:
            os.makedirs(os.path.join(clone, "golden_lib", "src"))
            open(os.path.join(clone, "golden_lib", "src", "lib.rs"), "w").close()
            self.assertEqual(classify_origin("golden_lib/src/lib.rs", clone, "/cargo"), "in_repo")

    def test_relative_non_repo_is_other(self):
        # std's relative "library/..." path does not resolve under the clone dir.
        with tempfile.TemporaryDirectory() as clone:
            self.assertEqual(
                classify_origin("library/core/src/slice/mod.rs", clone, "/cargo"), "other"
            )


class TestDemangle(unittest.TestCase):
    def test_batch_demangle_mocked(self):
        names = ["_RNvCs123_3app4main", "_RNvCs123_3app6helper"]
        completed = SimpleNamespace(returncode=0, stdout="app::main\napp::helper\n", stderr="")
        with patch.object(rust.subprocess, "run", return_value=completed) as run:
            out = demangle_names(names)
        run.assert_called_once()
        self.assertEqual(out, ["app::main", "app::helper"])

    def test_line_count_mismatch_keeps_mangled(self):
        names = ["a", "b"]
        completed = SimpleNamespace(returncode=0, stdout="only-one-line\n", stderr="")
        with patch.object(rust.subprocess, "run", return_value=completed):
            self.assertEqual(demangle_names(names), ["a", "b"])

    def test_rustfilt_missing_keeps_mangled(self):
        with patch.object(rust.subprocess, "run", side_effect=OSError("no rustfilt")):
            self.assertEqual(demangle_names(["x"]), ["x"])

    def test_empty(self):
        self.assertEqual(demangle_names([]), [])


class TestPostprocessItem(unittest.TestCase):
    def test_adds_demangled_and_origin(self):
        strategy = _make_strategy()
        item: dict[str, object] = {
            "file": "app",
            "functions": [
                {
                    "function_name": "_RNvCs_3app4main",
                    "source_file": "/tmp/projects/o/p/src/main.rs",
                },
                {"function_name": "_RNvCs_4core6option", "source_file": "/rustc/abc/library/x.rs"},
            ],
        }
        completed = SimpleNamespace(returncode=0, stdout="p::main\ncore::option\n", stderr="")
        with patch.object(rust.subprocess, "run", return_value=completed):
            strategy._postprocess_item(item, "/tmp/projects/o/p")
        funcs = item["functions"]
        assert isinstance(funcs, list)
        self.assertEqual(funcs[0]["demangled_name"], "p::main")
        self.assertEqual(funcs[0]["origin"], "in_repo")
        self.assertEqual(funcs[1]["demangled_name"], "core::option")
        self.assertEqual(funcs[1]["origin"], "stdlib")


class TestFindBinariesFallback(unittest.TestCase):
    def test_fallback_when_json_empty_and_exit_zero(self):
        strategy = _make_strategy()
        with tempfile.TemporaryDirectory() as tmp:
            release = os.path.join(tmp, "target", "release")
            os.makedirs(release)
            binpath = os.path.join(release, "app")
            with open(binpath, "wb") as f:
                f.write(b"\x7fELF fake")
            strategy._artifacts = []
            strategy._last_returncode = 0
            strategy._target_dir = os.path.join(tmp, "target")
            strategy._profile = "release"
            with patch.object(rust, "is_elf_executable", return_value=True):
                self.assertEqual(strategy.find_binaries(tmp), {binpath})

    def test_no_fallback_on_nonzero_exit(self):
        strategy = _make_strategy()
        strategy._artifacts = []
        strategy._last_returncode = 1
        strategy._target_dir = "/nonexistent/target"
        strategy._profile = "release"
        self.assertEqual(strategy.find_binaries("/whatever"), set())

    def test_recorded_artifacts_preferred(self):
        strategy = _make_strategy()
        with tempfile.TemporaryDirectory() as tmp:
            real = os.path.join(tmp, "app")
            with open(real, "wb") as f:
                f.write(b"x")
            strategy._artifacts = [real, "/does/not/exist"]
            self.assertEqual(strategy.find_binaries(tmp), {real})


class TestFallbackBinariesHelper(unittest.TestCase):
    def test_missing_dir_returns_empty(self):
        self.assertEqual(fallback_binaries("/no/such", "release"), set())


class TestRustArtifactPrefix(unittest.TestCase):
    def test_format(self):
        self.assertEqual(
            rust_artifact_prefix("o", "p", "0123456789ab", "llvm", "RelWithDebInfo", "-O2"),
            "o_p_0123456789ab_rustc-llvm_RelWithDebInfo_-O2",
        )

    def test_cranelift_debug(self):
        self.assertEqual(
            rust_artifact_prefix("o", "p", "abc", "cranelift", "Debug", "-O0"),
            "o_p_abc_rustc-cranelift_Debug_-O0",
        )


class TestStrategyIdentity(unittest.TestCase):
    def test_identity_fields(self):
        strategy = _make_strategy()
        self.assertEqual(strategy.platform, "linux")
        self.assertEqual(strategy.compiler, "rustc")
        self.assertEqual(strategy.language, "rust")
        self.assertEqual(strategy.build_mode, "RelWithDebInfo")
        self.assertEqual(strategy.codegen_backend, "llvm")
        self.assertEqual(strategy.compiler_version, "rustc 1.98.0-nightly (abc 2026-06-14)")
        self.assertEqual(strategy.backend_caps["maturity"], "stable")

    def test_backend_selection(self):
        strategy = _make_strategy(CODEGEN_BACKEND="cranelift")
        self.assertEqual(strategy.codegen_backend, "cranelift")
        self.assertEqual(strategy.adapter.rustflags(), ["-Zcodegen-backend=cranelift"])

    def test_prepare_rejects_non_cargo(self):
        strategy = _make_strategy()
        with tempfile.TemporaryDirectory() as tmp:
            prepared = strategy.prepare(tmp, "-O2")
        assert isinstance(prepared, rust.RustPrepared)
        self.assertEqual(prepared.failure, "not a cargo project")

    def test_build_short_circuits_on_prepare_failure(self):
        from assemblage.enums import BuildStatus

        strategy = _make_strategy()
        prepared = rust.RustPrepared(frozenset(), failure="not a cargo project")
        out, status = strategy.build("/clone", "-O2", prepared)
        self.assertEqual(status, BuildStatus.FAILED)
        self.assertEqual(out, "not a cargo project")


class _CapturePublisher:
    def __init__(self, *_a: object, **_k: object) -> None:
        self.sent: list[str] = []

    def publish(self, _queue: object, body: str, **_kw: object) -> None:
        self.sent.append(body)

    def close(self) -> None:
        pass


class TestRegistrationFields(unittest.TestCase):
    def _app(self, env: dict[str, str], strategy: object):
        from assemblage.builder.app import BuilderApp

        with (
            patch.dict(os.environ, env, clear=False),
            patch("assemblage.builder.app.make_strategy", return_value=strategy),
        ):
            return BuilderApp(BuilderSettings())

    def test_rust_registration_carries_backend_and_mode(self):
        import json

        strategy = SimpleNamespace(build_mode="RelWithDebInfo")
        app = self._app(
            {"compiler": "rustc", "language": "rust", "CODEGEN_BACKEND": "cranelift"}, strategy
        )
        cap = _CapturePublisher()
        with patch("assemblage.builder.app.Publisher", return_value=cap):
            app._publish_registration("reply-queue")
        body = json.loads(cap.sent[0])
        self.assertEqual(body["language"], "rust")
        self.assertEqual(body["compiler"], "rustc")
        self.assertEqual(body["codegen_backend"], "cranelift")
        self.assertEqual(body["build_mode"], "RelWithDebInfo")

    def test_c_registration_leaves_backend_empty(self):
        import json

        strategy = SimpleNamespace(build_mode="RelWithDebInfo")
        app = self._app({"compiler": "gcc", "language": "c++"}, strategy)
        cap = _CapturePublisher()
        with patch("assemblage.builder.app.Publisher", return_value=cap):
            app._publish_registration("reply-queue")
        body = json.loads(cap.sent[0])
        self.assertEqual(body["codegen_backend"], "")
        self.assertEqual(body["build_mode"], "RelWithDebInfo")


if __name__ == "__main__":
    unittest.main()
