"""Wire-format tests for assemblage.messages, driven by the golden fixtures in
tests/fixtures/messages/ (captured 2026-07-15 from the pre-re-architecture
message classes — see the README there).

These are the compatibility contract for the typed-message layer:
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
from assemblage.enums import BuildStatus, CloneStatus, ScraperOutputPolicy

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "messages"


def golden(name: str) -> str:
    return (FIXTURES / f"{name}.json").read_text()


class TestTypedMessages(unittest.TestCase):
    """The golden-fixture contract, enforced on the pydantic-v2 messages."""

    # fixture -> model; models that map 1:1 to a golden's key set
    ROUNDTRIP: ClassVar[dict[str, type]] = {
        "scraper_data_out_single": typed.RepoRecord,
        "clone_status_msg_in": typed.CloneStatusMsg,
        "build_status_msg_in": typed.BuildStatusMsg,
        "binary_task_msg_in": typed.BinaryRecordMsg,
        "builder_reg_v2": typed.BuilderRegistration,
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

    def test_clone_status_parses_through_buildstatus(self):
        """The coordinator parses clone messages via BuildStatus(msg.status).
        That only works because the two enums share value strings — pin it."""
        shared = {"processing", "failed", "success"}
        build_values = {m.value for m in BuildStatus}
        clone_values = {m.value for m in CloneStatus}
        self.assertTrue(shared <= (build_values & clone_values))

    def test_registered_defaults_queue_from_id(self):
        reg = typed.BuilderRegistered(build_opt_id=7)
        self.assertEqual(reg.build_opt_queue, "build_opt_7")

    def test_builder_reg_v1_still_parses_with_defaults(self):
        """The 2026-07-16 sanctioned evolution added codegen_backend/build_mode
        to BuilderRegistration. The frozen v1 golden is the backward-compat
        contract: it must still parse, the new fields must take their defaults,
        and re-serializing must be exactly v1 + the two default-valued keys
        (mixed-era queues: old producers, new consumers)."""
        wire = golden("builder_reg_in")
        parsed = typed.BuilderRegistration.model_validate_json(wire)
        self.assertEqual(parsed.codegen_backend, "")
        self.assertEqual(parsed.build_mode, "RelWithDebInfo")
        expected = json.loads(wire) | {"codegen_backend": "", "build_mode": "RelWithDebInfo"}
        self.assertEqual(json.loads(parsed.model_dump_json()), expected)

    def test_builder_reg_v2_rust_fields(self):
        """The v2 golden carries a Rust registration end to end."""
        parsed = typed.BuilderRegistration.model_validate_json(golden("builder_reg_v2"))
        self.assertEqual(parsed.compiler, "rustc")
        self.assertEqual(parsed.language, "rust")
        self.assertEqual(parsed.codegen_backend, "llvm")
        self.assertEqual(parsed.build_mode, "RelWithDebInfo")

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
