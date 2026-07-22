"""Unit tests for the re-architected builder package.

Covers the pure/decomposed pieces: source URL parsing, the frozen metadata key
set, the reporter's wire messages, the pipeline's message ordering with fake
deps, and the app's task handler (ack-early + 1000-task recycle).
"""

import json
import logging
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from assemblage.builder import artifacts, pipeline, source
from assemblage.builder.app import BuilderApp
from assemblage.builder.report import BuildReporter
from assemblage.enums import BuildStatus, CloneStatus
from assemblage.messages import BuildTask
from assemblage.mq.consumer import AckDecision, IncomingMessage
from assemblage.settings import BuilderSettings

from tests.constants import TEST_MESSAGE_LEVEL

logging.basicConfig(level=TEST_MESSAGE_LEVEL)
logger = logging.getLogger(__name__)

_BUILDER_ENV = {"compiler": "gcc", "language": "c++"}


def _fake_strategy():
    return SimpleNamespace(
        platform="linux",
        build_mode="RelWithDebInfo",
        compiler="gcc",
        compiler_version="13.2.0",
        language="c++",
    )


def _task():
    return BuildTask(
        name="hello-make",
        url="file:///e2e/hello-make",
        task_id=7,
        opt_id=3,
        repo_id=11,
        updated_at="01/01/2026, 00:00:00",
        build_system="make",
        msg_time=0.0,
        commit_hexsha="",
        compiler_flag="-O0",
    )


class TestParseGithubName(unittest.TestCase):
    def test_https(self):
        self.assertEqual(
            source.parse_github_name("https://github.com/torvalds/linux"),
            ("torvalds", "linux"),
        )

    def test_https_dot_git(self):
        self.assertEqual(
            source.parse_github_name("https://github.com/torvalds/linux.git"),
            ("torvalds", "linux"),
        )

    def test_git_at(self):
        self.assertEqual(
            source.parse_github_name("git@github.com:torvalds/linux.git"),
            ("torvalds", "linux"),
        )

    def test_file_url(self):
        # The E2E convention: first two path segments -> user=e2e, project=repo.
        self.assertEqual(
            source.parse_github_name("file:///e2e/hello-make"),
            ("e2e", "hello-make"),
        )

    def test_too_short(self):
        self.assertEqual(source.parse_github_name("https://example.com/x"), (None, None))


class TestGenerateMetadata(unittest.TestCase):
    def test_frozen_key_set_and_values(self):
        meta = artifacts.generate_metadata(
            strategy=_fake_strategy(),
            library="x64",
            task=_task(),
            commit_hexsha="0123456789ab",
            compiler_flag="-O0",
        )
        self.assertEqual(
            set(meta.keys()),
            {
                "Platform",
                "Build_mode",
                "Compiler",
                "Compiler_version",
                "URL",
                "Commit",
                "Optimization",
                "Pushed_at",
                "compiler_flag",
                "language",
                "library",
            },
        )
        self.assertEqual(meta["Platform"], "linux")
        self.assertEqual(meta["Build_mode"], "RelWithDebInfo")
        self.assertEqual(meta["Compiler"], "gcc")
        self.assertEqual(meta["Optimization"], "-O0")
        self.assertEqual(meta["Commit"], "0123456789ab")
        self.assertEqual(meta["URL"], "file:///e2e/hello-make")
        self.assertEqual(meta["Pushed_at"], "01/01/2026, 00:00:00")
        self.assertEqual(meta["library"], "x64")
        self.assertEqual(meta["language"], "c++")


class _FakePublisher:
    def __init__(self):
        self.published: list[tuple[str, dict]] = []

    def publish(self, queue, body, **_kwargs):
        self.published.append((queue.name, json.loads(body)))


class TestReporter(unittest.TestCase):
    def setUp(self):
        self.publisher = _FakePublisher()
        self.reporter = BuildReporter(self.publisher, opt_id=3, uuid="abcdef0123")

    def test_clone_status_wire(self):
        self.reporter.clone_status(
            url="file:///e2e/hello-make", status=CloneStatus.SUCCESS, msg="ok", task_id=7
        )
        queue, body = self.publisher.published[0]
        self.assertEqual(queue, "clone")
        self.assertEqual(
            body,
            {
                "url": "file:///e2e/hello-make",
                "opt_id": 3,
                "status": "success",
                "msg": "ok",
                "task_id": 7,
            },
        )

    def test_build_processing_wire(self):
        self.reporter.build_processing(
            url="file:///e2e/hello-make", task_id=7, commit_hexsha="0123456789ab"
        )
        queue, body = self.publisher.published[0]
        self.assertEqual(queue, "build")
        self.assertEqual(body["status"], "processing")
        self.assertEqual(body["build_time"], 0)
        self.assertEqual(body["commit_hexsha"], "0123456789ab")
        self.assertEqual(body["opt_id"], 3)

    def test_build_finished_wire(self):
        self.reporter.build_finished(
            url="u", task_id=7, status=BuildStatus.SUCCESS, build_time=42, commit_hexsha="c"
        )
        queue, body = self.publisher.published[0]
        self.assertEqual(queue, "build")
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["build_time"], 42)

    def test_binary_wire(self):
        self.reporter.binary(task_id=7, file_name="/tmp/projects/e2e/hello-make/hello")
        queue, body = self.publisher.published[0]
        self.assertEqual(queue, "binary")
        self.assertEqual(body, {"task_id": 7, "file_name": "/tmp/projects/e2e/hello-make/hello"})


class _RecordingReporter:
    def __init__(self):
        self.calls: list[str] = []

    def clone_status(self, **_kw):
        self.calls.append("clone_status")

    def build_processing(self, **_kw):
        self.calls.append("build_processing")

    def build_finished(self, **_kw):
        self.calls.append("build_finished")

    def binary(self, **_kw):
        self.calls.append("binary")


class TestPipelineOrdering(unittest.TestCase):
    def _ctx(self, reporter):
        strategy = SimpleNamespace(
            compiler="gcc",
            prepare=lambda clone_dir, flag: None,
            build=lambda clone_dir, flag, prepared: ("build output", BuildStatus.SUCCESS),
            debug_info=lambda clone_dir, originals: [],
        )
        return pipeline.BuildContext(
            strategy=strategy,
            reporter=reporter,
            compiler_flag="-O0",
            library="x64",
            uuid="abcde12345",
            binaries_root="/binaries",
            project_bucket=None,
            artifact_bucket=None,
        )

    def _source(self, status):
        return source.SourceResult(
            status=status,
            message="cloned",
            clone_dir="/tmp/projects/e2e/hello-make",
            commit_hexsha="0123456789ab",
            restored_from_s3=False,
            original_files=[],
            save_path=None,
        )

    def test_success_message_order(self):
        reporter = _RecordingReporter()
        ctx = self._ctx(reporter)
        with (
            patch.object(
                pipeline, "acquire_source", return_value=self._source(CloneStatus.SUCCESS)
            ),
            patch.object(pipeline, "generate_metadata", return_value={}),
            patch.object(pipeline, "save_metadata_locally", return_value=None),
            patch.object(
                pipeline,
                "save_binaries",
                return_value=artifacts.SavedBinaries("/binaries/successes/x", True, ["/x/hello"]),
            ),
        ):
            pipeline.run_task(ctx, _task())
        self.assertEqual(
            reporter.calls, ["clone_status", "build_processing", "binary", "build_finished"]
        )

    def test_clone_failure_short_circuits(self):
        reporter = _RecordingReporter()
        ctx = self._ctx(reporter)
        with patch.object(
            pipeline, "acquire_source", return_value=self._source(CloneStatus.FAILED)
        ):
            pipeline.run_task(ctx, _task())
        self.assertEqual(reporter.calls, ["clone_status"])


class TestBuilderAppHandler(unittest.TestCase):
    def _app(self):
        with (
            patch.dict(os.environ, _BUILDER_ENV, clear=False),
            patch("assemblage.builder.app.make_strategy", return_value=_fake_strategy()),
        ):
            return BuilderApp(BuilderSettings())

    def _incoming(self):
        return IncomingMessage(
            body=_task().model_dump_json().encode(),
            correlation_id=None,
            reply_to=None,
            redelivered=False,
            routing_key="build_opt_3",
        )

    def test_handler_acks_and_recycles_at_1000(self):
        app = self._app()
        app._processed = 999
        seen: list[BuildTask] = []
        with (
            patch(
                "assemblage.builder.app.run_task",
                side_effect=lambda ctx, task: seen.append(task),
            ),
            patch.object(app._supervisor, "request_shutdown") as shutdown,
        ):
            handler = app._make_handler(app._make_context())
            decision = handler(self._incoming())
        self.assertEqual(decision, AckDecision.ACK)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].task_id, 7)
        shutdown.assert_called_once_with(0)

    def test_handler_no_recycle_before_1000(self):
        app = self._app()
        app._processed = 0
        with (
            patch("assemblage.builder.app.run_task", side_effect=lambda ctx, task: None),
            patch.object(app._supervisor, "request_shutdown") as shutdown,
        ):
            handler = app._make_handler(app._make_context())
            handler(self._incoming())
        shutdown.assert_not_called()
        self.assertEqual(app._processed, 1)


class TestSweepStaleScratch(unittest.TestCase):
    """A restarted builder must reclaim scratch orphaned by a SIGKILLed build.

    SIGKILL (build timeout, 1000-task recycle, storm) skips pipeline.py's finally
    cleanup, and docker restart preserves the layer -- so orphaned target dirs pile
    up (783 GB measured 2026-07-21) until a fresh builder wipes them at startup.
    """

    def _projects(self, base):
        p = os.path.join(base, "projects")
        os.makedirs(p)
        return p

    def test_removes_all_leftover_owner_dirs(self):
        import tempfile

        from assemblage.builder.app import _sweep_stale_scratch

        with tempfile.TemporaryDirectory() as base:
            projects = self._projects(base)
            for owner in ("alice", "bob"):
                d = os.path.join(projects, owner, "repo", "target")
                os.makedirs(d)
                with open(os.path.join(d, "big.rlib"), "w") as fh:
                    fh.write("x" * 1000)
            _sweep_stale_scratch(base)
            # the projects dir itself survives; everything under it is gone
            self.assertTrue(os.path.isdir(projects))
            self.assertEqual(os.listdir(projects), [])

    def test_missing_projects_dir_is_not_an_error(self):
        import tempfile

        from assemblage.builder.app import _sweep_stale_scratch

        with tempfile.TemporaryDirectory() as base:
            _sweep_stale_scratch(base)  # no projects/ yet -- must not raise

    def test_runs_before_registration_at_startup(self):
        """The sweep must fire on run() even if registration later fails."""
        from assemblage.builder.app import BuilderApp

        with patch.dict(os.environ, _BUILDER_ENV, clear=False):
            app = BuilderApp(BuilderSettings())
        with (
            patch("assemblage.builder.app._sweep_stale_scratch") as sweep,
            patch.object(app, "_register", return_value=False),
        ):
            app.run()
        sweep.assert_called_once_with(app._strategy.base_path)


if __name__ == "__main__":
    unittest.main()
