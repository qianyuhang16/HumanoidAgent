"""Run the packaged 619 software rehearsal with synthetic tools and decisions."""
import argparse
import asyncio
from dataclasses import replace
from hashlib import sha256
import importlib.metadata
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
CHEN = ROOT / 'upstream/examples/chen_taskaware'
PLATFORM_ARGS = [sys.executable, '-B', str(Path(__file__).resolve()), '--platform']


def guard_offline(allow_platform):
    for key in list(os.environ):
        if any(word in key.upper() for word in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'PROXY')):
            os.environ.pop(key, None)

    def audit(event, values):
        if event in ('socket.connect', 'socket.bind'):
            address = values[1]
            frame = sys._getframe(1)
            while frame:
                if (frame.f_code.co_filename == socket.__file__
                        and frame.f_code.co_name in ('socketpair', '_fallback_socketpair')
                        and isinstance(address, tuple) and ipaddress.ip_address(address[0]).is_loopback):
                    return
                frame = frame.f_back
            raise PermissionError('619 rehearsal prohibits network')
        if event == 'subprocess.Popen':
            command_matches = values[1] in (PLATFORM_ARGS, subprocess.list2cmdline(PLATFORM_ARGS))
            if allow_platform and command_matches and values[2] and Path(values[2]).resolve() == CHEN:
                return
            raise PermissionError('only the packaged synthetic platform subprocess is allowed')
        if event in ('socket.getaddrinfo', 'socket.sendto', 'os.system', 'os.spawn',
                     'os.posix_spawn', 'os.exec', 'os.startfile'):
            raise PermissionError('619 rehearsal prohibits network and external processes')

    sys.addaudithook(audit)


class PipeReader:
    async def readline(self):
        return await asyncio.to_thread(sys.stdin.buffer.readline, 16 * 1024 * 1024)


class PipeWriter:
    def write(self, data):
        sys.stdout.buffer.write(data)

    async def drain(self):
        await asyncio.to_thread(sys.stdout.buffer.flush)


async def platform():
    from examples.chen_taskaware.runtime.server import connect
    from primitive_vla.runtime.connections.server import StdioServer

    # The candidate's asyncio connect_*_pipe fails on Windows anonymous pipes.
    # Only pipe I/O changes here. The server, RPC, registry and transfer stay native.
    await StdioServer(connect, PipeReader(), PipeWriter()).run()


def chen_context_adapter(chen):
    from scene_memory.working_memory import attach_context_block

    class CandidateRevisionContext(chen.RevisionContext):
        def build(self, session, active_calls):
            self.ledger.publish_pending(active_calls, self.agent.router.active(session.id))
            block = self.ledger.view()
            budget = self.base.max_chars
            self.base.max_chars = budget - len(json.dumps({'task_scene': block}, ensure_ascii=False)) - 160
            try:
                context = self.base.build(session, active_calls)
            finally:
                self.base.max_chars = budget
            context = attach_context_block(context, 'task_scene', block)
            if (len(json.dumps(context.data, ensure_ascii=False))
                    + len(json.dumps(context.action_schema, ensure_ascii=False)) > budget):
                raise ValueError('task scene exceeds the candidate context budget')
            return context

    return CandidateRevisionContext


async def run_chen(out, task):
    from examples.chen_taskaware import host as chen
    from examples.chen_taskaware.legacy.ledger import RevisionError
    from examples.chen_taskaware.tests.test_integration import ScriptedVLM
    from primitive_vla.vlm import FunctionCall, VLMResponse

    class CandidateFixtureVLM(ScriptedVLM):
        """Reuse Chen's decisions, adapting the call format and real message input."""
        async def generate(self, request):
            assert not request.text and request.messages
            data = json.loads(request.messages[-1].text)
            assert list(data) == ['task_scene']
            response = await super().generate(replace(request, text=json.dumps(data)))
            (call,) = response.calls
            return VLMResponse(calls=(FunctionCall('decide', {
                'action': call.name, 'arguments': call.arguments}),))

    connection = {'command': PLATFORM_ARGS[0], 'args': PLATFORM_ARGS[1:], 'cwd': str(CHEN),
                  'env': {'PYTHONPATH': os.pathsep.join(sys.path)}}
    original = chen.RevisionContext
    chen.RevisionContext = chen_context_adapter(chen)
    result = {}
    try:
        for scenario in ('positive', 'uncertain_precision'):
            path = out / ('chen-' + scenario)
            options = dict(task=task, connection=connection, run_dir=path,
                acknowledge_synthetic=True, synthetic_scenario=scenario)
            expected = 2 if scenario == 'positive' else 0
            async with chen.build(vlm=CandidateFixtureVLM(pause_at_r1=True), **options) as app:
                await app.run()
                first = app.query()
                assert first['status'] == ('waiting_user' if expected else 'failed'), first
            if expected:
                async with chen.build(vlm=CandidateFixtureVLM(), resume=True, **options) as app:
                    assert app.query()['session_id'] == first['session_id']
                    assert app.query()['task_scene']['remaining'] == ['goal_b']
                    await app.reply('Continue the remaining synthetic goal.')
                    assert app.session.status == 'completed'
            async with chen.build(vlm=CandidateFixtureVLM(), resume=True, **options) as app:
                state = app.ledger.verify_chain()
                query = app.query()
                assert state['ordinal'] == expected
                assert bool(query['task_scene']['remaining']) == (not expected)
                summary = app.report()
            native = json.loads((path/'native/platform-state/synthetic_world.json').read_text(encoding='utf-8'))
            assert native['place_count'] == (2 if expected else 1)
            result[scenario] = {'revision_count': state['ordinal'], 'place_calls': native['place_count'],
                'remaining': query['task_scene']['remaining'], 'status': query['status'],
                'summary': str(summary.relative_to(out))}
        path = out/'chen-unstored-cavity'
        async with chen.build(task=task, connection=connection, run_dir=path,
                acknowledge_synthetic=True, vlm=CandidateFixtureVLM(pause_at_r1=True)) as app:
            await app.run()
            assert app.session.status == 'waiting_user'
            proof = app.ledger.state()['effects']['goal_a']['proof']
            observation = app.agent.memory.evidence(proof['records']['observation'])
            verification = app.agent.memory.evidence(proof['records']['verification'])
            cavity = observation.payload['result']['payload']['case']['observations'][0]['resource_refs'][0]
            assert cavity not in {ref.id for ref in verification.resource_refs}
            get_resource = app.agent.memory.get_resource

            def missing_cavity(resource_id):
                if resource_id == cavity:
                    raise KeyError('synthetic probe: observation-only cavity registration unavailable')
                return get_resource(resource_id)

            app.agent.memory.get_resource = missing_cavity
            try:
                await app.reply('Continue the remaining synthetic goal.')
            except RevisionError as error:
                assert app.ledger.state()['ordinal'] == 1
                result['missing_retained_observation_resource'] = {'revision_count': 1,
                    'hidden_resource': cavity, 'reason': str(error), 'r2_published': False}
            else:
                raise AssertionError('missing retained proof must refuse publication')
            finally:
                app.agent.memory.get_resource = get_resource
    finally:
        chen.RevisionContext = original
    return result


async def verify_message_transport():
    from primitive_vla.planning import ModelPlanner, PlanningContext
    from primitive_vla.vlm import FunctionCall, Message, VLMResponse
    from scene_memory.working_memory import attach_context_block

    class Capture:
        async def generate(self, request):
            assert request.messages[0].text == 'synthetic earlier turn'
            assert request.messages[0].images[0].data == b'fixture-bytes'
            assert json.loads(request.messages[-1].text)['working_memory_ledger'] == {'protected': ['p']}
            return VLMResponse(calls=(FunctionCall('decide', {'action': 'finish', 'arguments': {}}),))

    context = PlanningContext({'instructions': 'synthetic'}, {},
        ({'id': 'sample', 'media_type': 'image/png'},), ((Message('user', 'synthetic earlier turn'), ('sample',)),))
    planner = ModelPlanner(Capture(), read_resource=lambda resource: b'fixture-bytes')
    await planner.decide(attach_context_block(context, 'working_memory_ledger', {'protected': ['p']}))


async def run(out):
    from first_test_619_root import run_root
    from examples.chen_taskaware.legacy.ledger import validate_task

    manifest = json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))
    for entry in manifest:
        file = (ROOT/entry['path']).resolve(strict=True)
        if not file.is_relative_to(ROOT):
            raise ValueError('manifest path must be inside the package')
        raw = file.read_bytes()
        if len(raw) != entry['bytes'] or sha256(raw).hexdigest() != entry['sha256']:
            raise ValueError('package file differs: ' + entry['path'])
    dependencies = {}
    for requirement in (ROOT/'requirements.txt').read_text().splitlines():
        name, pin = requirement.split('==')
        actual = importlib.metadata.version(name)
        if actual != pin:
            raise ValueError(f'{name} {pin} required, detected {actual}')
        dependencies[name] = actual
    out.mkdir(parents=True, exist_ok=False)
    task = validate_task(json.loads((ROOT/'synthetic_task.json').read_text(encoding='utf-8')))
    await verify_message_transport()
    chen = await run_chen(out, task)
    root = await run_root(out/'root', task)
    receipt = {'synthetic': True, 'physical_execution': 'not_started', 'api_calls': 0,
        'task_sha256': sha256((ROOT/'synthetic_task.json').read_bytes()).hexdigest(),
        'python': sys.version.split()[0], 'dependencies': dependencies, 'manifest_files': len(manifest),
        'native_message_image_and_ledger': 'passed', 'chen': chen, 'root': root}
    (out/'summary.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print('619 offline engineering rehearsal passed:', out/'summary.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--platform', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.platform == (args.out is not None):
        parser.error('provide --out for the offline rehearsal')
    if not CHEN.is_dir():
        parser.error('run this entry from the assembled 619 package')
    # A standalone launcher selects one isolated source tree for parent and child.
    sys.path[:0] = [str(ROOT/'upstream/src'), str(ROOT/'upstream'), str(CHEN)]
    guard_offline(not args.platform)
    if args.platform:
        asyncio.run(platform())
    else:
        asyncio.run(asyncio.wait_for(run(args.out.resolve()), timeout=90))


if __name__ == '__main__':
    main()
