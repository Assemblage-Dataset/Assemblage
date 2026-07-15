"""Hermetic environment for the test suite.

Several production modules read settings at import time (a pre-rearchitecture
behavior — CoordinatorSettings freezes os.getenv values in Field defaults),
so the environment must be shaped BEFORE test modules import them. pytest
imports this conftest first.

Unit tests must run with no services: no database, no RabbitMQ, and — most
importantly — no S3 (an S3_HOST in the environment makes Coordinator.__init__
construct a real boto3 client and open sockets; that was the source of the
historical "16 errors" baseline when secrets.env leaked into test runs).
"""
import os

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("POSTGRES_DATABASE", "assemblage_test")
os.environ.setdefault("POSTGRES_USER", "assemblage")
os.environ.setdefault("POSTGRES_PASSWORD", "assemblage")

# Never let a developer's secrets.env turn unit tests into network tests.
for _var in ("S3_HOST", "S3_ACCESS_KEY", "S3_SECRET_ACCESS_KEY"):
    os.environ.pop(_var, None)
