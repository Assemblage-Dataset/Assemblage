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

### Known defect (pre-existing, out of P10 scope)

`db_construct` only stores a `Binary_info_list` entry when its `file` field
equals the cleaned staged binary name, but `build_staging_entry` writes the raw
download name (`{binary_id}_{filename}`) into `file`. The names never match, so
**the daily pipeline currently stores zero DWARF functions/rvas/lines** — the
`functions`/`rvas`/`lines` tables are populated only by bulk backfills, not the
daily run. This is why the parity gate's comparison (B) exists: the SQLite path
(A) cannot exercise the extractor. The defect predates P10 (it lives in both
trees the parity gate compares) and fixing it would change frozen pipeline
output, so it is left for a later phase.

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
