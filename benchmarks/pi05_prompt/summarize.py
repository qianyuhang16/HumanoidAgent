"""Export factual endpoint metrics and evidence links, without inventing task success."""
import json
import math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'workspace/pi05/evaluation'
rows=[]
for path in sorted((OUT/'direct').glob('3*.json')):
    run=json.loads(path.read_text())
    diag=run['command']['diagnostics']
    row={'seed':run['seed'],'condition':run['condition'],'actions':run['command']['actions'],
         'instrument_on':diag['instrument_on'],'scene':run['scene'],
         'prompt':run['command']['prompt']}
    for side in ('left','right'):
        battery=diag['batteries'][side]
        xyz=battery['position_error_xyz']
        row[side+'_xy_error_cm']=round(math.hypot(*xyz[:2])*100,2)
        row[side+'_z_error_cm']=round(xyz[2]*100,2)
        row[side+'_in_slot_xy']=battery['in_slot_xy']
    rows.append(row)
(OUT/'direct-summary.json').write_text(json.dumps(rows,indent=2))
print(json.dumps(rows,indent=2))
