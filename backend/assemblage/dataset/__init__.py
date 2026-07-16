"""Host-side dataset construction pipeline (absorbed Assemblage_dataset_cli).

Modules:
    pipeline  -- daily MinIO -> DWARF -> staging -> SQLite pipeline
    construct -- db_construct(), zip processing, ELF/DWARF staging helpers
    orm       -- SQLAlchemy models for the SQLite dataset + migrations
    store     -- Dataset_DB query/insert manager
    cli       -- the `assemblage-dataset` click entry point
    daily     -- the `assemblage-daily` runner (secrets.env + defaults)

These modules require the optional `dataset` extra (click, tqdm):
    uv sync --dev --extra dataset
"""
