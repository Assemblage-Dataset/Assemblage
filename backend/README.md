# backend/

`backend/` is the Python source root (bind-mounted into every worker container
at `/app`, and the target of the alembic runbooks). It holds:

- `assemblage/` — the core package. See `assemblage/README.md` for the module map.
- `scripts/` — entry points: `start_worker.py` (`TYPE` dispatch), the host-side
  `run_daily_dataset.py` / `restage_from_raw.py`, and `build_deephistory.py`.
- `alembic/` + `alembic.ini` — migrations. The schema is frozen to the live DB;
  read `alembic/README.md` before touching models.

Project config (pyproject, ruff, mypy, pytest) lives at the repository root.
