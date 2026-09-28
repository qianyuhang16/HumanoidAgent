"""Persist arm handover state across chat turns; never persist a VLA action queue."""
import json
from pathlib import Path

class ArmState:
    def __init__(self, workspace):
        self.path=Path(workspace)/'arm-state.json'

    def dirty(self):
        if not self.path.exists():return set()
        data=json.loads(self.path.read_text())
        arms=set(data['needs_reset'])
        if not arms <= {'left','right'}:raise ValueError('Invalid persisted arm state')
        return arms

    def require(self, arms):
        other=self.dirty()-set(arms)
        if other:raise ValueError('Switching arms requires robot.reset_arm first: '+', '.join(sorted(other)))

    def write(self, arms):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        tmp=self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps({'needs_reset':sorted(arms)}))
        tmp.replace(self.path)

    def used(self, arms):self.write(self.dirty()|set(arms))
    def reset(self, arm):self.write(self.dirty()-{arm})
