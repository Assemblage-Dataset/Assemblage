"""Run one DWARF extraction in a short-lived child process.

Why a child process rather than the extractor's own ``timeout_secs``: that
timeout is SIGALRM-based and ``dwarf.extract._alarm`` arms it *only on the main
thread*, but the builder runs every task on a :class:`~assemblage.runtime.
supervisor.Supervisor` worker thread. Passing ``timeout_secs`` from the builder
is therefore silently a no-op, which is why builder-side extraction has always
been unbounded.

Measured 2026-07-20 on the live fleet: llvm/Debug builders sat in a *single*
extraction for 18+ minutes at ~90% CPU and 2.5-3.9 GB RSS, completing 0 tasks in
a window where gcc tiers completed 17. Debug's own compile averages 41s, so
~97% of one of its tasks was extraction.

A child process bounds both dimensions:

- **wall clock** -- the parent kills the child's process group, which works from
  any thread and regardless of whether the time is spent in our loops or deep
  inside pyelftools (a cooperative deadline could only cover the former);
- **address space** -- ``RLIMIT_AS`` in the child, so a runaway extraction kills
  one child instead of OOM-killing the builder container. Extraction costs
  roughly 42x the binary size, so a 220 MB binary implies ~9.6 GB.

``dwarf.extract`` itself is untouched: the child imports and calls the very same
``extract_dwarf_info``, so output stays byte-for-byte identical and the golden
fixture still pins it.

Every failure mode -- timeout, OOM, crash, unparseable output -- returns
``None``, which is exactly what the existing ``DWARF_SIZE_LIMIT`` skip already
returns. Callers therefore need no new handling: the binary is still uploaded,
its metadata just carries an empty ``Binary_info_list``. Debug info is
best-effort; the artifact is not.
"""

import json
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterable, Iterator
from typing import Any

logger = logging.getLogger(__name__)

# Keep in sync with the module's __main__ contract below.
_CHILD_MODULE = "assemblage.dwarf.isolated"

# Emitted by the child on the line before its JSON payload, so a stray write to
# stdout from a library cannot be mistaken for the result.
_RESULT_MARKER = "---ASSEMBLAGE-DWARF-JSON---"


def extract_isolated(
    binfile: str,
    *,
    source_root: str | None = None,
    timeout_secs: int,
    mem_limit_bytes: int | None = None,
    jobs: int = 1,
) -> dict[str, Any] | None:
    """Extract ``binfile``'s DWARF in a child process; ``None`` if it can't.

    ``timeout_secs`` bounds the whole extraction in wall-clock terms.
    ``mem_limit_bytes``, when set, becomes the child's ``RLIMIT_AS``.
    ``jobs`` > 1 lets that child shard compile units across worker processes;
    they join its process group, so the timeout still reaps all of them.
    """
    cmd = [sys.executable, "-m", _CHILD_MODULE, binfile]
    if source_root:
        cmd += ["--source-root", source_root]
    if mem_limit_bytes:
        cmd += ["--mem-limit-bytes", str(mem_limit_bytes)]
    if jobs > 1:
        cmd += ["--jobs", str(jobs)]

    # start_new_session so the child leads its own process group and a timeout
    # can killpg the whole thing -- the same reason build.commands.run_command
    # does it. Without it, kill() would leave any grandchild running.
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as e:
        logger.warning("could not spawn DWARF extractor for %s: %s", binfile, e)
        return None

    try:
        stdout, stderr = proc.communicate(timeout=timeout_secs)
    except subprocess.TimeoutExpired:
        _killpg(proc)
        # Never silent: a binary losing its whole Binary_info_list has to be
        # attributable from the logs, same contract as the size-limit skip.
        logger.warning(
            "DWARF extraction timed out after %ss for %s (binary still stored, "
            "Binary_info_list will be empty)",
            timeout_secs,
            binfile,
        )
        return None

    if proc.returncode != 0:
        logger.warning(
            "DWARF extractor exited %s for %s: %s",
            proc.returncode,
            binfile,
            (stderr or "").strip()[-300:],
        )
        return None

    return _parse_result(stdout, binfile)


def extract_each(
    binfiles: Iterable[str],
    *,
    source_root: str | None = None,
    timeout_secs: int,
    phase_timeout_s: int,
    mem_limit_bytes: int | None = None,
    jobs: int = 1,
) -> Iterator[dict[str, Any]]:
    """Extract every binary in ``binfiles``, honouring an overall phase budget.

    Yields only the successful items, in the caller's iteration order (deliberately
    not sorted -- the C golden is pinned against the existing order). The
    per-binary timeout is clamped to whatever is left of the phase budget, so a
    build emitting many binaries cannot park a builder for
    ``len(binfiles) * timeout_secs``.
    """
    deadline = time.monotonic() + phase_timeout_s
    skipped = 0
    for binfile in binfiles:
        remaining = int(deadline - time.monotonic())
        if remaining <= 0:
            skipped += 1
            continue
        try:
            item = extract_isolated(
                binfile,
                source_root=source_root,
                timeout_secs=max(1, min(timeout_secs, remaining)),
                mem_limit_bytes=mem_limit_bytes,
                jobs=jobs,
            )
        except Exception as e:
            # Preserves the per-binary tolerance both debug_info loops had: one
            # bad binary must not cost the others their debug info.
            logger.warning("DWARF extraction failed for %s: %s: %s", binfile, type(e).__name__, e)
            continue
        if item:
            yield item
    if skipped:
        logger.warning(
            "DWARF phase budget of %ss exhausted: %d binary(ies) stored without debug info",
            phase_timeout_s,
            skipped,
        )


def _parse_result(stdout: str, binfile: str) -> dict[str, Any] | None:
    marker = stdout.rfind(_RESULT_MARKER)
    if marker < 0:
        logger.warning("DWARF extractor produced no result marker for %s", binfile)
        return None
    payload = stdout[marker + len(_RESULT_MARKER) :]
    try:
        item = json.loads(payload)
    except ValueError as e:
        logger.warning("DWARF extractor produced unparseable output for %s: %s", binfile, e)
        return None
    if item is None:
        return None
    if not isinstance(item, dict):
        logger.warning("DWARF extractor returned %s, expected object", type(item).__name__)
        return None
    return item


def _killpg(proc: subprocess.Popen[str]) -> None:
    """SIGKILL the child's process group, then reap it."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()
    try:
        proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover - the group is SIGKILLed
        logger.error("DWARF extractor %d did not exit after SIGKILL", proc.pid)


def _main(argv: list[str]) -> int:
    """Child entry point: extract one binary, print JSON after the marker."""
    import argparse

    parser = argparse.ArgumentParser(description="Extract DWARF info from one binary")
    parser.add_argument("binfile")
    parser.add_argument("--source-root", default=None)
    parser.add_argument("--mem-limit-bytes", type=int, default=0)
    parser.add_argument("--jobs", type=int, default=1)
    args = parser.parse_args(argv)

    if args.mem_limit_bytes > 0:
        # Applied here, after the interpreter and pyelftools are already loaded,
        # so the cap bounds extraction rather than startup. Exceeding it raises
        # MemoryError in this child only.
        import resource

        try:
            resource.setrlimit(resource.RLIMIT_AS, (args.mem_limit_bytes, args.mem_limit_bytes))
        except (ValueError, OSError) as e:  # pragma: no cover - platform dependent
            sys.stderr.write(f"could not set RLIMIT_AS: {e}\n")

    # Imported here so a failure to set the rlimit above is reported first, and
    # so --help costs nothing.
    from assemblage.dwarf.extract import extract_dwarf_info

    item = extract_dwarf_info(args.binfile, source_root=args.source_root, jobs=args.jobs)
    # Marker first: pyelftools or a warning may already have written to stdout.
    sys.stdout.write(_RESULT_MARKER)
    json.dump(item, sys.stdout)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    sys.exit(_main(sys.argv[1:]))
