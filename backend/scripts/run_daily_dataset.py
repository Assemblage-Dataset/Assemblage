#!/usr/bin/env python3
"""Thin launcher for the daily Assemblage dataset pipeline.

The runner logic lives in :mod:`assemblage.dataset.daily` (also exposed as the
``assemblage-daily`` console script). This script exists so the pipeline can be
invoked directly from a checkout::

    python backend/scripts/run_daily_dataset.py [--since YYYY-MM-DD] [--dataset-dir PATH]

It relies on the ``assemblage`` package being importable (installed via
``uv sync --dev --extra dataset`` or on PYTHONPATH).
"""

from assemblage.dataset.daily import main

if __name__ == "__main__":
    main()
