"""GitHub scraping: a rate-limit-aware REST client and a date-window search.

Decomposes the pre-re-architecture ``worker/scraper.py``'s ``GithubRepositories``
onto two focused classes, porting the frozen behaviour verbatim:

- :class:`GitHubClient` — ``get_request`` with GitHub's two rate limits
  (``X-RateLimit-*`` headers, ``Retry-After``, 403 secondary limits), token
  cycling across a primary + alternate tokens, proxy rotation, a request
  timeout, and the long-sleep ``sleep_and_update`` behaviour.
- :class:`GitHubRepoSearch` — the backwards date-window walk over the Search
  API, per-repo tree fetch for the file list, the **LICENSE-required** filter,
  language lowercasing, commit sha from the tree response, and build-system
  detection, producing wire-ready :class:`~assemblage.messages.RepoRecord`s.

The rate-limit / credential logic has good unit coverage
(``tests/unit_tests/workers/scraper_test.py``); those tests are the spec.
"""

import json
import logging
import random
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

import requests

from assemblage.build.detect import get_build_system
from assemblage.constants import (
    GITHUB_REPO_URL,
    RATE_LIMIT_UPDATE_INTERVAL,
    RATE_LIMIT_WAIT,
    SCRAPER_PAGE_SIZE,
    SCRAPER_RATE_INTERVAL,
    SCRAPER_REQUEST_TIMEOUT_S,
    SECONDARY_RATE_LIMIT_WAIT,
)
from assemblage.enums import GithubTimeOrder
from assemblage.messages import RepoRecord

logger = logging.getLogger(__name__)

# A GitHub response and the seconds it took to obtain, or (None, None) on error.
RequestResult = tuple[requests.Response | None, float | None]


def github_time_to_mysql_time(gtime: str) -> str:
    """Convert a GitHub ISO timestamp to a MySQL datetime string.

    Falls back to ``2000-01-01 01:01:01`` when the input cannot be parsed
    (frozen behaviour — the DB column must always get a valid datetime).
    """
    try:
        dt = datetime.strptime(gtime, "%Y-%m-%dT%H:%M:%SZ")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return datetime.strptime("2000-01-01T01:01:01Z", "%Y-%m-%dT%H:%M:%SZ").strftime(
            "%Y-%m-%d %H:%M:%S"
        )


class GitHubClient:
    """Issues GitHub REST requests, handling rate limits and token cycling.

    GitHub enforces two separate rate limits — one for the Search API and one
    for the standard API used to fetch a repo's tree. ``get_request`` respects
    both: it parses the ``X-RateLimit-*`` headers, sleeps until reset (or cycles
    to an alternate token), honours ``Retry-After`` and 403 *secondary* limits,
    and retries transparently.
    """

    def __init__(
        self,
        token: str | None,
        *,
        alternate_tokens: list[str] | None = None,
        proxies: list[str] | None = None,
        worker_id: int = 0,
        timeout: int = SCRAPER_REQUEST_TIMEOUT_S,
    ) -> None:
        self.worker_id = worker_id
        self.timeout = timeout

        # Authentication first: configure_token_cycling reads self.token.
        self.set_token(token)
        self.configure_token_cycling(alternate_tokens)

        self.proxies: list[str] = list(proxies) if proxies else []
        if "" not in self.proxies:
            self.proxies.append("")

    # --- proxies --------------------------------------------------------------

    def random_proxy(self) -> dict[str, str] | None:
        """Return a random proxy mapping for ``requests`` (or ``None``)."""
        if self.proxies == []:
            return None
        return {"https": random.choice(self.proxies)}

    # --- tokens ---------------------------------------------------------------

    def set_token(self, token: str | None) -> None:
        """Set the active token and rebuild the auth headers accordingly."""
        self.token = token
        if self.token in [None, ""]:
            logger.warning(
                "No Token is set. Scraper will be severely rate-limited. "
                "Please configure a PAT and add it to secrets.env as GITHUB_TOKEN, "
                "and then restart."
            )
            self.auth_headers: dict[str, str] = {}
        else:
            self.auth_headers = {"Authorization": f"Bearer {self.token}"}

    def set_token_timeout(self, timeout: float) -> None:
        for entry in self.alternate_tokens:
            if entry["token"] == self.token:
                entry["timeout"] = timeout

    def cycle_token(self) -> int:
        """Switch to the next non-timed-out alternate token.

        Favours tokens earliest in the list (no load balancing). Returns 1 on
        success, 0 when every alternate token is still timed out.
        """
        for index, entry in enumerate(self.alternate_tokens):
            if entry["timeout"] == 0 or entry["timeout"] <= int(time.time()):
                self.set_token(entry["token"])
                logger.info("Cycling token to provided token %s", index)
                return 1
        logger.info("Problem with switching tokens: no non timed out token found. ")
        return 0

    def configure_token_cycling(self, alternate_git_tokens: list[str] | None) -> None:
        """Build the alternate-token table when alternates were supplied."""
        self.do_cycle_tokens = alternate_git_tokens is not None and len(alternate_git_tokens) > 0
        self.alternate_tokens: list[dict[str, Any]]
        if self.do_cycle_tokens:
            assert alternate_git_tokens is not None
            self.alternate_tokens = []
            if self.token is not None:
                self.alternate_tokens.append({"token": self.token, "timeout": 0})
            for candidate in alternate_git_tokens:
                self.alternate_tokens.append({"token": candidate, "timeout": 0})
        else:
            self.alternate_tokens = [{"token": self.token, "timeout": 0}]

    # --- sleeping -------------------------------------------------------------

    def sleep_and_update(self, duration: float, reason: str = "") -> None:
        """Sleep ``duration`` seconds, logging periodic wake-up estimates."""
        time_left = duration
        while time_left > 0:
            logger.info(
                "Crawler %s will wake up in %ss (%sm). Reason for sleep: %s",
                self.worker_id,
                round(time_left, 2),
                round(time_left / 60, 1),
                reason,
            )
            time.sleep(min(RATE_LIMIT_UPDATE_INTERVAL, time_left))
            time_left -= RATE_LIMIT_UPDATE_INTERVAL
        logger.info("Crawler %s done sleeping, resuming activity...", self.worker_id)

    # --- the request ----------------------------------------------------------

    def get_request(
        self,
        query: str,
        payload: dict[str, Any] | None = None,
        headers: Any = "default",
        proxy: Any = "random",
    ) -> RequestResult:
        """Issue a GET to ``query``, handling rate limits, and return (resp, elapsed).

        Default behaviour sends an empty payload with :attr:`auth_headers` and a
        random proxy. Retries transparently on 401 (drops the bad token),
        rate-limit reset, and ``Retry-After``.
        """
        use_headers = self.auth_headers if headers == "default" else headers
        use_proxy = self.random_proxy() if proxy == "random" else proxy

        start_request_time = float(time.time())
        try:
            r = requests.get(
                query,
                params=payload,
                headers=use_headers,
                proxies=use_proxy,
                timeout=self.timeout,
            )
        except Exception as err:
            logger.error("An unexpected issue occurred when getting query %s:", query)
            logger.info(err)
            return None, None

        receipt_time = float(time.time())

        if r.status_code == 404:
            logger.error("404 Not Found. Query: %s:", query)
            return None, None

        if r.status_code == 401:
            logger.error(
                "401 Unauthorized. The authentication token provided is not valid. "
                "Please provide a valid token."
            )
            logger.info("Scraping will proceed unauthenticated.")
            self.set_token(None)
            return self.get_request(query=query, payload=payload, headers=headers, proxy=proxy)

        # The rest of the function checks for rate limits and other issues.
        try:
            total_rate_limit = int(r.headers["X-RateLimit-Limit"])
            remaining_rate_limit = int(r.headers["X-RateLimit-Remaining"])
            rate_limit_reset_time = float(r.headers["X-RateLimit-Reset"])
        except (KeyError, ValueError):
            logger.warning("Error when converting rate limit headers into values.")
            return None, None

        try:
            rdict = json.loads(r.text)
        except Exception as err:
            logger.error(
                "Error when parsing query result (format of result may not be as expected)."
            )
            logger.info(err)
            return None, start_request_time - receipt_time

        if (
            "message" in rdict
            and "rate limit" in rdict["message"]
            and "secondary" in rdict["message"]
        ):
            logger.warning(
                "Secondary rate limit hit -- this indicates that GitHub has "
                "identified unusual scraper activity. Scraping should be paused."
            )
            success = 0
            if self.do_cycle_tokens:
                self.set_token_timeout(rate_limit_reset_time)
                success = self.cycle_token()
            if not self.do_cycle_tokens or not success:
                self.sleep_and_update(
                    SECONDARY_RATE_LIMIT_WAIT, reason="Secondary rate limit reached"
                )

        # Check rate limits per https://docs.github.com/en/rest/using-the-rest-api/
        if remaining_rate_limit == 0:
            time_to_reset = rate_limit_reset_time - float(time.time()) + 1
            success = 0
            if self.do_cycle_tokens:
                self.set_token_timeout(rate_limit_reset_time)
                success = self.cycle_token()
            if not self.do_cycle_tokens or not success:
                logger.info(
                    "Rate limit (%s) reached. Crawler %s will sleep for %ss. ",
                    total_rate_limit,
                    self.worker_id,
                    round(time_to_reset, 2),
                )
                self.sleep_and_update(time_to_reset, reason="Rate limit reached")
            # retry after timeout
            if not r.ok:
                return self.get_request(query, payload=payload, headers=headers, proxy=proxy)

        if "Retry-After" in r.headers:
            # rate limit not respected: wait for Retry-After seconds
            try:
                retry_time = int(r.headers["Retry-After"])
            except ValueError:
                logger.warning(
                    "Warning: was not able to extract Retry-After time from headers (text is '%s')",
                    r.headers["Retry-After"],
                )
                retry_time = RATE_LIMIT_WAIT

            time_since_response = int(time.time()) - receipt_time
            time_to_sleep = max(0, retry_time - time_since_response)
            if time_to_sleep > 0:
                logger.info(
                    "Github refused connection due to rate limit, sleeping for %ss",
                    round(time_to_sleep + 1, 2),
                )
                self.sleep_and_update(time_to_sleep + 1, reason="Rate limit reached (2)")
                return self.get_request(query, payload=payload, headers=headers, proxy=proxy)

        if not r.ok:
            logger.error(
                "Crawler request was UNSUCCESSFUL (status code %s). Query: %s", r.status_code, query
            )
            return None, start_request_time - receipt_time

        elapsed_time = round(float(time.time()) - start_request_time, 2)
        return r, elapsed_time


class GitHubRepoSearch:
    """Walks GitHub's Search API backwards in time, yielding :class:`RepoRecord`s.

    Each date window (``created``/``pushed`` within
    ``[current - interval, current]``) is paged through the Search API; every
    result is expanded with a per-repo tree fetch for its file list, filtered to
    require a license, and turned into a wire-ready record.
    """

    def __init__(
        self,
        client: GitHubClient,
        qualifiers: set[str],
        crawl_time_start: int,
        crawl_time_end: int,
        crawl_time_interval: int,
        *,
        sort: GithubTimeOrder = GithubTimeOrder.CREATED,
        build_sys_callback: Callable[[list[str]], str] = get_build_system,
        worker_id: int = 0,
    ) -> None:
        self.client = client
        self.qualifiers = qualifiers
        self.crawl_time_start = crawl_time_start
        self.crawl_time_end = crawl_time_end
        self.crawl_time_interval = crawl_time_interval
        self.current_crawl_time = crawl_time_start
        self.sort = sort
        self.build_sys_callback = build_sys_callback
        self.worker_id = worker_id

    def _process_repo_message(self, repo: Any) -> tuple[RepoRecord, list[str]] | tuple[None, None]:
        """Fetch a search result's file tree and build its :class:`RepoRecord`.

        Returns ``(None, None)`` for malformed input, an unreachable tree, or a
        repo with no license (the LICENSE-required filter).
        """
        if not isinstance(repo, dict):
            logger.error(
                "_process_repo_message expects dictionary as input, not %s", str(type(repo))
            )
            return None, None
        url = repo["url"]
        default_branch = repo["default_branch"]
        req = f"{url}/git/trees/{default_branch}"
        try:
            page, _ = self.client.get_request(req)
            if page is None:
                logger.info("Could not process repo %s: error getting page %s", url, req)
                return None, None
        except Exception as err:
            logger.info(err)
            return None, None

        time.sleep(SCRAPER_RATE_INTERVAL)  # prevents monopolizing resources

        repo_page = json.loads(page.text)
        if "tree" not in repo_page:
            return None, None

        files = [record["path"] for record in repo_page["tree"] if "path" in record]
        build_tool = self.build_sys_callback(files)

        license_info = repo.get("license")
        if not license_info:
            return None, None
        license_name = license_info.get("name", "") if isinstance(license_info, dict) else ""
        if not license_name:
            return None, None

        record = RepoRecord(
            name=repo["name"],
            url=url,
            language=repo["language"].lower(),
            owner_id=repo["owner"]["id"],
            description=repo["description"] or "",
            created_at=github_time_to_mysql_time(repo["created_at"]),
            updated_at=github_time_to_mysql_time(repo["pushed_at"]),
            size=int(repo["size"]),
            build_system=build_tool,
            branch=repo["default_branch"],
            commit_hexsha=repo_page["sha"],
            license=license_name,
        )
        return record, files

    def fetch_data(self) -> Iterator[tuple[RepoRecord, list[str]]]:
        """Page the Search API window-by-window, yielding (record, files)."""
        if self.crawl_time_start < self.crawl_time_end:
            logger.error(
                "Warning: start crawl time %s is earlier than the oldest permitted timestamp %s.",
                self.crawl_time_start,
                self.crawl_time_end,
            )
        self.current_crawl_time = self.crawl_time_start

        while self.current_crawl_time > self.crawl_time_end:
            # up here to reduce instances of rescraping the same repos
            self.current_crawl_time -= self.crawl_time_interval
            self.current_crawl_time = int(self.current_crawl_time)

            query_time_start = datetime.utcfromtimestamp(self.current_crawl_time).isoformat()
            query_time_end = datetime.utcfromtimestamp(
                self.current_crawl_time + self.crawl_time_interval
            ).isoformat()
            qualifier_str = " ".join(self.qualifiers)
            query_s = (
                f"{self.sort.value}:{query_time_start}+08:00..{query_time_end}+08:00 "
                f"{qualifier_str}"
            )

            logger.debug("Crawler query is ' %s ' (GitHub)", query_s)
            total_query_results_count = 999  # big enough to run the while loop once
            payload: dict[str, Any] = {"q": query_s, "per_page": SCRAPER_PAGE_SIZE, "page": -1}
            while payload["page"] * SCRAPER_PAGE_SIZE < total_query_results_count:
                try:
                    payload["page"] += 1
                    r, request_response_time = self.client.get_request(
                        GITHUB_REPO_URL, payload=payload
                    )
                    logger.info("Crawler request respond in %ss", request_response_time)
                    if r is None:
                        continue

                    rdict = json.loads(r.text)
                    if "items" in rdict:
                        total_query_results_count = min(
                            rdict["total_count"], total_query_results_count
                        )
                        logger.debug(
                            "Successful search result obtained by crawler %s. "
                            "GitHub responded with %s repos",
                            self.worker_id,
                            total_query_results_count,
                        )
                        for repo in rdict["items"]:
                            record, files = self._process_repo_message(repo)
                            if record and files:
                                yield record, files
                except Exception as err:
                    logger.info(err)
        logger.info("scraping finished!")

    def __iter__(self) -> Iterator[RepoRecord]:
        for record, _files in self.fetch_data():
            yield record
