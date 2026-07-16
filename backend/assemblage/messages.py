"""Typed RabbitMQ messages (pydantic v2), wire-compatible with the frozen goldens.

Each model serializes (``model_dump_json``) and parses (``model_validate_json``)
the exact JSON in ``tests/fixtures/messages/*.json``. String enums serialize as
their lowercase ``.value``; ``ScrapeBundle`` is a bare JSON array. Every model
ignores unknown keys so a mixed-era queue (old producers, new consumers) keeps
working.

Two deliberate deltas from the pre-re-architecture wire, both documented in the
fixtures README:

- ``BuildTask`` drops the write-only ``output_dir`` / ``mod_timestamp`` fields
  (receivers ignore missing keys).
- ``ScraperControlReply.qualifiers`` is a real ``list[str] | None`` now (the old
  ``ScraperControlTaskOut`` discarded its argument and always sent ``null``).
"""

from collections.abc import Iterator

from pydantic import BaseModel, ConfigDict, RootModel, model_validator

from assemblage.enums import (
    BuildStatus,
    CloneStatus,
    ScraperMsgType,
    ScraperOutputPolicy,
)
from assemblage.mq.topology import build_opt_queue


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class RepoRecord(_WireModel):
    """One scraped repository (bundle element)."""

    name: str
    url: str
    language: str
    owner_id: int
    description: str
    created_at: str
    updated_at: str
    size: int
    build_system: str
    branch: str
    commit_hexsha: str | None = None
    license: str | None = None


class ScrapeBundle(RootModel[list[RepoRecord]]):
    """A batch of scraped repositories — serialized as a BARE JSON ARRAY."""

    def __len__(self) -> int:
        return len(self.root)

    def __iter__(self) -> Iterator[RepoRecord]:  # type: ignore[override]
        return iter(self.root)


class CloneStatusMsg(_WireModel):
    url: str
    opt_id: int
    status: CloneStatus
    msg: str
    task_id: int


class BuildStatusMsg(_WireModel):
    url: str
    opt_id: int
    status: BuildStatus
    msg: str
    task_id: int
    build_time: int
    commit_hexsha: str


class BinaryRecordMsg(_WireModel):
    task_id: int
    file_name: str


class BuilderRegistration(_WireModel):
    """Sent by a builder on startup to register its (compiler, flag) identity.

    2026-07-16 sanctioned evolution (Rust rollout): ``codegen_backend`` and
    ``build_mode`` were added with defaults, so pre-evolution JSON (the frozen
    ``builder_reg_in`` golden) still parses; new serializations follow the
    ``builder_reg_v2`` golden. Both goldens are pinned in the fixtures README.
    """

    name: str
    uuid: str
    compiler: str
    library: str
    language: str
    platform: str
    compiler_flag: str
    build_command: str
    build_system: str
    codegen_backend: str = ""
    build_mode: str = "RelWithDebInfo"


class BuilderRegistered(_WireModel):
    """Coordinator's reply: the assigned build-option id and its queue."""

    build_opt_id: int
    build_opt_queue: str | None = None

    @model_validator(mode="after")
    def _default_queue(self) -> "BuilderRegistered":
        if self.build_opt_queue is None:
            self.build_opt_queue = build_opt_queue(self.build_opt_id).name
        return self


class ScraperControlRequest(_WireModel):
    """Scraper -> coordinator registration/handshake (SETUP/UPDATE)."""

    message_type: ScraperMsgType
    start_time: int
    end_time: int


class ScraperControlReply(_WireModel):
    """Coordinator -> scraper control message (setup / request-repos)."""

    message_type: ScraperMsgType
    start_time: int | None = None
    end_time: int | None = None
    policy: ScraperOutputPolicy | None = None
    request_amount: int = -1
    specific_recipient: bool = True
    qualifiers: list[str] | None = None


class BuildTask(_WireModel):
    """Coordinator -> builder: clone then build a repo at a commit."""

    name: str
    url: str
    task_id: int
    opt_id: int
    repo_id: int
    updated_at: str
    build_system: str
    msg_time: float
    commit_hexsha: str = ""
    compiler_flag: str = ""
