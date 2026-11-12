import asyncio
import json
from tempfile import TemporaryDirectory
import unittest

from primitive_vla.contracts import Domain, Record
from primitive_vla.memory import Memory
from primitive_vla.memory.organizer import MemoryOrganizer
from primitive_vla.vlm import FunctionCall, VLMResponse


def proposal(sources, replace=()):
    return VLMResponse((FunctionCall("consolidate", {"notes": [{
        "title": "观察经验", "content": "# 适用条件\n动作效果仍需独立验证。",
        "source_ids": list(sources), "replace_ids": list(replace),
    }]}),))


class MemoryOrganizerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.memory = Memory(self.directory.name)

    def tearDown(self):
        self.memory.close()
        self.directory.cleanup()

    def note(self, content="观察", lifetime="long"):
        return self.memory.write(Record("s", "t", "planning", Domain.PLANNING, "note", "recorded",
                                        {"title": "经验", "content": content}, lifetime=lifetime))

    async def test_merges_notes_preserves_versions_and_skips_unchanged_input_after_restart(self):
        first, second = self.note("观察一"), self.note("观察二")

        class Model:
            calls = 0

            async def generate(inner, request):
                inner.calls += 1
                self.assertIn("观察一", request.text)
                return proposal((first.id, second.id), (first.id, second.id))

        model = Model()
        organizer = MemoryOrganizer(self.memory, model)
        result = await organizer.once()
        summary = self.memory.get(result["published"][0])
        self.assertEqual(summary.domain, Domain.PLANNING)
        self.assertEqual(summary.status, "synthesized")
        self.assertEqual(summary.payload["provenance"], "model_summary")
        self.assertEqual(set(summary.payload["source_versions"]), {first.id, second.id})
        self.assertEqual(len(list(self.memory.archive.glob("*.md"))), 2)
        self.assertEqual(self.memory.evidence(first.id), first)
        self.assertIn(summary.id, self.memory.read_index()["content"])
        reopened = Memory(self.directory.name)
        self.assertEqual((await MemoryOrganizer(reopened, model).once())["status"], "unchanged")
        self.assertEqual(model.calls, 1)

    async def test_unknown_source_is_rejected_before_any_publication(self):
        note = self.note()

        class Model:
            async def generate(inner, request):
                return proposal(("invented-source",), (note.id,))

        with self.assertRaises(ValueError):
            await MemoryOrganizer(self.memory, Model()).once()
        self.assertEqual(self.memory.search(), [note])
        self.assertEqual(list(self.memory.archive.glob("*.md")), [])

    async def test_raw_verification_cannot_be_replaced_by_a_model_summary(self):
        record = self.memory.write(Record("s", "t", "verify", Domain.VERIFICATION, "verification",
                                          "completed", {"conclusion": "failure"}, lifetime="long"))

        class Model:
            async def generate(inner, request):
                return proposal((record.id,), (record.id,))

        with self.assertRaisesRegex(ValueError, "raw evidence"):
            await MemoryOrganizer(self.memory, Model()).once()
        self.assertEqual(self.memory.get(record.id), record)

    async def test_summary_sources_resolve_to_the_version_actually_read(self):
        note = self.note("initial text")
        self.memory.update(note.id, {"payload": {"content": "updated text"}}, session_id="s")

        class Model:
            async def generate(inner, request):
                return proposal((note.id,))

        result = await MemoryOrganizer(self.memory, Model()).once()
        summary = self.memory.get(result["published"][0])
        version = summary.payload["source_versions"][note.id]
        self.memory.update(note.id, {"payload": {"content": "later text"}}, session_id="s")
        self.assertEqual(self.memory.evidence(note.id, version).payload["content"], "updated text")
        self.assertEqual(self.memory.evidence(note.id).payload["content"], "initial text")

    async def test_concurrent_update_or_delete_does_not_publish_a_stale_summary(self):
        for delete in (False, True):
            with self.subTest(delete=delete):
                note = self.note("old", lifetime="short" if delete else "long")
                started, release = asyncio.Event(), asyncio.Event()

                class Model:
                    async def generate(inner, request):
                        started.set()
                        await release.wait()
                        return proposal((note.id,))

                task = asyncio.create_task(MemoryOrganizer(self.memory, Model()).once())
                await asyncio.wait_for(started.wait(), 1)
                if delete:
                    self.memory.delete(note.id, session_id="s")
                else:
                    self.memory.update(note.id, {"payload": {"content": "new"}}, session_id="s")
                release.set()
                self.assertEqual((await task)["status"], "stale")
                self.assertEqual(self.memory.search(filters={"source": "memory.organizer"}), [])

    async def test_periodic_job_runs_and_stops_without_another_model_call(self):
        note = self.note()
        stop = asyncio.Event()

        class Model:
            calls = 0

            async def generate(inner, request):
                inner.calls += 1
                stop.set()
                return proposal((note.id,))

        model = Model()
        organizer = MemoryOrganizer(self.memory, model, interval=0.01)
        await asyncio.wait_for(organizer.run(stop), 1)
        self.assertEqual(model.calls, 1)
        self.assertEqual(organizer.last_result["status"], "completed")

    async def test_oversized_documents_do_not_trigger_unbounded_requests(self):
        self.note("large" * 10000)

        class Model:
            async def generate(inner, request):
                self.fail("oversized source should not be sent to the model")

        organizer = MemoryOrganizer(self.memory, Model(), context_chars=4096)
        self.assertEqual((await organizer.once())["status"], "context_budget")

    async def test_proposals_are_bounded_and_preserve_failed_evidence(self):
        failure = self.memory.write(Record("s", "t", "verify", Domain.VERIFICATION,
                                           "verification", "completed", {"conclusion": "failure"}))

        class Model:
            async def generate(inner, request):
                self.assertIn("failure", request.text)
                self.assertLessEqual(len(request.instructions) + len(request.text) +
                                     len(json.dumps(request.functions[0].parameters)), 4096)
                return proposal((failure.id,))

        result = await MemoryOrganizer(self.memory, Model(), context_chars=4096).once()
        self.assertEqual(len(result["published"]), 1)
        self.assertEqual(self.memory.evidence(failure.id).payload["conclusion"], "failure")
