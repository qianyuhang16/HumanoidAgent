"""Paired direct-policy prompt pilot against the persistent VLA lab."""
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'workspace/pi05/evaluation/direct'
CONDITIONS = {
    'official_global': 'Use dual arm to pick up the batteries and place them into the battery slots in the positive direction.',
    'released_left': 'Place the left battery at the middle.',
    'paraphrase_left': 'Move the battery on the left to the center.',
    'released_right': 'Place the right battery at the middle.',
    'grasp_hold_left': 'Pick up the left battery and hold it above the table. Do not put it down or flip it.',
}


def api(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request('http://127.0.0.1:8087/api/'+path, data=data,
                                    headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def ready():
    deadline = time.monotonic()+900
    while time.monotonic()<deadline:
        state=api('status')
        if state['state']=='ready': return state
        if state['state']=='error': raise RuntimeError(state['error'])
        time.sleep(2)
    raise TimeoutError('VLA lab did not return to ready within 900 seconds')


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest={'seeds':[300100,300101], 'conditions':CONDITIONS, 'max_actions':300,
              'model':json.loads((ROOT/'configs/vla-lab.json').read_text()),
              'checkpoint_sha256':'1d54f50aa26d590d4aaf43cceab964db0c93b2f547c808e26559d6a304aef120',
              'command':'PYTHONPATH=src .venv/bin/python benchmarks/pi05_prompt/direct.py'}
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2))
    ready()
    for seed in manifest['seeds']:
        for label,prompt in CONDITIONS.items():
            target=OUT/f'{seed}-{label}.json'
            if target.exists(): continue
            api('reset',{'seed':seed}); before=ready()
            api('execute',{'prompt':prompt,'max_actions':300,'stop_on_end':False})
            after=ready()
            scene=ROOT/'workspace/vla-lab'/after['session']/f"scene-{after['scene_index']:03d}"
            result={'condition':label,'seed':seed,'scene':str(scene),'before':before['observation'],
                    'after':after['observation'],'command':after['commands'][-1],
                    'model_identity':after['model_identity']}
            target.write_text(json.dumps(result,indent=2))
            print(json.dumps({'seed':seed,'condition':label,'command':result['command']}),flush=True)
    # Flush the last scene video and leave a fresh scene ready for manual use.
    api('reset',{'seed':300100});ready()

if __name__=='__main__':main()
