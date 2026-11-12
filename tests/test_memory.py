from tempfile import TemporaryDirectory
from pathlib import Path
import time
import unittest

from primitive_vla.contracts import Domain, Record, ResourceRef, ToolError
from primitive_vla.memory import Memory


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.memory = Memory(self.directory.name)

    def tearDown(self):
        self.memory.close()
        self.directory.cleanup()

    def test_long_term_records_survive_reopen_and_queries_keep_provenance(self):
        record = self.memory.write(Record("s1", "t", "map", Domain.STATE, "map", "available",
                                          {"地点": "B点"}, lifetime="long"))
        self.memory.close()
        self.memory = Memory(self.directory.name)
        self.assertEqual(self.memory.search("B点", {"lifetime": "long"})[0].id, record.id)
        self.assertEqual(self.memory.search(filters={"session_id": "s2"}), [])
        with self.assertRaises(ValueError):
            self.memory.update(record.id, {"source": "verification"})

    def test_expired_observations_disappear_and_cleanup_preserves_evidence(self):
        observation = self.memory.write(Record("s", "t", "camera", Domain.STATE, "observation", "observed",
                                               expires_at=time.time() - 1))
        verified = self.memory.write(Record("s", "t", "verify", Domain.VERIFICATION, "verification", "completed"))
        self.assertEqual([r.id for r in self.memory.search()], [verified.id])
        with self.assertRaises(KeyError):
            self.memory.get(observation.id)
        self.assertEqual(self.memory.cleanup(), 1)
        self.assertEqual(self.memory.get(verified.id).domain, Domain.VERIFICATION)

    def test_resources_are_separate_and_external_uris_are_not_fetched(self):
        ref = self.memory.put_resource(b"binary image data", "image/png", "test frame")
        record = self.memory.write(Record("s", "t", "camera", Domain.STATE, "observation", "observed",
                                          resource_refs=(ref,)))
        self.assertEqual(self.memory.read_resource(ref.id), b"binary image data")
        self.assertNotIn("binary image data", str(record.to_dict()))
        remote = ResourceRef("https://example.invalid/map", "application/octet-stream")
        self.memory.register_resource(remote)
        self.assertEqual(self.memory.get_resource(remote.id), remote)
        with self.assertRaises(ValueError):
            self.memory.read_resource(remote.id)

    def test_generic_payloads_remain_generic(self):
        record = self.memory.write(Record("s", "t", "execution.test", Domain.EXECUTION,
                                          "tool_finished", "completed", {"duration_ms": 10}))
        self.assertEqual(self.memory.get(record.id).payload, {"duration_ms": 10})
        self.assertNotIn("holding_object", self.memory.get(record.id).to_dict())

    def test_search_is_literal_and_paginated(self):
        for number in range(4):
            self.memory.write(Record("s", "t", "test", Domain.PLANNING, "note", "recorded",
                                     {"text": "literal %_", "number": number}))
        self.assertEqual(len(self.memory.search("%_", limit=2, offset=2)), 2)
        self.assertEqual(self.memory.search("' OR 1=1 --"), [])
        with self.assertRaises(ValueError):
            self.memory.search(filters={"bad_column": "x"})

    def test_markdown_is_readable_and_is_the_working_source_of_truth(self):
        note = self.memory.write(Record("s", "t", "planning", Domain.PLANNING, "note", "recorded",
                                       {"title": "抓取经验", "content": "# 观察\n\n先检查视角。"}, lifetime="long"))
        path = Path(self.directory.name) / "long" / f"{note.id}.md"
        self.assertIn("# 观察\n\n先检查视角。", path.read_text())
        self.assertIn(note.id, self.memory.read_index()["content"])
        path.write_text(path.read_text().replace("先检查视角。", "重新获取图像。"))
        self.assertIn("重新获取图像。", self.memory.get(note.id).payload["content"])
        self.assertEqual(self.memory.read(note.id)["format"], "markdown")
        self.assertIn("先检查视角。", self.memory.evidence(note.id).payload["content"])
        self.assertFalse((Path(self.directory.name) / "memory.sqlite3").exists())

    def test_delete_short_memory_preserves_recovery_evidence_and_resources(self):
        ref = self.memory.put_resource(b"frame", "image/png")
        record = self.memory.write(Record("s", "t", "execution.move", Domain.EXECUTION,
                                          "tool_started", "running", {"robot_id": "r", "required_resources": ["base"]},
                                          call_id="call-1", resource_refs=(ref,)))
        self.memory.delete(record.id, session_id="s")
        self.memory = Memory(self.directory.name)
        with self.assertRaises(KeyError):
            self.memory.get(record.id)
        self.assertEqual(self.memory.search(), [])
        self.assertEqual(self.memory.unfinished_calls()[0].call_id, "call-1")
        self.assertEqual(self.memory.evidence(record.id), record)
        self.assertEqual(self.memory.read_resource(ref.id), b"frame")

    def test_delete_scope_does_not_include_other_sessions_or_long_term(self):
        for session, lifetime in (("another", "short"), ("s", "long")):
            record = self.memory.write(Record(session, "t", "planning", Domain.PLANNING,
                                              "note", "recorded", {"content": "keep"}, lifetime=lifetime))
            with self.assertRaises(ToolError):
                self.memory.delete(record.id, session_id="s")
            self.assertEqual(self.memory.get(record.id), record)

    def test_file_identifiers_cannot_escape_workspace(self):
        for operation in (self.memory.get, self.memory.evidence, self.memory.get_resource, self.memory.load_session):
            with self.assertRaises(ValueError):
                operation("../outside")
