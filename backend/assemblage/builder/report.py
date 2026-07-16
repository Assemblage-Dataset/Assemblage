"""Status reporting: the builder's four messages back to the coordinator.

``BuildReporter`` owns a :class:`~assemblage.mq.publisher.Publisher` and turns
pipeline events into the frozen wire messages on the ``clone`` / ``build`` /
``binary`` queues. Message bodies are truncated to the last 1000 characters, as
the pre-re-architecture builder did (the coordinator truncates further on
ingest).
"""

import logging

from assemblage.enums import BuildStatus, CloneStatus
from assemblage.messages import BinaryRecordMsg, BuildStatusMsg, CloneStatusMsg
from assemblage.mq.publisher import Publisher
from assemblage.mq.topology import BINARY, BUILD, CLONE

logger = logging.getLogger(__name__)

_MSG_LIMIT = 1000


class BuildReporter:
    """Publishes clone/build/binary status messages for one build option."""

    def __init__(self, publisher: Publisher, opt_id: int, uuid: str) -> None:
        self._publisher = publisher
        self._opt_id = opt_id
        self._uuid = uuid

    def clone_status(self, *, url: str, status: CloneStatus, msg: str, task_id: int) -> None:
        self._publisher.publish(
            CLONE,
            CloneStatusMsg(
                url=url,
                opt_id=self._opt_id,
                status=status,
                msg=msg[-_MSG_LIMIT:],
                task_id=task_id,
            ).model_dump_json(),
        )

    def build_processing(self, *, url: str, task_id: int, commit_hexsha: str) -> None:
        self._publish_build(
            url=url,
            task_id=task_id,
            status=BuildStatus.PROCESSING,
            msg="Received and building",
            build_time=0,
            commit_hexsha=commit_hexsha,
        )

    def build_finished(
        self,
        *,
        url: str,
        task_id: int,
        status: BuildStatus,
        build_time: int,
        commit_hexsha: str,
        msg: str = "Build Process Finished",
    ) -> None:
        self._publish_build(
            url=url,
            task_id=task_id,
            status=status,
            msg=msg,
            build_time=build_time,
            commit_hexsha=commit_hexsha,
        )

    def binary(self, *, task_id: int, file_name: str) -> None:
        self._publisher.publish(
            BINARY,
            BinaryRecordMsg(task_id=task_id, file_name=file_name).model_dump_json(),
        )

    def _publish_build(
        self,
        *,
        url: str,
        task_id: int,
        status: BuildStatus,
        msg: str,
        build_time: int,
        commit_hexsha: str,
    ) -> None:
        self._publisher.publish(
            BUILD,
            BuildStatusMsg(
                url=url,
                opt_id=self._opt_id,
                status=status,
                msg=msg[-_MSG_LIMIT:],
                task_id=task_id,
                build_time=build_time,
                commit_hexsha=commit_hexsha,
            ).model_dump_json(),
        )
