"""Three shared capability documents; no task-specific skill contracts."""
from pathlib import Path


def capability_descriptions():
    root = Path(__file__).parent
    return {name: (root/folder/'SKILL.md').read_text().strip()
            for name, folder in [('perceive', 'grounding_sam'), ('execute', 'vla'), ('move_to', 'ik')]}
