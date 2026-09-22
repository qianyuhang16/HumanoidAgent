"""Use the existing generic-skill Agent unchanged with the selected battery policy."""
import asyncio
import json
from pathlib import Path
from primitive_vla.runtime.generic_service import GenericService
from primitive_vla.runtime.generic_episode import episode

ROOT=Path(__file__).resolve().parents[2]

async def main():
    service=GenericService(ROOT,ROOT/'workspace/pi05/evaluation/settings')
    for seed in (300100,300101):
        path=ROOT/'workspace/pi05/evaluation/agent'/str(seed)
        if (path/'summary.json').exists(): continue
        config=service.config('battery')
        config.update(seed=seed,live_frame=True)
        config['tool_agent']['max_calls']=16
        config['task']['timeout_s']=1200
        # Same planner and generic tool contracts, no battery-specific prompt additions.
        await episode(config,path)

if __name__=='__main__':asyncio.run(main())
