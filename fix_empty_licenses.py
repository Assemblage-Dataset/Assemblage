"""Fix empty/None license fields in SQLite by querying the GitHub API."""
from __future__ import annotations
import os
import sqlite3
import time
import requests
from dotenv import load_dotenv

load_dotenv("secrets.env")

SQLITE_PATH = "dataset/linux_licensed.sqlite"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

HEADERS = {"Accept": "application/vnd.github.v3+json"}
if GITHUB_TOKEN:
    HEADERS["Authorization"] = f"token {GITHUB_TOKEN}"


def get_license_from_github(owner_repo: str) -> str | None:
    """Query GitHub API for repo license. Returns SPDX id or None."""
    url = f"https://api.github.com/repos/{owner_repo}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 403:
            # Rate limited — check reset time
            reset = int(r.headers.get("X-RateLimit-Reset", 0))
            wait = max(reset - int(time.time()), 1)
            print(f"  Rate limited, sleeping {wait}s")
            time.sleep(wait)
            return get_license_from_github(owner_repo)
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            print(f"  Unexpected status {r.status_code} for {owner_repo}")
            return None
        data = r.json()
        lic = data.get("license")
        if lic and lic.get("spdx_id") and lic["spdx_id"] != "NOASSERTION":
            return lic["spdx_id"].lower()
        return None
    except requests.RequestException as e:
        print(f"  Request error for {owner_repo}: {e}")
        return None


def main():
    conn = sqlite3.connect(SQLITE_PATH)
    cur = conn.cursor()

    # Get distinct repos with empty or None license
    rows = cur.execute(
        "SELECT DISTINCT github_url FROM binaries WHERE license = '' OR license = 'None'"
    ).fetchall()
    print(f"Found {len(rows)} repos with empty/None license")

    updated_repos = 0
    updated_rows = 0
    skipped = 0
    not_found = 0

    for i, (url,) in enumerate(rows):
        owner_repo = url.replace("https://github.com/", "").strip("/")
        lic = get_license_from_github(owner_repo)

        if lic:
            cur.execute(
                "UPDATE binaries SET license = ? WHERE github_url = ? AND (license = '' OR license = 'None')",
                (lic, url),
            )
            count = cur.rowcount
            updated_repos += 1
            updated_rows += count
            print(f"[{i+1}/{len(rows)}] {owner_repo} -> {lic} ({count} rows)")
        else:
            skipped += 1
            # Check if repo is gone (404) — mark as "unknown" so we don't retry
            if lic is None:
                cur.execute(
                    "UPDATE binaries SET license = 'unknown' WHERE github_url = ? AND (license = '' OR license = 'None')",
                    (url,),
                )
                not_found += 1

        # Commit every 50 repos
        if (i + 1) % 50 == 0:
            conn.commit()
            print(f"  ...committed ({i+1}/{len(rows)})")

    conn.commit()
    conn.close()

    print(f"\nDone:")
    print(f"  Repos with license found: {updated_repos} ({updated_rows} rows updated)")
    print(f"  Repos with no license / not found: {skipped} (marked 'unknown')")


if __name__ == "__main__":
    main()
