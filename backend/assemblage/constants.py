"""Tunable constants for the Assemblage runtime.

The canonical home for numeric/string tunables used by the re-architected
coordinator, builder and scraper. ``consts.py`` re-exports these so un-ported
modules keep importing ``assemblage.consts`` unchanged.
"""

# --- Coordinator dispatch pacing ---------------------------------------------

DISPATCH_INTERVAL = 0.1
"""Seconds to pause after a successful dispatch. 0 is fine except with a large
backlog and all workers on one machine, where it can starve other workers."""

IDLE_DISPATCH_INTERVAL = 30
"""Seconds to wait before retrying when a dispatch scan finds nothing."""

WAIT_AFTER_REQ_INTERVAL = 1
"""Seconds to wait after requesting more repos before continuing to dispatch."""

COORDINATOR_REPO_REQUEST_THRESHOLD = 10
"""Below this many pending repos for a buildopt, the coordinator asks
ON_REQUEST scrapers for more. Should be >= replicas per buildopt."""

COORDINATOR_REPO_REQUEST_TIMEOUT = 60
"""Seconds to wait for a repo bundle before re-sending the request."""

# --- Scraper -----------------------------------------------------------------

SCRAPER_REPO_BUNDLESIZE = 25
"""Repositories sent to the coordinator per bundle. Lower = lower latency."""

SCRAPER_RATE_INTERVAL = 0.01
"""Seconds between scrapes. 0 can choke CONTINUOUS-policy units."""

SCRAPER_PAGE_SIZE = 100
"""Repos per page of GitHub search results (unlikely to need changing)."""

GITHUB_REPO_URL = "https://api.github.com/search/repositories"

SCRAPER_REQUEST_TIMEOUT_S = 10
"""HTTP reply timeout when talking to GitHub, in seconds."""

RATE_LIMIT_WAIT = 60
"""Seconds to wait on a primary rate limit when no reset time is parseable."""

SECONDARY_RATE_LIMIT_WAIT = 120
"""Seconds to wait on a secondary rate limit."""

RATE_LIMIT_UPDATE_INTERVAL = 60
"""Seconds between "still sleeping" rate-limit progress messages."""

# --- RabbitMQ channel tuning -------------------------------------------------

CHANNEL_HEARTBEAT = 500
"""AMQP heartbeat interval in seconds."""

CHANNEL_TIMEOUT = 350
"""Blocked-connection timeout in seconds."""

# --- Filesystem --------------------------------------------------------------

BIN_DIR = "/binaries"
"""Root directory for builder work trees and produced binaries."""
