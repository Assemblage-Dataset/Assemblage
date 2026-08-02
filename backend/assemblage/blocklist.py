"""Owner/repository blocklist consulted at dispatch.

The corpus is dominated by a handful of pathological repositories: five
``Dicklesworthstone/franken*`` repos alone hold 581 GiB of the 2.9 TiB
``artifacts`` bucket (measured 2026-08-02), and every build option added later
back-fills them again. This is the kill switch — a blocked owner or repository
is never handed to a builder.

The list is a plain text file rather than an env var so it can be edited
**without restarting the coordinator**. That matters here: the coordinator only
starts a build option's dispatch thread when a builder *registers*, so bouncing
it to pick up a config change strands the whole fleet for ~45 minutes.
:class:`FileBlocklist` re-stats the file at most once every
``RELOAD_INTERVAL_S`` and reloads when it changes, so an edit takes effect on a
live coordinator within seconds.

Format — one entry per line; ``#`` comments and blank lines are ignored::

    Dicklesworthstone           # every repository owned by this account
    someone/one-huge-repo       # just this repository
    https://github.com/a/b      # a URL works too; only owner/name is kept

Matching is case-insensitive, because GitHub logins are.
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: How often :class:`FileBlocklist` re-stats its file. Dispatch consults the
#: blocklist on every tick across ~20 threads; stat-ing that often is pointless.
RELOAD_INTERVAL_S = 30.0

# Characters that are wildcards in SQL LIKE. GitHub names very commonly contain
# '_' (``franken_numpy``), which would otherwise match any single character.
_LIKE_ESCAPE = "\\"


def _escape_like(value: str) -> str:
    """Escape ``\\``, ``%`` and ``_`` for a LIKE pattern using ``_LIKE_ESCAPE``."""
    for char in (_LIKE_ESCAPE, "%", "_"):
        value = value.replace(char, _LIKE_ESCAPE + char)
    return value


def split_repo_url(url: str) -> tuple[str, str] | None:
    """``(owner, name)`` lowercased from a repo URL or an ``owner/name`` slug.

    Handles both URL shapes the system stores: the scraper writes GitHub API
    URLs (``https://api.github.com/repos/{owner}/{name}``) and ``patch_url``
    turns them into clone URLs (``https://github.com/{owner}/{name}``). Only the
    last two path segments are read, so both work, as does a bare slug.
    """
    path = url.split("://", 1)[-1]
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        return None
    owner, name = parts[-2], parts[-1]
    if name.endswith(".git"):
        name = name[: -len(".git")]
    if not owner or not name:
        return None
    return owner.lower(), name.lower()


@dataclass(frozen=True)
class Blocklist:
    """A parsed set of blocked owners and blocked ``owner/name`` repositories."""

    owners: frozenset[str] = frozenset()
    repos: frozenset[str] = frozenset()

    def __bool__(self) -> bool:
        return bool(self.owners or self.repos)

    @classmethod
    def parse(cls, text: str) -> "Blocklist":
        """Parse the file format; unparseable entries are logged and skipped."""
        owners: set[str] = set()
        repos: set[str] = set()
        for lineno, raw in enumerate(text.splitlines(), start=1):
            entry = raw.split("#", 1)[0].strip()
            if not entry:
                continue
            if "/" not in entry:
                owners.add(entry.lower())
                continue
            split = split_repo_url(entry)
            if split is None:
                logger.warning("ignoring unparseable blocklist entry on line %d: %r", lineno, raw)
                continue
            owner, name = split
            repos.add(f"{owner}/{name}")
        return cls(frozenset(owners), frozenset(repos))

    def matches(self, url: str) -> bool:
        """True when ``url`` names a blocked repository or a blocked owner's repo."""
        split = split_repo_url(url)
        if split is None:
            return False
        owner, name = split
        return owner in self.owners or f"{owner}/{name}" in self.repos

    def like_patterns(self) -> tuple[str, ...]:
        """LIKE patterns matching exactly the blocked URLs, for the SQL filter.

        Dispatch excludes blocked rows *in the query* rather than filtering the
        result in Python: a blocked row at the head of the queue would otherwise
        be re-selected and re-rejected forever, wedging that build option's
        dispatcher. Patterns are anchored so they cannot over-match — an owner
        entry requires ``/{owner}/`` to be followed by something (the repo name),
        and a repo entry must end the URL (optionally with trailing path).
        """
        patterns = [f"%/{_escape_like(owner)}/%" for owner in sorted(self.owners)]
        for slug in sorted(self.repos):
            escaped = _escape_like(slug)
            patterns.append(f"%/{escaped}")
            patterns.append(f"%/{escaped}/%")
        return tuple(patterns)


EMPTY = Blocklist()

#: What the store calls to get the blocklist in force right now.
BlocklistProvider = Callable[[], Blocklist]


class FileBlocklist:
    """A :class:`Blocklist` backed by a file, reloaded in place when it changes.

    A read failure never *unblocks*: the last good list stays in force and the
    error is logged, so a truncated or briefly-unreadable file cannot quietly
    re-admit a repository that is costing terabytes.
    """

    def __init__(self, path: str | Path, reload_interval_s: float = RELOAD_INTERVAL_S) -> None:
        self._path = Path(path)
        self._interval = reload_interval_s
        self._lock = threading.Lock()
        self._current = EMPTY
        self._stamp: tuple[int, int] | None = None
        self._checked_at = float("-inf")
        self.current()  # load once up front so startup logs the real list

    @property
    def path(self) -> Path:
        return self._path

    def current(self) -> Blocklist:
        """The blocklist in force, reloading if the file changed."""
        with self._lock:
            now = time.monotonic()
            if now - self._checked_at >= self._interval:
                self._checked_at = now
                self._reload_locked()
            return self._current

    def _reload_locked(self) -> None:
        try:
            stat = self._path.stat()
        except FileNotFoundError:
            if self._stamp is not None:
                logger.warning("blocklist %s disappeared; keeping the loaded list", self._path)
            return
        except OSError as exc:
            logger.warning(
                "cannot stat blocklist %s (%s); keeping the loaded list", self._path, exc
            )
            return

        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp == self._stamp:
            return

        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "cannot read blocklist %s (%s); keeping the loaded list", self._path, exc
            )
            return

        self._stamp = stamp
        self._current = Blocklist.parse(text)
        logger.info(
            "blocklist loaded from %s: %d owner(s), %d repo(s)",
            self._path,
            len(self._current.owners),
            len(self._current.repos),
        )
