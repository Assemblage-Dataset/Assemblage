"""Golden-repo E2E gate for the Assemblage pipeline.

Runs as a one-shot container inside docker-compose.e2e.yml. Its exit code IS
the gate:

1. materialize the fixture repos into the shared /e2e volume as real git
   repositories with pinned commit metadata (deterministic sha);
2. wait for the builder to register (buildopt row appears — this also proves
   the reconstructed alembic chain bootstraps a fresh database);
3. publish a one-repo scrape bundle (bare JSON array) to the `scrape` queue;
4. poll PostgreSQL until the b_status row reaches clone SUCCESS + build
   SUCCESS (names, per DB convention) — deadline-bounded;
5. assert the exact MinIO object keys and the metadata JSON content,
   including DWARF function/line facts for the fixture's add()/mul3();
6. normalize the metadata and diff it against the committed golden
   (tests/fixtures/golden/); write the golden if it does not exist yet.

Behavior frozen by this gate: S3 key layout, metadata key set,
Binary_info_list schema, DB status-name convention, scrape wire format,
registration->dispatch->build round trip.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import boto3
import pika
import psycopg2

FIXTURES = Path("/fixtures/repos")
E2E_REPOS = Path("/e2e")
GOLDEN_DIR = Path("/golden")
DEADLINE_S = int(os.environ.get("E2E_DEADLINE_S", "300"))
POLL_S = 5

REPO = "hello-make"
USER = "e2e"  # first path segment of file:///e2e/<repo>
EXPECTED_FUNCTIONS = {
    # function -> (file suffix, first body line) — see fixture mathlib.c
    "add": ("src/mathlib.c", 6),
    "mul3": ("src/mathlib.c", 11),
}

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


def buildopt_registered() -> bool:
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM buildopt WHERE compiler_name='gcc' AND compiler_flag='-O0'")
        return cur.fetchone() is not None


def publish_bundle(sha: str) -> None:
    repo_msg = {
        "name": REPO,
        "url": f"file:///e2e/{REPO}",
        "language": "c++",
        "owner_id": 1,
        "description": "e2e fixture",
        "created_at": "2026-01-01 00:00:00",
        "updated_at": "2026-01-01 00:00:00",
        "size": 1,
        "build_system": "make",
        "branch": "master",
        "commit_hexsha": sha,
        "license": "MIT License",
    }
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
            body=json.dumps([repo_msg]).encode(),  # bare JSON array — frozen wire format
            properties=pika.BasicProperties(delivery_mode=2),
        )
    log("published 1-repo scrape bundle")


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
        log(f"b_status: clone={clone_st} build={build_st}")
        if build_st == "FAILED":
            raise SystemExit(f"FAIL: build FAILED: {build_msg[:2000]}")
        if clone_st == "SUCCESS" and build_st == "SUCCESS":
            return {"status_id": status_id, "sha": sha}
        return None


def s3_client():
    return boto3.client(
        "s3",
        endpoint_url=f"http://{os.environ['S3_HOST']}:9000",
        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
        region_name="us-east-1",
    )


def normalize_metadata(meta: dict) -> dict:
    """Stable form for golden comparison: volatile values masked, lists sorted."""
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


def main() -> int:
    sha = prepare_repo(REPO)
    prepare_repo("hello-cmake")  # available for the nightly matrix
    # Frozen behavior: dispatch does not forward the scrape-time sha (the
    # b_status row starts empty), so the builder re-derives the commit via
    # `git rev-parse --short=12` and keys the DB row, both S3 buckets and
    # the metadata by the 12-char prefix.
    sha12 = sha[:12]

    wait_for("builder registration (buildopt row)", buildopt_registered)
    publish_bundle(sha)
    result = wait_for("clone+build SUCCESS", status_success)

    if result["sha"] and result["sha"] != sha12:
        log(f"FAIL: b_status commit {result['sha']} != fixture prefix {sha12}")
        return 1

    # --- binaries row -------------------------------------------------------
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT file_name FROM binaries WHERE status_id = %s", (result["status_id"],))
        files = [r[0] for r in cur.fetchall()]
    log(f"binaries rows: {files}")
    if not any(f.endswith("hello") for f in files):
        log("FAIL: no 'hello' binaries row")
        return 1

    # --- exact S3 keys ------------------------------------------------------
    s3 = s3_client()
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
            log(f"FAIL: missing s3 object {bucket}/{key}")
            return 1

    # --- metadata content ---------------------------------------------------
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
        log(f"FAIL: metadata missing keys {missing}; has {sorted(meta.keys())}")
        return 1
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
            log(f"FAIL: metadata[{key}] = {meta[key]!r}, expected {expected!r}")
            return 1

    hello_entries = [e for e in meta["Binary_info_list"] if e["file"].split("/")[-1] == "hello"]
    if not hello_entries:
        log(
            f"FAIL: no Binary_info_list entry for 'hello': "
            f"{[e['file'] for e in meta['Binary_info_list']]}"
        )
        return 1
    functions = {f["function_name"]: f for f in hello_entries[0]["functions"]}
    for fname, (src_suffix, body_line) in EXPECTED_FUNCTIONS.items():
        fn = functions.get(fname)
        if fn is None:
            log(f"FAIL: function {fname} not extracted; got {sorted(functions)}")
            return 1
        if not fn["source_file"].endswith(src_suffix):
            log(f"FAIL: {fname} source_file {fn['source_file']} !endswith {src_suffix}")
            return 1
        if not fn.get("function_info"):
            log(f"FAIL: {fname} has no RVA ranges")
            return 1
        line_numbers = {ln["line_number"] for ln in fn.get("lines", [])}
        if body_line not in line_numbers:
            log(f"FAIL: {fname} lines {sorted(line_numbers)} missing body line {body_line}")
            return 1
    log("DWARF facts ok (add/mul3: source file, RVA ranges, body lines)")

    # --- golden diff --------------------------------------------------------
    golden_path = GOLDEN_DIR / f"{REPO}.metadata.norm.json"
    normalized = json.dumps(normalize_metadata(meta), indent=2, sort_keys=True) + "\n"
    if golden_path.exists():
        if golden_path.read_text() != normalized:
            candidate = GOLDEN_DIR / f"{REPO}.metadata.norm.rejected.json"
            candidate.write_text(normalized)
            log(f"FAIL: normalized metadata differs from golden; see {candidate.name}")
            return 1
        log("golden metadata: match")
    else:
        golden_path.write_text(normalized)
        log(f"golden metadata: WROTE initial {golden_path.name} (commit it)")

    log("E2E GATE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
