"""Golden-repo E2E gate for the Assemblage pipeline (C/C++ and Rust).

Runs as a one-shot container inside docker-compose.e2e.yml. Its exit code IS
the gate:

1. materialize the fixture repos into the shared /e2e volume as real git
   repositories with pinned commit metadata (deterministic sha);
2. wait for every builder to register (buildopt rows appear — this also proves
   the reconstructed alembic chain bootstraps a fresh database);
3. publish ONE scrape bundle carrying both hello-make (c++) and rust-golden
   (rust) as a bare JSON array to the `scrape` queue;
4. poll PostgreSQL until each project's b_status rows reach clone+build SUCCESS;
5. assert the exact MinIO object keys and metadata JSON content, including DWARF
   function/line/rva facts for both the C fixture and the Rust fixture;
6. normalize the metadata and diff it against the committed goldens
   (tests/fixtures/golden/); write a golden if it does not exist yet.

Behavior frozen by this gate: S3 key layout (C flat + Rust backend/mode prefix),
metadata key sets, Binary_info_list schema, DB status-name convention, scrape
wire format, registration->dispatch->build round trip, and LANGUAGE-AWARE
DISPATCH ISOLATION (a c++ repo never lands on a rust buildopt and vice versa).
"""

import io
import json
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import boto3
import pika
import psycopg2
from elftools.elf.elffile import ELFFile

FIXTURES = Path("/fixtures/repos")
E2E_REPOS = Path("/e2e")
GOLDEN_DIR = Path("/golden")
DEADLINE_S = int(os.environ.get("E2E_DEADLINE_S", "300"))
RUST_DEADLINE_S = int(os.environ.get("RUST_DEADLINE_S", "600"))
POLL_S = 5

REPO = "hello-make"
USER = "e2e"  # first path segment of file:///e2e/<repo>
EXPECTED_FUNCTIONS = {
    # function -> (file suffix, first body line) — see fixture mathlib.c
    "add": ("src/mathlib.c", 6),
    "mul3": ("src/mathlib.c", 11),
}

# --- Rust fixture ground truth (tests/fixtures/repos/rust-golden) ------------
RUST_REPO = "rust-golden"
RUST_FLAGS = ("-O0", "-O2")
RUST_BINARY = "golden_bin"
# Frozen fixture line numbers (see the fixture README). Body text is compared
# byte-for-byte after rstrip.
ADD_BODY_LINE, ADD_BODY_TEXT = 5, "    a + b"
MUL3_BODY_LINE, MUL3_BODY_TEXT = 7, "    x * 3"
ADD_SRC_SUFFIX = "golden_lib/src/lib.rs"
MUL3_SRC_SUFFIX = "golden_bin/src/main.rs"

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "e2e",
    "GIT_AUTHOR_EMAIL": "e2e@assemblage.invalid",
    "GIT_COMMITTER_NAME": "e2e",
    "GIT_COMMITTER_EMAIL": "e2e@assemblage.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00 +0000",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00 +0000",
}


def log(msg: str) -> None:
    print(f"[injector] {msg}", flush=True)


def fail(msg: str) -> None:
    log(f"FAIL: {msg}")


def sh(cmd: list[str], cwd: Path) -> str:
    return subprocess.run(
        cmd, cwd=cwd, env=GIT_ENV, check=True, capture_output=True, text=True
    ).stdout.strip()


def prepare_repo(name: str) -> str:
    dest = E2E_REPOS / name
    if dest.exists():
        subprocess.run(["rm", "-rf", str(dest)], check=True)
    subprocess.run(["cp", "-r", str(FIXTURES / name), str(dest)], check=True)
    sh(["git", "init", "-q", "-b", "master"], dest)
    sh(["git", "add", "-A"], dest)
    sh(["git", "commit", "-q", "-m", "fixture"], dest)
    sha = sh(["git", "rev-parse", "HEAD"], dest)
    log(f"prepared {name} at {dest} sha={sha}")
    return sha


def pg_conn():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "5432")),
        dbname=os.environ["POSTGRES_DATABASE"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        connect_timeout=5,
    )


def wait_for(desc: str, fn, deadline_s: int = DEADLINE_S):
    start = time.time()
    while time.time() - start < deadline_s:
        try:
            result = fn()
            if result:
                log(f"{desc}: ok after {round(time.time() - start, 1)}s")
                return result
        except Exception as exc:  # polling: any failure is a retry
            log(f"{desc}: waiting ({type(exc).__name__}: {exc})")
        time.sleep(POLL_S)
    raise TimeoutError(f"deadline waiting for {desc}")


def all_buildopts_registered() -> bool:
    """The gcc opt and both rust (llvm) opts must exist before the scrape insert
    (b_status fan-out only covers buildopts that already exist)."""
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT compiler_name, compiler_flag, codegen_backend FROM buildopt")
        rows = {(r[0], r[1], r[2] or "") for r in cur.fetchall()}
    want = {
        ("gcc", "-O0", ""),
        ("rustc", "-O0", "llvm"),
        ("rustc", "-O2", "llvm"),
    }
    return want.issubset(rows)


def publish_bundle(c_sha: str, rust_sha: str) -> None:
    """One bare-JSON-array bundle carrying the c++ AND the rust fixture."""
    repos = [
        {
            "name": REPO,
            "url": f"file:///e2e/{REPO}",
            "language": "c++",
            "owner_id": 1,
            "description": "e2e c fixture",
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
            "size": 1,
            "build_system": "make",
            "branch": "master",
            "commit_hexsha": c_sha,
            "license": "MIT License",
        },
        {
            "name": RUST_REPO,
            "url": f"file:///e2e/{RUST_REPO}",
            "language": "rust",
            "owner_id": 1,
            "description": "e2e rust fixture",
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
            "size": 1,
            "build_system": "cargo",
            "branch": "master",
            "commit_hexsha": rust_sha,
            "license": "mit",
        },
    ]
    params = pika.ConnectionParameters(
        host=os.environ.get("MQ_HOST", "rabbitmq"),
        credentials=pika.PlainCredentials("guest", "guest"),
    )
    with pika.BlockingConnection(params) as conn:
        chan = conn.channel()
        chan.queue_declare(queue="scrape", durable=True)
        chan.basic_publish(
            exchange="",
            routing_key="scrape",
            body=json.dumps(repos).encode(),  # bare JSON array — frozen wire format
            properties=pika.BasicProperties(delivery_mode=2),
        )
    log("published 2-repo scrape bundle (hello-make c++, rust-golden rust)")


def status_success() -> dict | None:
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT s.id, s.clone_status, s.build_status, s.commit_hexsha, s.build_msg
               FROM b_status s JOIN projects p ON p.id = s.repo_id
               WHERE p.name = %s""",
            (REPO,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        status_id, clone_st, build_st, sha, build_msg = row
        log(f"b_status[{REPO}]: clone={clone_st} build={build_st}")
        if build_st == "FAILED":
            raise SystemExit(f"FAIL: {REPO} build FAILED: {build_msg[:2000]}")
        if clone_st == "SUCCESS" and build_st == "SUCCESS":
            return {"status_id": status_id, "sha": sha}
        return None


def rust_rows() -> list[dict]:
    """b_status rows for rust-golden joined to their buildopt identity."""
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT s.id, s.clone_status, s.build_status, s.commit_hexsha, s.build_msg,
                      o.compiler_name, o.compiler_flag, o.codegen_backend, o.language
               FROM b_status s
               JOIN projects p ON p.id = s.repo_id
               JOIN buildopt o ON o.id = s.build_opt_id
               WHERE p.name = %s""",
            (RUST_REPO,),
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def rust_builds_settled() -> list[dict] | None:
    rows = rust_rows()
    flags = {r["compiler_flag"]: r["build_status"] for r in rows}
    log(f"b_status[{RUST_REPO}]: {flags}")
    for r in rows:
        if r["build_status"] == "FAILED":
            raise SystemExit(
                f"FAIL: rust {r['compiler_flag']} build FAILED: {r['build_msg'][:2000]}"
            )
    if len(rows) >= 2 and all(
        r["clone_status"] == "SUCCESS" and r["build_status"] == "SUCCESS" for r in rows
    ):
        return rows
    return None


def s3_client():
    return boto3.client(
        "s3",
        endpoint_url=f"http://{os.environ['S3_HOST']}:9000",
        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
        region_name="us-east-1",
    )


# --- normalization / goldens -------------------------------------------------


def normalize_metadata(meta: dict) -> dict:
    """Stable form for the C golden: volatile values masked, lists sorted."""
    norm = dict(meta)
    norm["Compiler_version"] = "<masked>"
    norm["Commit"] = "<sha>"
    norm["Build_time"] = "<masked>" if "Build_time" in norm else norm.get("Build_time")
    bil = norm.get("Binary_info_list") or []
    for entry in bil:
        entry["file"] = entry["file"].split("/")[-1]
        entry["functions"] = sorted(
            entry.get("functions", []), key=lambda f: f.get("function_name", "")
        )
        for fn in entry["functions"]:
            # RVAs shift with toolchain point releases; presence+shape is golden,
            # exact addresses are asserted structurally instead.
            for rng in fn.get("function_info", []):
                rng["rva_start"] = "<rva>"
                rng["rva_end"] = "<rva>"
            for line in fn.get("lines", []):
                line["rva"] = "<rva>"
                line["length"] = "<len>"
    norm["Binary_info_list"] = sorted(bil, key=lambda e: e["file"])
    return norm


def normalize_rust_metadata(meta: dict) -> dict:
    """Stable form for the Rust golden.

    Masks the volatile fields (rva numerics, Compiler_version/Toolchain, Commit,
    size) and — because a Rust binary statically links thousands of stdlib DWARF
    subprograms whose exact set/addresses are noise — keeps ONLY the in_repo
    functions (the fixture's own code), which is what the gate is really pinning:
    names, demangled names, origin, source files, line numbers and source text.
    """
    norm = dict(meta)
    norm["Compiler_version"] = "<masked>"
    norm["Toolchain"] = "<masked>"
    norm["Commit"] = "<sha>"
    if "Build_time" in norm:
        norm["Build_time"] = "<masked>"
    bil = norm.get("Binary_info_list") or []
    out_entries = []
    for entry in bil:
        funcs = [f for f in entry.get("functions", []) if f.get("origin") == "in_repo"]
        funcs = sorted(funcs, key=lambda f: f.get("function_name", ""))
        for fn in funcs:
            for rng in fn.get("function_info", []):
                rng["rva_start"] = "<rva>"
                rng["rva_end"] = "<rva>"
            for line in fn.get("lines", []):
                line["rva"] = "<rva>"
                line["length"] = "<len>"
        out_entries.append({"file": entry["file"].split("/")[-1], "functions": funcs})
    norm["Binary_info_list"] = sorted(out_entries, key=lambda e: e["file"])
    return norm


def golden_check(name: str, normalized: dict) -> bool:
    """Diff against a committed golden, or write it the first time. True == ok."""
    path = GOLDEN_DIR / name
    payload = json.dumps(normalized, indent=2, sort_keys=True) + "\n"
    if path.exists():
        if path.read_text() != payload:
            rejected = GOLDEN_DIR / (name.replace(".json", ".rejected.json"))
            rejected.write_text(payload)
            fail(f"normalized metadata differs from golden {name}; see {rejected.name}")
            return False
        log(f"golden {name}: match")
        return True
    path.write_text(payload)
    log(f"golden captured: WROTE initial {name} (commit it)")
    return True


# --- ELF helpers (assertion f) -----------------------------------------------


def elf_base_and_symtab(data: bytes) -> tuple[int, dict[str, int]]:
    """(lowest PT_LOAD vaddr, {symbol name: st_value}) for a downloaded binary."""
    elf = ELFFile(io.BytesIO(data))
    base = None
    for seg in elf.iter_segments():
        if seg["p_type"] == "PT_LOAD":
            v = seg["p_vaddr"]
            if base is None or v < base:
                base = v
    syms: dict[str, int] = {}
    symtab = elf.get_section_by_name(".symtab")
    if symtab is not None:
        for sym in symtab.iter_symbols():
            if sym["st_value"] and sym.name:
                syms[sym.name] = sym["st_value"]
    return (base or 0), syms


def rva_int(hexstr: str) -> int:
    return int(hexstr, 16)


def func_ranges(fn: dict) -> list[tuple[int, int]]:
    return [(rva_int(r["rva_start"]), rva_int(r["rva_end"])) for r in fn.get("function_info", [])]


# --- Rust assertions ---------------------------------------------------------


def find_by_demangled(funcs: list[dict], needle: str) -> list[dict]:
    return [f for f in funcs if needle in f.get("demangled_name", "")]


def assert_rust(s3, sha12: str) -> bool:
    ok = True
    metas: dict[str, dict] = {}

    fixture_lib = (FIXTURES / RUST_REPO / "golden_lib" / "src" / "lib.rs").read_bytes()

    for flag in RUST_FLAGS:
        prefix = f"{USER}_{RUST_REPO}_{sha12}_rustc-llvm_RelWithDebInfo_{flag}"

        # (b) exact S3 keys ---------------------------------------------------
        expected = [
            ("artifacts", f"{prefix}/{RUST_BINARY}"),
            ("artifacts", f"{prefix}/assemblage_meta.json"),
            ("project-archive", f"{USER}/{RUST_REPO}/{sha12}.tar.gz"),
            ("project-archive", f"{USER}/{RUST_REPO}/latest.txt"),
        ]
        for bucket, key in expected:
            try:
                s3.head_object(Bucket=bucket, Key=key)
                log(f"s3 ok: {bucket}/{key}")
            except Exception:
                fail(f"missing s3 object {bucket}/{key}")
                ok = False

        pointer = (
            s3.get_object(Bucket="project-archive", Key=f"{USER}/{RUST_REPO}/latest.txt")["Body"]
            .read()
            .decode()
            .strip()
        )
        if pointer != sha12:
            fail(f"rust latest.txt {pointer!r} != {sha12!r}")
            ok = False

        # archive contains the fixture source, byte-for-byte -----------------
        tar_bytes = s3.get_object(
            Bucket="project-archive", Key=f"{USER}/{RUST_REPO}/{sha12}.tar.gz"
        )["Body"].read()
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tf:
            member = next(
                (m for m in tf.getmembers() if m.name.endswith("golden_lib/src/lib.rs")), None
            )
            if member is None:
                fail(f"{flag} archive missing golden_lib/src/lib.rs")
                ok = False
            else:
                extracted = tf.extractfile(member).read()
                if extracted != fixture_lib:
                    fail(f"{flag} archived lib.rs bytes != fixture bytes")
                    ok = False
                else:
                    log(f"{flag} archive: golden_lib/src/lib.rs matches fixture")

        # (c) metadata --------------------------------------------------------
        meta = json.loads(
            s3.get_object(Bucket="artifacts", Key=f"{prefix}/assemblage_meta.json")["Body"].read()
        )
        metas[flag] = meta
        c_era = {
            "Platform",
            "Build_mode",
            "Compiler",
            "Compiler_version",
            "URL",
            "Commit",
            "Optimization",
            "Pushed_at",
            "Binary_info_list",
        }
        missing = c_era - meta.keys()
        if missing:
            fail(f"{flag} metadata missing C-era keys {missing}")
            ok = False
        checks = {
            "Platform": "linux",
            "Compiler": "rustc",
            "Optimization": flag,
            "Build_mode": "RelWithDebInfo",
            "URL": f"file:///e2e/{RUST_REPO}",
            "Commit": sha12,
            "language": "rust",
            "Codegen_backend": "llvm",
            "Mangling": "v0",
        }
        for key, want in checks.items():
            if meta.get(key) != want:
                fail(f"{flag} metadata[{key}] = {meta.get(key)!r}, expected {want!r}")
                ok = False
        if not (isinstance(meta.get("Toolchain"), str) and meta["Toolchain"].strip()):
            fail(f"{flag} metadata Toolchain empty/non-string")
            ok = False
        if not isinstance(meta.get("Backend_caps"), dict):
            fail(f"{flag} metadata Backend_caps not a dict")
            ok = False
        if not isinstance(meta.get("Cargo_locked"), bool):
            fail(f"{flag} metadata Cargo_locked not a bool")
            ok = False

    # per-opt DWARF assertions ------------------------------------------------
    ok = assert_rust_functions_o0(s3, sha12, metas["-O0"]) and ok
    ok = assert_rust_inlining_o2(metas["-O2"]) and ok

    # (h) goldens -------------------------------------------------------------
    for flag in RUST_FLAGS:
        if not golden_check(
            f"{RUST_REPO}.{flag}.metadata.norm.json", normalize_rust_metadata(metas[flag])
        ):
            ok = False
    return ok


def _bin_functions(meta: dict) -> list[dict]:
    entries = [e for e in meta["Binary_info_list"] if e["file"].split("/")[-1] == RUST_BINARY]
    return entries[0]["functions"] if entries else []


def assert_rust_functions_o0(s3, sha12: str, meta: dict) -> bool:
    ok = True
    funcs = _bin_functions(meta)
    if not funcs:
        fail("no golden_bin Binary_info_list entry in -O0 metadata")
        return False

    def one(needle: str) -> dict | None:
        hits = find_by_demangled(funcs, needle)
        if not hits:
            fail(f"-O0 no function demangling to contain {needle!r}")
            return None
        return hits[0]

    add = one("golden_lib::add")
    mul3 = one("golden_bin::mul3")
    mix = one("golden_lib::mix")

    # (d) two distinct pair_sum monomorphizations
    pair = find_by_demangled(funcs, "pair_sum")
    mangled = {p["function_name"] for p in pair}
    if len(mangled) < 2:
        fail(f"-O0 expected >=2 distinct pair_sum mangled names, got {sorted(mangled)}")
        ok = False
    else:
        log(f"-O0 pair_sum instantiations: {sorted(p['demangled_name'] for p in pair)}")

    # closure: the fixture's own closure, which v0 demangles to
    # 'golden_bin::main::{closure#N}' (NOT the pre-v0 '{{closure}}' shape).
    # Filter to the fixture's closure so a stdlib closure (e.g.
    # std::rt::lang_start::{closure#0}) is never mistaken for it.
    closures = [
        f
        for f in funcs
        if "{closure#" in f.get("demangled_name", "")
        and "golden_bin::main" in f.get("demangled_name", "")
    ]
    if not closures:
        fail("-O0 no 'golden_bin::main::{closure#' entry")
        ok = False

    # (d) origin/source/mangling for each fixture anchor
    anchors = [f for f in (add, mul3, mix, *pair, *closures[:1]) if f is not None]
    for fn in anchors:
        if fn.get("origin") != "in_repo":
            fail(f"-O0 {fn['demangled_name']} origin={fn.get('origin')} != in_repo")
            ok = False
        if not fn["function_name"].startswith("_R"):
            fail(f"-O0 {fn['demangled_name']} mangled {fn['function_name']} not v0 (_R)")
            ok = False
    for fn, suffix in ((add, ADD_SRC_SUFFIX), (mul3, MUL3_SRC_SUFFIX), (mix, ADD_SRC_SUFFIX)):
        if fn is not None and not fn["source_file"].endswith(suffix):
            fail(f"-O0 {fn['demangled_name']} source_file {fn['source_file']} !endswith {suffix}")
            ok = False
    if ok:
        log("-O0 functions ok (add/mul3/mix, 2x pair_sum, closure: in_repo, _R, sources)")

    # (e) exact line + source text for add and mul3
    for fn, line_no, text in (
        (add, ADD_BODY_LINE, ADD_BODY_TEXT),
        (mul3, MUL3_BODY_LINE, MUL3_BODY_TEXT),
    ):
        if fn is None:
            ok = False
            continue
        matched = [ln for ln in fn.get("lines", []) if ln["line_number"] == line_no]
        if not matched:
            fail(f"-O0 {fn['demangled_name']} missing body line {line_no}")
            ok = False
            continue
        if not any(ln["source_code"].rstrip() == text for ln in matched):
            got = [ln["source_code"] for ln in matched]
            fail(f"-O0 {fn['demangled_name']} line {line_no} source_code {got} != {text!r}")
            ok = False
        else:
            log(f"-O0 line ground truth: {fn['demangled_name']} line {line_no} == {text!r}")

    # (f) rva cross-check against ELF .symtab
    data = s3.get_object(
        Bucket="artifacts",
        Key=f"{USER}_{RUST_REPO}_{sha12}_rustc-llvm_RelWithDebInfo_-O0/{RUST_BINARY}",
    )["Body"].read()
    base, syms = elf_base_and_symtab(data)
    for fn in (add, mul3):
        if fn is None:
            ok = False
            continue
        ranges = func_ranges(fn)
        if not ranges or not all(s < e for s, e in ranges):
            fail(f"-O0 {fn['demangled_name']} bad rva ranges {ranges}")
            ok = False
            continue
        st = syms.get(fn["function_name"])
        if st is None:
            fail(f"-O0 {fn['demangled_name']} symbol {fn['function_name']} absent from .symtab")
            ok = False
            continue
        sym_rva = st - base
        if not any(s <= sym_rva < e for s, e in ranges):
            fail(
                f"-O0 {fn['demangled_name']} symtab rva {hex(sym_rva)} not in DWARF ranges {ranges}"
            )
            ok = False
        else:
            log(
                f"-O0 rva cross-check: {fn['demangled_name']} symtab {hex(sym_rva)} "
                f"in {[(hex(s), hex(e)) for s, e in ranges]}"
            )
    return ok


def assert_rust_inlining_o2(meta: dict) -> bool:
    """At -O2 mul3 is inlined into main: the only mul3 entry carrying code is a
    DW_TAG_inlined_subroutine whose RVA range is nested inside main's range (its
    standalone .symtab symbol is gone — verified structurally here)."""
    ok = True
    funcs = _bin_functions(meta)
    mains = find_by_demangled(funcs, "golden_bin::main")
    mains = [m for m in mains if "closure" not in m.get("demangled_name", "")]
    mul3s = find_by_demangled(funcs, "golden_bin::mul3")
    if not mains:
        fail("-O2 no golden_bin::main entry")
        return False
    if not mul3s:
        fail("-O2 no golden_bin::mul3 entry (expected inlined subroutine)")
        return False
    main_ranges = func_ranges(mains[0])
    m_start = min(s for s, _ in main_ranges)
    m_end = max(e for _, e in main_ranges)
    mul3_ranges = [r for f in mul3s for r in func_ranges(f)]
    if not mul3_ranges:
        fail("-O2 mul3 entry has no rva ranges")
        return False
    for s, e in mul3_ranges:
        if not (m_start <= s and e <= m_end):
            fail(
                f"-O2 mul3 range {(hex(s), hex(e))} not nested in main {(hex(m_start), hex(m_end))}"
            )
            ok = False
    if ok:
        log(
            f"-O2 inlining: mul3 ranges {[(hex(s), hex(e)) for s, e in mul3_ranges]} "
            f"nested in main {(hex(m_start), hex(m_end))}"
        )
    return ok


# --- C assertions (unchanged behaviour) --------------------------------------


def assert_c(s3, sha12: str, status_id: int) -> bool:
    ok = True
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT file_name FROM binaries WHERE status_id = %s", (status_id,))
        files = [r[0] for r in cur.fetchall()]
    log(f"binaries rows: {files}")
    if not any(f.endswith("hello") for f in files):
        fail("no 'hello' binaries row")
        ok = False

    prefix = f"{USER}_{REPO}_{sha12}_gcc_-O0"
    expected_keys = {
        ("artifacts", f"{prefix}/assemblage_meta.json"),
        ("artifacts", f"{prefix}/hello"),
        ("project-archive", f"{USER}/{REPO}/{sha12}.tar.gz"),
        ("project-archive", f"{USER}/{REPO}/latest.txt"),
    }
    for bucket, key in sorted(expected_keys):
        try:
            s3.head_object(Bucket=bucket, Key=key)
            log(f"s3 ok: {bucket}/{key}")
        except Exception:
            fail(f"missing s3 object {bucket}/{key}")
            ok = False

    meta = json.loads(
        s3.get_object(Bucket="artifacts", Key=f"{prefix}/assemblage_meta.json")["Body"].read()
    )
    required_keys = {
        "Platform",
        "Build_mode",
        "Compiler",
        "Compiler_version",
        "URL",
        "Commit",
        "Optimization",
        "Pushed_at",
        "Binary_info_list",
    }
    missing = required_keys - meta.keys()
    if missing:
        fail(f"metadata missing keys {missing}; has {sorted(meta.keys())}")
        return False
    checks = {
        "Platform": "linux",
        "Compiler": "gcc",
        "Optimization": "-O0",
        "Build_mode": "RelWithDebInfo",
        "URL": f"file:///e2e/{REPO}",
        "Commit": sha12,
    }
    for key, expected in checks.items():
        if meta[key] != expected:
            fail(f"metadata[{key}] = {meta[key]!r}, expected {expected!r}")
            ok = False

    hello_entries = [e for e in meta["Binary_info_list"] if e["file"].split("/")[-1] == "hello"]
    if not hello_entries:
        fail(
            f"no Binary_info_list entry for 'hello': {[e['file'] for e in meta['Binary_info_list']]}"
        )
        return False
    functions = {f["function_name"]: f for f in hello_entries[0]["functions"]}
    for fname, (src_suffix, body_line) in EXPECTED_FUNCTIONS.items():
        fn = functions.get(fname)
        if fn is None:
            fail(f"function {fname} not extracted; got {sorted(functions)}")
            ok = False
            continue
        if not fn["source_file"].endswith(src_suffix):
            fail(f"{fname} source_file {fn['source_file']} !endswith {src_suffix}")
            ok = False
        if not fn.get("function_info"):
            fail(f"{fname} has no RVA ranges")
            ok = False
        line_numbers = {ln["line_number"] for ln in fn.get("lines", [])}
        if body_line not in line_numbers:
            fail(f"{fname} lines {sorted(line_numbers)} missing body line {body_line}")
            ok = False
    log("C DWARF facts ok (add/mul3: source file, RVA ranges, body lines)")

    if not golden_check(f"{REPO}.metadata.norm.json", normalize_metadata(meta)):
        ok = False
    return ok


def assert_dispatch_isolation() -> bool:
    """The frozen language-aware-dispatch invariant: hello-make (c++) lands on
    exactly the gcc opt, rust-golden (rust) on exactly the two rust opts."""
    ok = True
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT p.name, o.compiler_name, o.compiler_flag, COALESCE(o.codegen_backend, ''),
                      s.clone_status, s.build_status
               FROM b_status s
               JOIN projects p ON p.id = s.repo_id
               JOIN buildopt o ON o.id = s.build_opt_id"""
        )
        rows = cur.fetchall()
    by_project: dict[str, set] = {}
    statuses: dict[str, set] = {}
    for name, comp, flag, backend, clone_st, build_st in rows:
        by_project.setdefault(name, set()).add((comp, flag, backend))
        statuses.setdefault(name, set()).add((clone_st, build_st))
    want = {
        REPO: {("gcc", "-O0", "")},
        RUST_REPO: {("rustc", "-O0", "llvm"), ("rustc", "-O2", "llvm")},
    }
    for name, expected in want.items():
        got = by_project.get(name, set())
        if got != expected:
            fail(f"dispatch isolation: {name} opts {sorted(got)} != {sorted(expected)}")
            ok = False
        if statuses.get(name) != {("SUCCESS", "SUCCESS")}:
            fail(f"dispatch isolation: {name} statuses {statuses.get(name)} != all SUCCESS")
            ok = False
    if ok:
        log("dispatch isolation ok: c++ -> gcc opt only; rust -> two rust opts only")
    return ok


def main() -> int:
    c_sha = prepare_repo(REPO)
    rust_sha = prepare_repo(RUST_REPO)
    prepare_repo("hello-cmake")  # available for the nightly matrix
    # Frozen behavior: dispatch does not forward the scrape-time sha, so the
    # builder re-derives the commit via `git rev-parse --short=12` and keys the
    # DB row, both S3 buckets and the metadata by the 12-char prefix.
    c_sha12 = c_sha[:12]
    rust_sha12 = rust_sha[:12]

    wait_for("all builders registered (gcc + 2 rust buildopts)", all_buildopts_registered)
    publish_bundle(c_sha, rust_sha)

    c_result = wait_for("hello-make clone+build SUCCESS", status_success)
    if c_result["sha"] and c_result["sha"] != c_sha12:
        fail(f"hello-make b_status commit {c_result['sha']} != fixture prefix {c_sha12}")
        return 1
    rust_settled = wait_for(
        "rust-golden -O0 & -O2 clone+build SUCCESS", rust_builds_settled, RUST_DEADLINE_S
    )
    for r in rust_settled:
        if r["commit_hexsha"] and r["commit_hexsha"] != rust_sha12:
            fail(f"rust b_status commit {r['commit_hexsha']} != fixture prefix {rust_sha12}")
            return 1

    s3 = s3_client()
    ok = True
    ok = assert_dispatch_isolation() and ok
    ok = assert_c(s3, c_sha12, c_result["status_id"]) and ok
    ok = assert_rust(s3, rust_sha12) and ok

    if not ok:
        log("E2E GATE FAILED")
        return 1
    log("E2E GATE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
