"""Builder composition root: register, then consume and build.

``BuilderApp`` performs the registration handshake (declare a private control
queue, publish a :class:`BuilderRegistration`, wait on a short-lived
:class:`ConsumerLoop` for the coordinator's :class:`BuilderRegistered` reply,
bounded by a 5-minute deadline), then runs one task :class:`ConsumerLoop` on the
assigned ``build_opt_{id}`` queue under a :class:`Supervisor`. The task loop acks
**before** building (``ack_early=True``) — the load-bearing at-most-once dispatch
semantics — and the process recycles itself via ``request_shutdown(0)`` after
1000 tasks instead of the old ``os._exit``. SIGTERM handling lives in the
supervisor, not an import-time handler.
"""

import logging
import threading
import uuid
from collections.abc import Callable

from assemblage.build.strategy import make_strategy
from assemblage.builder.pipeline import BuildContext, run_task
from assemblage.builder.report import BuildReporter
from assemblage.enums import SupportedLanguage
from assemblage.messages import BuilderRegistered, BuilderRegistration, BuildTask
from assemblage.mq.connection import ConnectionFactory
from assemblage.mq.consumer import AckDecision, ConsumerLoop, IncomingMessage
from assemblage.mq.publisher import Publisher
from assemblage.mq.topology import BUILDER_REG, QueueSpec, build_opt_queue, builder_ctrl_queue
from assemblage.runtime.supervisor import Supervisor
from assemblage.settings import BuilderSettings
from assemblage.storage.layout import ARTIFACTS_BUCKET, PROJECT_ARCHIVE_BUCKET
from assemblage.storage.s3 import S3Bucket, S3Client

logger = logging.getLogger(__name__)

_RECYCLE_AFTER_TASKS = 1000

Handler = Callable[[IncomingMessage], AckDecision]


class BuilderApp:
    """Builds and runs the builder's registration + task-consumption services."""

    def __init__(self, settings: BuilderSettings) -> None:
        self._settings = settings
        self._uuid = str(uuid.uuid1())
        self._factory = ConnectionFactory(settings.mq)
        self._supervisor = Supervisor()
        self._strategy = make_strategy(settings)
        self._project_bucket, self._artifact_bucket = self._make_buckets()
        self._processed = 0
        self._opt_id = 0
        self._build_opt_queue = ""

    def _make_buckets(self) -> tuple[S3Bucket | None, S3Bucket | None]:
        s3 = self._settings.s3
        if not s3.enabled:
            return None, None
        assert s3.host and s3.access_key and s3.secret_access_key  # enforced by S3Settings
        client = S3Client(
            host=s3.host,
            access_key=s3.access_key,
            port=s3.port,
            secret_access_key=s3.secret_access_key,
            region_name=s3.region,
            https=s3.https,
        )
        return S3Bucket(client, PROJECT_ARCHIVE_BUCKET), S3Bucket(client, ARTIFACTS_BUCKET)

    # --- registration ---------------------------------------------------------

    def _register(self) -> bool:
        """Handshake with the coordinator; record the assigned opt id and queue."""
        ctrl = builder_ctrl_queue(self._uuid)
        self._declare_ctrl(ctrl)

        done = threading.Event()

        def handler(incoming: IncomingMessage) -> AckDecision:
            if incoming.correlation_id != self._uuid:
                return AckDecision.REQUEUE
            reg = BuilderRegistered.model_validate_json(incoming.body)
            self._opt_id = reg.build_opt_id
            self._build_opt_queue = reg.build_opt_queue or build_opt_queue(reg.build_opt_id).name
            done.set()
            return AckDecision.ACK

        stop = threading.Event()
        loop = ConsumerLoop("builder-ctrl", self._factory, ctrl, handler)
        thread = threading.Thread(target=loop.run, args=(stop,), name="builder-ctrl", daemon=True)
        thread.start()

        self._publish_registration(ctrl.name)

        deadline_s = self._settings.wait_for_build_opt_minutes * 60
        registered = done.wait(deadline_s)
        stop.set()
        loop.request_stop()
        thread.join(timeout=10)

        if not registered:
            logger.error("builder registration timed out after %ds", deadline_s)
            return False
        logger.info("builder registered: opt_id=%d queue=%s", self._opt_id, self._build_opt_queue)
        return True

    def _declare_ctrl(self, ctrl: QueueSpec) -> None:
        """Declare the private control queue before the coordinator can reply to it."""
        connection = self._factory.open(name=f"builder-declare-{self._uuid}")
        try:
            channel = connection.channel()
            channel.queue_declare(
                queue=ctrl.name, durable=ctrl.durable, auto_delete=ctrl.auto_delete
            )
        finally:
            connection.close()

    def _publish_registration(self, reply_to: str) -> None:
        publisher = Publisher(f"builder-reg-{self._uuid}", self._factory)
        try:
            is_rust = self._settings.language == SupportedLanguage.RUST
            registration = BuilderRegistration(
                name=self._settings.name,
                uuid=self._uuid,
                compiler=str(self._settings.compiler),
                library=str(self._settings.library),
                language=str(self._settings.language),
                platform=str(self._settings.build_os),
                compiler_flag=self._settings.compiler_flag,
                build_command="",
                build_system="all",
                codegen_backend=str(self._settings.codegen_backend) if is_rust else "",
                build_mode=self._strategy.build_mode,
            )
            publisher.publish(
                BUILDER_REG,
                registration.model_dump_json(),
                correlation_id=self._uuid,
                reply_to=reply_to,
            )
            logger.info("registration published (reply_to=%s)", reply_to)
        finally:
            publisher.close()

    # --- task consumption -----------------------------------------------------

    def _make_context(self) -> BuildContext:
        reporter = BuildReporter(
            Publisher(f"builder-report-{self._uuid}", self._factory), self._opt_id, self._uuid
        )
        return BuildContext(
            strategy=self._strategy,
            reporter=reporter,
            compiler_flag=self._settings.compiler_flag,
            library=str(self._settings.library),
            uuid=self._uuid,
            binaries_root=self._settings.binaries_root,
            project_bucket=self._project_bucket,
            artifact_bucket=self._artifact_bucket,
        )

    def _make_handler(self, ctx: BuildContext) -> Handler:
        def handle(incoming: IncomingMessage) -> AckDecision:
            task = BuildTask.model_validate_json(incoming.body)
            run_task(ctx, task)
            self._processed += 1
            if self._processed >= _RECYCLE_AFTER_TASKS:
                logger.info(
                    "builder %s reached %d tasks, recycling", self._uuid[:5], self._processed
                )
                self._supervisor.request_shutdown(0)
            return AckDecision.ACK  # ignored under ack_early

        return handle

    def run(self) -> int:
        """Register, then consume build tasks until signalled or recycled."""
        logger.info("builder %s starting", self._uuid)
        if not self._register():
            return 1

        ctx = self._make_context()
        queue = build_opt_queue(self._opt_id)
        consumer = ConsumerLoop(
            "builder-task", self._factory, queue, self._make_handler(ctx), ack_early=True
        )
        self._supervisor.add(consumer)
        logger.info("builder consuming %s", queue.name)
        return self._supervisor.run_until_signal()


def main() -> int:
    settings = BuilderSettings()
    logging.basicConfig(
        format="%(asctime)s %(levelname)s:%(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=settings.log_level,
    )
    return BuilderApp(settings).run()
