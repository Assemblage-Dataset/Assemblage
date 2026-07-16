"""Test-suite configuration constants.

Relocated out of ``assemblage.consts`` during the P5 consts split: these are
test-only tunables with no production consumer, so they belong in the test
tree rather than the shipped package.

The conftest refuses to run against a DB literally named 'assemblage' so the
live corpus can never be truncated by a test.
"""

import os

TEST_MESSAGE_LEVEL = "DEBUG"
TEST_DB_ADDR = os.getenv(
    "TEST_DB_ADDR",
    "postgresql+psycopg2://assemblage:assemblage@assemblage-test-db:5432/assemblage",
)
