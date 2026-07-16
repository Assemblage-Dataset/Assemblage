"""Subprocess execution that never kills the worker on a child timeout.

The old ``cmd_with_output`` ran the child in the worker's own process group,
so its timeout path (``killpg(getpgid(pid), SIGTERM)``) could SIGTERM the
worker itself. ``run_command`` starts every child in a **new session**
(``start_new_session=True``): the child is its own process-group leader, so on
timeout we ``killpg`` the child's whole tree and the worker survives.
"""

import logging
import os
import signal
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_TERM_GRACE_SECONDS = 3.0


@dataclass
class CommandResult:
    stdout: bytes
    stderr: bytes
    returncode: int


def run_command(
    cmd: str,
    *,
    timeout: float = 600.0,
    cwd: str | None = None,
) -> CommandResult:
    """Run ``cmd`` in a shell, returning captured output and the exit code.

    On timeout the child's entire process group is terminated (SIGTERM, then
    SIGKILL after a grace period) and a ``returncode=1`` result with stderr
    ``b"subprocess.TimeoutExpired"`` is returned, matching the legacy contract.
    """
    logger.debug("running command: %s", cmd)
    with subprocess.Popen(
        cmd,
        shell=True,
        cwd=cwd or None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        close_fds=True,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return CommandResult(stdout, stderr, process.returncode)
        except subprocess.TimeoutExpired:
            logger.warning("command timed out after %ss: %s", timeout, cmd)
            _terminate_group(process)
            return CommandResult(b"", b"subprocess.TimeoutExpired", 1)


def _terminate_group(process: "subprocess.Popen[bytes]") -> None:
    try:
        pgid = os.getpgid(process.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=_TERM_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=_TERM_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        logger.error("process group %d did not exit after SIGKILL", pgid)
