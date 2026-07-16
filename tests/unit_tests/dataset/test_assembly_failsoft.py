"""The assembly sub-pipeline must never sink the daily binaries run.

run_assembly_pipeline references a store API (bulk_add_repos / a repos table)
that has never existed in any version of the dataset store, so until it is
rebuilt it fails on real data. run_pipeline calls it through
_harvest_assembly_failsoft, which logs and swallows — these tests pin that
contract.
"""

import logging

from assemblage.dataset import pipeline


def test_failsoft_swallows_and_logs(monkeypatch, caplog):
    def boom(**_kwargs):
        raise AttributeError("'Dataset_DB' object has no attribute 'bulk_add_repos'")

    monkeypatch.setattr(pipeline, "run_assembly_pipeline", boom)
    with caplog.at_level(logging.ERROR, logger="assemblage.dataset.pipeline"):
        pipeline._harvest_assembly_failsoft(since_date_str="2026-01-01")
    assert any("assembly sub-pipeline failed" in r.message for r in caplog.records)
    assert any(r.exc_info for r in caplog.records)


def test_failsoft_passes_kwargs_through(monkeypatch):
    seen = {}
    monkeypatch.setattr(pipeline, "run_assembly_pipeline", lambda **kw: seen.update(kw))
    pipeline._harvest_assembly_failsoft(since_date_str="2026-01-01", bucket="artifacts")
    assert seen == {"since_date_str": "2026-01-01", "bucket": "artifacts"}
