"""Wire-format tests for mq/messages.py, driven by the golden fixtures in
tests/fixtures/messages/ (captured 2026-07-15 from the running system's
message classes — see the README there).

These are the compatibility contract for the typed-message rewrite:
- serialization must dict-equal the goldens (key order never matters; every
  consumer json.loads()es),
- parsing the goldens must reproduce the same wire form (round-trip),
- enum-valued fields must encode as the lowercase member VALUES,
- the scrape bundle must serialize as a BARE JSON ARRAY.
"""

import json
import unittest
from pathlib import Path
from typing import ClassVar

from assemblage import messages as typed
from assemblage.consts import BuildStatus, CloneStatus, ScraperOutputPolicy
from assemblage.mq.messages import (
    BinaryTaskMsgIn,
    BuilderRegIn,
    BuilderRegOut,
    BuilderTaskOut,
    BuildStatusMsgIn,
    CloneStatusMsgIn,
    ScraperControlTaskIn,
    ScraperControlTaskOut,
    ScraperDataOutBundle,
    ScraperDataOutSingle,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "messages"

# fixture file -> message class it must round-trip through
ROUNDTRIP = {
    "builder_reg_in": BuilderRegIn,
    "builder_reg_out": BuilderRegOut,
    "builder_task_out": BuilderTaskOut,
    "scraper_data_out_single": ScraperDataOutSingle,
    "scraper_data_out_bundle": ScraperDataOutBundle,
    "clone_status_msg_in": CloneStatusMsgIn,
    "build_status_msg_in": BuildStatusMsgIn,
    "binary_task_msg_in": BinaryTaskMsgIn,
    "scraper_control_task_in": ScraperControlTaskIn,
}


def golden(name: str) -> str:
    return (FIXTURES / f"{name}.json").read_text()


class TestMessage(unittest.TestCase):
    def test_roundtrip_dict_equality(self):
        """from_json(golden).to_json() must dict-equal the golden."""
        for name, cls in ROUNDTRIP.items():
            with self.subTest(message=name):
                wire = golden(name)
                parsed = cls.from_json(wire)
                self.assertEqual(
                    json.loads(parsed.to_json()),
                    json.loads(wire),
                    f"{cls.__name__} does not round-trip its golden wire form",
                )

    def test_bundle_is_bare_json_array(self):
        wire = golden("scraper_data_out_bundle")
        decoded = json.loads(wire)
        self.assertIsInstance(decoded, list, "scrape bundle must be a bare JSON array")
        self.assertEqual(len(decoded), 2)
        bundle = ScraperDataOutBundle.from_json(wire)
        self.assertEqual(len(bundle), 2)
        self.assertEqual(json.loads(bundle.to_json()), decoded)

    def test_enum_fields_encode_as_lowercase_values(self):
        enums = json.loads(golden("enum_wire_values"))
        self.assertEqual(enums["BuildStatus"]["SUCCESS"], "success")
        self.assertEqual(enums["CloneStatus"]["NOT_STARTED"], "not_started")
        # live encoding still matches the frozen table
        for enum_cls, table in (
            (BuildStatus, enums["BuildStatus"]),
            (CloneStatus, enums["CloneStatus"]),
        ):
            for member_name, wire_value in table.items():
                self.assertEqual(enum_cls[member_name].value, wire_value)
        # and an enum inside a message serializes as its value
        msg = CloneStatusMsgIn(url="u", opt_id=1, status=CloneStatus.SUCCESS, msg="m", task_id=2)
        self.assertEqual(json.loads(msg.to_json())["status"], "success")

    def test_clone_status_parses_through_buildstatus(self):
        """The coordinator parses clone messages via BuildStatus(msg.status).
        That only works because the two enums share value strings — pin it."""
        shared = {"processing", "failed", "success"}
        build_values = {m.value for m in BuildStatus}
        clone_values = {m.value for m in CloneStatus}
        self.assertTrue(shared <= (build_values & clone_values))

    def test_unknown_keys_are_tolerated(self):
        """Old peers may send fields the current code dropped; parsing must
        not raise (BuilderTaskOut and friends take **kwargs today)."""
        wire = json.loads(golden("builder_task_out"))
        wire["some_future_field"] = 123
        parsed = BuilderTaskOut.from_json(json.dumps(wire))
        self.assertEqual(parsed.task_id, 42)

    def test_scraper_control_out_matches_goldens(self):
        for name in ("scraper_control_task_out_setup", "scraper_control_task_out_request"):
            with self.subTest(fixture=name):
                wire = json.loads(golden(name))
                rebuilt = ScraperControlTaskOut(**wire)
                self.assertEqual(json.loads(rebuilt.to_json()), wire)

    def test_scraper_single_to_dict_matches_golden(self):
        wire = golden("scraper_data_out_single")
        parsed = ScraperDataOutSingle.from_json(wire)
        self.assertEqual(parsed.to_dict(), json.loads(wire))


class TestTypedMessages(unittest.TestCase):
    """The same golden-fixture contract, enforced on the pydantic-v2 rewrite
    (assemblage.messages). Both message layers coexist until P8."""

    # fixture -> new model; models that map 1:1 to a golden's key set
    ROUNDTRIP: ClassVar[dict[str, type]] = {
        "scraper_data_out_single": typed.RepoRecord,
        "clone_status_msg_in": typed.CloneStatusMsg,
        "build_status_msg_in": typed.BuildStatusMsg,
        "binary_task_msg_in": typed.BinaryRecordMsg,
        "builder_reg_in": typed.BuilderRegistration,
        "builder_reg_out": typed.BuilderRegistered,
        "scraper_control_task_in": typed.ScraperControlRequest,
        "scraper_control_task_out_setup": typed.ScraperControlReply,
        "scraper_control_task_out_request": typed.ScraperControlReply,
    }

    def test_roundtrip_dict_equality(self):
        for name, cls in self.ROUNDTRIP.items():
            with self.subTest(message=name):
                wire = golden(name)
                parsed = cls.model_validate_json(wire)
                self.assertEqual(
                    json.loads(parsed.model_dump_json()),
                    json.loads(wire),
                    f"{cls.__name__} does not round-trip its golden wire form",
                )

    def test_bundle_is_bare_json_array(self):
        wire = golden("scraper_data_out_bundle")
        bundle = typed.ScrapeBundle.model_validate_json(wire)
        dumped = json.loads(bundle.model_dump_json())
        self.assertIsInstance(dumped, list)
        self.assertEqual(len(bundle), 2)
        self.assertEqual(dumped, json.loads(wire))

    def test_build_task_drops_dead_fields(self):
        """BuildTask parses the golden but drops the write-only
        output_dir / mod_timestamp keys (receivers ignore missing keys)."""
        wire = json.loads(golden("builder_task_out"))
        task = typed.BuildTask.model_validate_json(golden("builder_task_out"))
        dumped = json.loads(task.model_dump_json())
        expected = {k: v for k, v in wire.items() if k not in ("output_dir", "mod_timestamp")}
        self.assertEqual(dumped, expected)
        self.assertNotIn("output_dir", dumped)
        self.assertNotIn("mod_timestamp", dumped)

    def test_enum_fields_serialize_as_lowercase_values(self):
        msg = typed.CloneStatusMsg(
            url="u", opt_id=1, status=CloneStatus.SUCCESS, msg="m", task_id=2
        )
        self.assertEqual(json.loads(msg.model_dump_json())["status"], "success")
        reply = typed.ScraperControlReply(
            message_type="setup", policy=ScraperOutputPolicy.CONTINUOUS
        )
        self.assertEqual(json.loads(reply.model_dump_json())["policy"], "continuous")

    def test_registered_defaults_queue_from_id(self):
        reg = typed.BuilderRegistered(build_opt_id=7)
        self.assertEqual(reg.build_opt_queue, "build_opt_7")

    def test_unknown_keys_are_ignored(self):
        wire = json.loads(golden("builder_reg_in"))
        wire["some_future_field"] = 123
        parsed = typed.BuilderRegistration.model_validate_json(json.dumps(wire))
        self.assertEqual(parsed.name, "gcc-O2")

    def test_reply_qualifiers_carried(self):
        """Unlike the old ScraperControlTaskOut (which discarded qualifiers),
        the reply model actually carries them."""
        reply = typed.ScraperControlReply(message_type="request_repos", qualifiers=["language:c++"])
        self.assertEqual(json.loads(reply.model_dump_json())["qualifiers"], ["language:c++"])


if __name__ == "__main__":
    unittest.main()
