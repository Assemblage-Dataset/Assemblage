# Assemblage Dataset CLI

This repository holds the dataset construction tool for Assemblage

## Table schema

```
Table schema
    +=========+      +=========+      +===========+
    |binaries |      |functions|      |    rvas   |
    |=========|      |=========|      |===========|
    |   *id   |<-+   |   *id   |<--+  |    *id    |
    |---------|  |   |---------|   |  |-----------|
    |file_name|  +-- |binary_id|   +--|function_id|
    |---------|  |   |---------|   |  |-----------|
    | platform|  |   |  name   |   |  |   start   |
    |---------|  |   |---------|   |  |-----------|
    |   ...   |  |   |  hash   |   |  |    end    |
    +=========+  |   +=========+   |  +===========+
                 |                 |  
                 |   +=========+   |  +===========+
                 |   |  pdbs   |   |  |  lines    |
                 |   |=========|   |  |===========|
                 |   |   id    |   |  |    id     |
                 |   |---------|   |  |-----------|
                 +---|binary_id|   +--|function_id|
                     |---------|      |-----------|
                     |file_name|      |source_code|
                     +=========+      +===========+

* indicates indexing column
                    
```

## Shared DWARF extractor and S3 layout (P10)

`pipeline.py` no longer carries its own copy of the DWARF extractor. It imports
the one shared extractor, `assemblage.dwarf.extract.extract_dwarf_info` (the
same one the builder uses), through a thin `extract_dwarf_info` wrapper that
keeps the daily pipeline's `DWARF_TIMEOUT_SECS` contract (`0` disables
extraction; a positive value bounds it with a SIGALRM wall-clock timeout).
Likewise `download_binary` / `download_source_archive` derive every S3 key from
`assemblage.storage.layout` — the builder's flat artifact prefix
(`{owner}_{project}_{sha12}_{compiler}_{flag}/<file>`) is tried first, with the
historical slash-separated keys kept as fallbacks for pre-flat objects.

The one field that changed: the shared extractor uses the builder's per-line
`length` convention (a line's `length` is the gap to the next code address; the
last line of a function is no longer forced to `0`). `intersect_ratio` is also
formatted `"0%"` rather than `"0.00%"`. Neither affects corpus identity —
`length` is a derived heuristic the E2E golden already masks as `<len>`, and
`intersect_ratio` is never stored by `db_construct`.

### Parity gate

`tests/e2e/dataset_parity.sh` (`make parity`) proves the swap did not change the
dataset: it stands up the golden-repo E2E stack and runs the daily pipeline from
the pre-P10 tree (embedded extractor, slash-only download) and from the current
tree (shared extractor, flat-key download) against one identical stack state,
then diffs (A) the two `linux_licensed.sqlite` files column-by-column and (B) the
DWARF extractor output directly. Both diffs must be empty.

### Fixed: daily pipeline now stores functions/rvas/lines (2026-07-16, R5)

**What was wrong.** `db_construct` only stored a `Binary_info_list` entry when
its `file` field *equalled* the cleaned staged binary name, but the re-extracted
entry's `file` is the on-disk download name (`{binary_id}_{filename}`, e.g.
`1005_hello`) while db_construct compared it against the cleaned name (`hello`).
The two never matched, so **the daily pipeline stored zero DWARF
functions/rvas/lines** — those tables were populated only by bulk backfills.

**What matches now.** `staged_name_matches` (in `construct.py`) compares the
entry's basename both raw and with a leading `{digits}_` prefix stripped, and
normalises Windows/POSIX paths. So the re-extracted `1005_hello` and the
builder's already-clean Rust names (`golden_bin`) both resolve to the staged
binary, and the daily run now populates `functions`, `rvas` and `lines`. This
deliberately changes the daily corpus output, so the P10 parity gate
(`dataset_parity.sh`, which asserted output was *unchanged*) is retired as
historical; the new acceptance instrument is `tests/e2e/dataset_correctness.sh`
(`make dataset-gate`), which asserts the corpus is *correctly populated* for
both a C and a Rust binary.

### Known-incomplete: the assembly sub-pipeline (fail-soft)

`run_assembly_pipeline` (assembly.sqlite harvest of `.s` artifacts) has never
completed a run: it calls `Dataset_DB.bulk_add_repos` and a `repos` table that
no version of the dataset store — including the pre-absorption CLI — ever
defined. It runs *after* the binaries corpus is complete, wrapped in
`_harvest_assembly_failsoft`, which logs the failure and keeps the daily run
green. Rebuilding it against the real ORM is future work.

### New columns (R5, Rust support)

`binaries` gains `compiler`, `language`, `codegen_backend` (and `build_mode`,
which already existed on the model) — all nullable, threaded from the PostgreSQL
`buildopt` join through the staging metadata. Old C/C++ rows leave them NULL/`''`.
`functions` gains `demangled_name` (rustfilt v0 form) and `origin`
(`in_repo`/`dependency`/`stdlib`), both nullable and populated only for Rust —
the C/C++ DWARF extractor emits neither. For Rust the pipeline reuses the
builder's already-extracted per-binary entries (they carry demangled_name,
origin and build-time-resolved source) rather than re-extracting host-side.
`migrate_existing_db` adds all of these idempotently; no new indexes.

## CLI commands

Currently this CLI supports 3 commands

1.  Unzip the zips in one folder to the destination folder called unzipped_folder:
```
python cli.py --data zip_folder --dest unzipped_folder
```

2.  Generate serialized SQLite database 
```
python cli.py -g --data unzipped_folder --dbfile some.sqlite --functions --rvas --lines --pdbs
```

where --functions --rvas --lines --pdbs are flags to include the function, eva, lines and pdns information

3.  Legacy, will be deprecated soon: Add license for each reposotory:
```
python cli.py --addlicense --dbfile some.sqlite
```
