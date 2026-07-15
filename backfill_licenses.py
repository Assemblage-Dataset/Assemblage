"""Backfill license from old SQLite dataset into PostgreSQL."""
import os
import sqlite3
import psycopg2

SQLITE_PATH = "dataset/linux_licensed.sqlite"
PG_DSN = f"host={os.environ.get('DB_HOST', 'localhost')} dbname=assemblage user=assemblage password={os.environ.get('POSTGRES_PASSWORD', 'assemblage')}"

# SPDX -> GitHub display name mapping
SPDX_MAP = {
    "mit": "MIT License",
    "apache-2.0": "Apache License 2.0",
    "gpl-3.0": "GNU General Public License v3.0",
    "gpl-2.0": "GNU General Public License v2.0",
    "bsd-3-clause": 'BSD 3-Clause "New" or "Revised" License',
    "bsd-2-clause": 'BSD 2-Clause "Simplified" License',
    "lgpl-3.0": "GNU Lesser General Public License v3.0",
    "lgpl-2.1": "GNU Lesser General Public License v2.1",
    "agpl-3.0": "GNU Affero General Public License v3.0",
    "mpl-2.0": "Mozilla Public License 2.0",
    "bsl-1.0": "Boost Software License 1.0",
    "cc0-1.0": "Creative Commons Zero v1.0 Universal",
    "isc": "ISC License",
    "zlib": "zlib License",
    "wtfpl": "Do What The F*ck You Want To Public License",
    "unlicense": "The Unlicense",
    "0bsd": "BSD Zero Clause License",
    "other": "Other",
}

def main():
    sconn = sqlite3.connect(SQLITE_PATH)
    # Get distinct (url, license) from SQLite
    rows = sconn.execute(
        "SELECT github_url, license FROM binaries WHERE license <> '' GROUP BY github_url"
    ).fetchall()
    sconn.close()
    print(f"Loaded {len(rows)} repos with license from SQLite")

    # Build url -> license map (normalize URL to owner/repo slug)
    license_map = {}
    for url, lic in rows:
        slug = url.replace("https://github.com/", "").strip("/")
        display = SPDX_MAP.get(lic, lic)
        license_map[slug] = display

    pg = psycopg2.connect(PG_DSN)
    cur = pg.cursor()

    # Get PG repos with empty license
    cur.execute("SELECT id, url FROM projects WHERE license IS NULL OR license = ''")
    empty = cur.fetchall()
    print(f"Found {len(empty)} PG repos with empty license")

    updated = 0
    for rid, url in empty:
        slug = url.replace("https://github.com/", "").replace("https://api.github.com/repos/", "").strip("/")
        if slug in license_map:
            cur.execute("UPDATE projects SET license = %s WHERE id = %s", (license_map[slug], rid))
            updated += 1

    pg.commit()
    cur.close()
    pg.close()
    print(f"Updated {updated} repos")

if __name__ == "__main__":
    main()
