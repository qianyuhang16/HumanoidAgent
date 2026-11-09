from tempfile import TemporaryDirectory
import time
import unittest

from primitive_vla.contracts import Domain, Record, ResourceRef
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
