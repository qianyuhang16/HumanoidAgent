"""Real Agent/ModelPlanner/Router/Memory and a real local stdio Python platform.

Only the VLM and the explicitly synthetic external environment are substitutes.
No tests have been executed by the implementation author.
"""
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import urllib.request

from primitive_vla.contracts import Domain, Record
from primitive_vla.tools.assembly import PlatformPackage
from primitive_vla.vlm import FunctionCall, VLMResponse

from examples.chen_taskaware.host import build
from examples.chen_taskaware.legacy.evidence import digest
from examples.chen_taskaware.legacy.ledger import Ledger, RevisionError, validate_task

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parents[1]
_GUARDS = []


def forbidden(*args, **kwargs):
    raise AssertionError("network is forbidden in the offline integration suite")


def setUpModule():
    for target in ("socket.create_connection", "urllib.request.urlopen",
                   "urllib.request.OpenerDirector.open"):
        guard = patch(target, side_effect=forbidden)
        guard.start()
        _GUARDS.append(guard)


def tearDownModule():
    for guard in reversed(_GUARDS):
        guard.stop()


def task_fixture():
    return {
        "task_id": "synthetic-two-items", "instruction": "Place both declared items in the box.",
        "scene_version": "R0", "map_version": "synthetic-map",
        "objects": [{"object_id": i, "label": label} for i, label in
                    (("a", "item A"), ("b", "item B"), ("box", "open box"),
                     ("protected", "protected distractor"))],
        "goals": [
            {"goal_id": "goal_a", "object_id": "a", "container_id": "box",
             "subject": "in(a,box)", "li_item": "甲", "visit": 1},
            {"goal_id": "goal_b", "object_id": "b", "container_id": "box",
             "subject": "in(b,box)", "li_item": "乙", "visit": 2},
        ],
        "protected_object_ids": ["protected"], "movable_object_ids": ["a", "b"],
        "retain_on_publish": {"goal_b": ["goal_a"]}, "max_evidence_age_s": 300,
        "constraints": ["Do not move the protected distractor."],
    }


class ScriptedVLM:
    """Synthetic decisions for tests only; always traverses the real ModelPlanner.

    A terminal non-success Verification (failure/uncertain) ends the task as failed:
    re-judging the same observation cannot change Li's answer and is never attempted.
    """
    def __init__(self, pause_at_r1=False):
        self.pause_at_r1 = pause_at_r1
        self.asked = False
        self.contexts = []
        self.verified_observations = set()

    async def generate(self, request):
        data = json.loads(request.text)
        self.contexts.append(data)
        state = data["task_scene"]
        if self.pause_at_r1 and state["scene"]["ordinal"] == 1 and not self.asked:
            self.asked = True
            return self.action("ask_user", question="Synthetic checkpoint: continue item B?")
        if not state["remaining"]:
            return self.action("finish", outcome="success", message="Synthetic effects verified.")
        goal = next(g for g in state["goals"] if g["goal_id"] == state["remaining"][0])
        if goal["verification_call_id"] is not None:
            return self.action(
                "finish", outcome="failed",
                message="Independent Verification concluded "
                        + str(goal["verification_conclusion"])
                        + "; the goal is not advanced and identical evidence is not re-judged.")
        args = {"external_task_id": state["external_task_id"],
                "scene_revision": state["scene"]["revision"], "goal_id": goal["goal_id"],
                "visit": goal["visit"], "attempt": goal["attempt"]}
        if goal["placement_call_id"] is None:
            name = "execution.chen.place"
        elif goal["observation_call_id"] is None:
            name = "state.chen.observe"
            args["related_call_id"] = goal["placement_call_id"]
        else:
            if goal["observation_call_id"] in self.verified_observations:
                raise AssertionError("scripted VLM would re-verify the same observation")
            self.verified_observations.add(goal["observation_call_id"])
            name = "verification.chen.verify"
            args.update(related_call_id=goal["placement_call_id"],
                        observation_call_id=goal["observation_call_id"])
        return self.action("call_tool", name=name, arguments=args)

    @staticmethod
    def action(action, **arguments):
        return VLMResponse(calls=(FunctionCall(action, arguments),))


class RealStdioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.task = task_fixture()
        # Preserve the parent's guard path; never replace its PYTHONPATH.
        paths = [os.environ.get("PYTHONPATH", ""), str(REPO / "src"), str(REPO)]
        self.connection = {
            "command": sys.executable, "args": ["-m", "runtime.server"], "cwd": str(PACKAGE),
            "env": {"PYTHONPATH": os.pathsep.join(p for p in paths if p)},
        }
        self.run_dir = self.root / "run"

    def open(self, vlm=None, resume=False, task=None, scenario="positive"):
        return build(task=task or self.task, vlm=vlm or ScriptedVLM(),
                     connection=self.connection, run_dir=self.run_dir,
                     acknowledge_synthetic=True, resume=resume, synthetic_scenario=scenario)

    def platform_state(self):
        path = self.run_dir / "native/platform-state/synthetic_world.json"
        return json.loads(path.read_text(encoding="utf-8"))

    async def test_real_stdio_r0_r1_r2_and_complete_native_evidence(self):
        original = deepcopy(self.task)
        model = ScriptedVLM()
        async with self.open(model) as app:
            await app.run()
            state = app.ledger.verify_chain()
            self.assertEqual(app.session.status, "completed")
            self.assertEqual(state["ordinal"], 2)
            self.assertEqual(app.query()["task_scene"]["remaining"], [])
            self.assertEqual(self.task, original)
            self.assertEqual(app.ledger.task, original)
            self.assertEqual(len(state["relations"]), 2)
            self.assertEqual(state["moved_geometry_unknown"], ["a", "b"])
            self.assertTrue(all(e["valid"] for e in state["effects"].values()))
            for effect in state["effects"].values():
                self.assertEqual(set(effect["subjects"]), {
                    "chen_in_proposition", "li_proposition_id", "counterpart_object_subject"})
                self.assertEqual(len(effect["proof"]["records"]), 6)
                app.ledger.check_proof(effect["proof"], fresh=True)
            self.assertEqual(self.platform_state()["place_count"], 2)
            events = app.ledger.events()
            self.assertEqual(len([r for r in events if r.event_type == "verification"]), 2)
            contexts = [r for r in events if r.event_type == "planning_revision_binding"]
            self.assertEqual(len(contexts), len(model.contexts))
            raw = app.agent.memory.read_resource(contexts[0].payload["context_resource"])
            self.assertEqual(json.loads(raw)["data"]["task_scene"]["scene"]["ordinal"], 0)
            self.assertEqual(app.platform["id"], "chen-taskaware")
            self.assertTrue(all(type(entry.provider).__name__ == "RemoteTool"
                                for entry in (app.agent.registry.get(s.name)
                                              for s in app.agent.registry.catalog())))
        self.assertFalse((self.run_dir / "owner.lock").exists())

    async def test_pause_reopen_reply_and_completed_reopen_never_replays(self):
        async with self.open(ScriptedVLM(pause_at_r1=True)) as app:
            await app.run()
            self.assertEqual(app.session.status, "waiting_user")
            self.assertEqual(app.ledger.state()["ordinal"], 1)
            session_id, task_id = app.session.id, app.session.task_id
            self.assertEqual(self.platform_state()["place_count"], 1)
        async with self.open(resume=True) as app:
            self.assertEqual((app.session.id, app.session.task_id), (session_id, task_id))
            self.assertEqual(app.session.status, "waiting_user")
            self.assertEqual(app.ledger.view()["remaining"], ["goal_b"])
            await app.reply("Continue the remaining synthetic goal.")
            self.assertEqual(app.ledger.verify_chain()["ordinal"], 2)
            replies = [r for r in app.ledger.events() if r.event_type == "user_input"]
            self.assertEqual(len(replies), 1)
            self.assertEqual(replies[0].payload["answer"], "Continue the remaining synthetic goal.")
        async with self.open(resume=True) as app:
            app.ledger.clock = lambda: 10**15
            self.assertEqual(app.ledger.verify_chain()["ordinal"], 2)
            self.assertEqual(app.ledger.view()["remaining"], [])
            self.assertEqual(app.ledger.task["constraints"], self.task["constraints"])
            self.assertEqual(app.ledger.task["protected_object_ids"], ["protected"])
            self.assertEqual(self.platform_state()["place_count"], 2)
            with self.assertRaisesRegex(ValueError, "cannot resume"):
                await app.run()

    async def test_missing_observation_only_cavity_refuses_before_r2_append(self):
        async with self.open(ScriptedVLM(pause_at_r1=True)) as app:
            await app.run()
            proof = app.ledger.state()["effects"]["goal_a"]["proof"]
            ob = app.agent.memory.evidence(proof["records"]["observation"])
            ver = app.agent.memory.evidence(proof["records"]["verification"])
            cavity = ob.payload["result"]["payload"]["case"]["observations"][0]["resource_refs"][0]
            self.assertNotIn(cavity, {r.id for r in ver.resource_refs})
            registration = app.agent.memory.references / (cavity + ".json")
            original = registration.read_bytes()
            before = deepcopy(app.ledger.state())
            registration.unlink()
            with self.assertRaisesRegex(RevisionError, "publication refused before append"):
                await app.reply("Continue.")
            self.assertEqual(app.ledger.state(), before)
            self.assertEqual(len(app.ledger.publications()), 1)
            events = app.ledger.events()
            refusals = [r for r in events if r.event_type == "scene_publication_refused"]
            self.assertTrue(refusals)
            self.assertFalse(any(r.event_type == "planning_revision_binding"
                                 and r.timestamp > refusals[-1].timestamp for r in events))
            self.assertEqual(self.platform_state()["place_count"], 2)
            # Restore only this temporary test resource; no real run is ever edited.
            registration.write_bytes(original)

    async def test_fresh_retention_checks_age_but_historical_replay_does_not(self):
        async with self.open(ScriptedVLM(pause_at_r1=True)) as app:
            await app.run()
            proof = app.ledger.state()["effects"]["goal_a"]["proof"]
            app.ledger.clock = lambda: 10**15
            with self.assertRaisesRegex(RevisionError, "expired"):
                app.ledger.check_proof(proof, fresh=True)
            self.assertEqual(app.ledger.verify_chain()["ordinal"], 1)

    async def test_latest_placement_is_a_current_retention_boundary(self):
        async with self.open(ScriptedVLM(pause_at_r1=True)) as app:
            await app.run()
            proof = app.ledger.state()["effects"]["goal_a"]["proof"]
            old_start = app.agent.memory.evidence(proof["records"]["execution_start"])
            old_end = app.agent.memory.evidence(proof["records"]["execution"])
            # Explicitly corrupted temporary journal fixture, not a routed robot action.
            import time
            now = time.time()
            new_call = "synthetic-later-placement"
            app.agent.memory.write(replace(old_start, id="later-start", call_id=new_call,
                                           timestamp=now))
            app.agent.memory.write(replace(old_end, id="later-end", call_id=new_call,
                                           timestamp=now + 0.001))
            with self.assertRaisesRegex(RevisionError, "latest completed placement"):
                app.ledger.check_proof(proof, fresh=True)
            self.assertEqual(app.ledger.verify_chain()["ordinal"], 1)

    async def test_native_guard_rejects_replay_and_wrong_scene_before_provider(self):
        async with self.open(ScriptedVLM(pause_at_r1=True)) as app:
            await app.run()
            goal = app.ledger.view()["goals"][0]
            args = {"external_task_id": self.task["task_id"],
                    "scene_revision": app.ledger.state()["revision"],
                    "goal_id": goal["goal_id"], "visit": goal["visit"], "attempt": goal["attempt"]}
            for revision in ("wrong-revision", app.ledger.state()["revision"]):
                args["scene_revision"] = revision
                call = await app.agent.router.invoke(
                    "execution.chen.place", args, session_id=app.session.id,
                    task_id=app.session.task_id, robot_id=app.session.robot_id,
                    robot_type=app.session.robot_type,
                )
                self.assertEqual(call.status, "failed")
                self.assertIsNotNone(call.error)
            self.assertEqual(self.platform_state()["place_count"], 1)

    async def test_report_rechecks_chain_and_survives_a_missing_resource(self):
        async with self.open() as app:
            await app.run()
            proof = app.ledger.state()["effects"]["goal_a"]["proof"]
            ob = app.agent.memory.evidence(proof["records"]["observation"])
            cavity = ob.payload["result"]["payload"]["case"]["observations"][0]["resource_refs"][0]
            (app.agent.memory.references / (cavity + ".json")).unlink()
            summary = json.loads(app.report().read_text(encoding="utf-8"))
            self.assertEqual(summary["task_effect_status"], "unconfirmed_integrity_check_failed")
            self.assertEqual(summary["integrity"]["verify_chain"], "failed")
            self.assertIsNone(summary["task_scene"])
        self.assertFalse((self.run_dir / "owner.lock").exists())

    async def test_uncertain_verification_stops_without_advance_or_repeat(self):
        # Declared degraded synthetic precision: 2*(0.008+0.005) > 0.02, so Li is uncertain.
        model = ScriptedVLM()
        async with self.open(model, scenario="uncertain_precision") as app:
            await app.run()
            self.assertEqual(app.session.status, "failed")
            state = app.ledger.verify_chain()
            self.assertEqual(state["ordinal"], 0)
            self.assertEqual(state["effects"], {})
            view = app.ledger.view()
            self.assertEqual(view["remaining"], ["goal_a", "goal_b"])
            goal = view["goals"][0]
            self.assertEqual(goal["verification_conclusion"], "uncertain")
            events = app.ledger.events()
            verifications = [r for r in events if r.event_type == "verification"]
            self.assertEqual(len(verifications), 1)
            self.assertEqual(verifications[0].payload["result"]["verification"]["conclusion"],
                             "uncertain")
            self.assertFalse(any(r.event_type == "scene_revision_published" for r in events))
            self.assertEqual(self.platform_state()["place_count"], 1)
            self.assertEqual(len(model.verified_observations), 1)
            # The native guard also refuses re-judging the same observation.
            args = {"external_task_id": self.task["task_id"],
                    "scene_revision": state["revision"], "goal_id": goal["goal_id"],
                    "visit": goal["visit"], "attempt": goal["attempt"],
                    "related_call_id": goal["placement_call_id"],
                    "observation_call_id": goal["observation_call_id"]}
            call = await app.agent.router.invoke(
                "verification.chen.verify", args, session_id=app.session.id,
                task_id=app.session.task_id, robot_id=app.session.robot_id,
                robot_type=app.session.robot_type,
            )
            self.assertEqual(call.status, "failed")
            self.assertEqual(len([r for r in app.ledger.events()
                                  if r.event_type == "verification"]), 1)
            summary = json.loads(app.report().read_text(encoding="utf-8"))
            self.assertEqual(summary["task_effect_status"], "incomplete")
            self.assertEqual(summary["integrity"]["verify_chain"], "passed")
        self.assertFalse((self.run_dir / "owner.lock").exists())

    async def test_task_change_refuses_reopen_and_releases_after_clean_close(self):
        async with self.open(ScriptedVLM(pause_at_r1=True)) as app:
            await app.run()
        changed = deepcopy(self.task)
        changed["constraints"].append("New constraint")
        with self.assertRaises(Exception):
            async with self.open(resume=True, task=changed):
                self.fail("changed task accepted")
        failures = list(self.run_dir.glob("host_failure_*.json"))
        self.assertEqual(len(failures), 1)
        self.assertTrue(json.loads(failures[0].read_text(encoding="utf-8"))
                        ["host_resources_released"])
        self.assertFalse((self.run_dir / "owner.lock").exists())


class ContractTests(unittest.TestCase):
    def test_task_entry_rejects_protected_movement_and_unknown_fields(self):
        task = task_fixture()
        self.assertEqual(validate_task(task), task)
        task["movable_object_ids"].append("protected")
        with self.assertRaisesRegex(ValueError, "overlap"):
            validate_task(task)
        task = task_fixture()
        task["model_reference_answer"] = "leak"
        with self.assertRaisesRegex(ValueError, "fields"):
            validate_task(task)

    def test_skill_files_are_the_tool_contract_source(self):
        package = PlatformPackage(PACKAGE)
        _, skills, _ = package.assemble("scene-revision-synthetic", {})
        definitions = skills.tool_definitions()
        self.assertEqual(len(definitions), 4)
        self.assertEqual({d["spec"]["name"] for d in definitions},
                         {s["id"] for s in skills.catalog()})
        for definition in definitions:
            self.assertEqual(definition["entrypoint"], "runtime.synthetic:factory")
            self.assertEqual(definition["spec"]["provider"], "synthetic.chen_taskaware")
            self.assertNotIn("skill_ref", definition["spec"])
        with self.assertRaisesRegex(ValueError, "override"):
            package.assemble("scene-revision-synthetic", {"synthetic": False})

    def test_vendor_sources_remain_byte_identical(self):
        import hashlib
        sources = json.loads((PACKAGE / "SOURCE_PROVENANCE.json").read_text(encoding="utf-8"))
        for source in sources["sources"]:
            if source["copy"] == "verbatim":
                data = (PACKAGE / source["destination"]).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), source["sha256"])

    def test_network_guard_is_active(self):
        with self.assertRaisesRegex(AssertionError, "network is forbidden"):
            socket.create_connection(("example.invalid", 443))


if __name__ == "__main__":
    unittest.main()
