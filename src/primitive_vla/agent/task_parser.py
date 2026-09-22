"""Convert structured input, templates or VLM output into bounded goals."""

from fnmatch import fnmatchcase
from pathlib import Path
from dataclasses import asdict, replace
from pydantic import TypeAdapter
from primitive_vla.config.document import load_document

from primitive_vla.config.schema import ParserConfig, TaskDefaults
from primitive_vla.domain.models import Task, Truth
from primitive_vla.domain.skills import SkillSpec
from primitive_vla.domain.state import State
from primitive_vla.providers.base import ModelClient

DEFAULT_INSTRUCTION = "把蓝杯里的配料倒入碗，搅拌，再把杯勺放回"
SIMULATION_INSTRUCTION = "把所有配料放入碗里，然后搅拌并放回工具"


def parse_task(data: dict, state: State) -> Task:
    task = TypeAdapter(Task).validate_python({"instruction": "Structured task", **data})
    goals = task.goals
    ids = {g.id for g in goals}
    if not goals or len(ids) != len(goals) or any(not g.conditions for g in goals):
        raise ValueError("Task requires nonempty goals with unique IDs")
    by_id = {g.id: g for g in goals}

    def visit(name: str, path: set[str]) -> None:
        if name not in by_id or name in path:
            raise ValueError("Invalid/cyclic goal dependency")
        for dependency in by_id[name].after:
            visit(dependency, path | {name})

    for goal in goals:
        visit(goal.id, set())
        if Truth.UNKNOWN in goal.conditions.values():
            raise ValueError("Goal conditions must be true or false, never unknown")
        for condition in goal.conditions:
            # The symbolic vocabulary is declared by the scene/observer.
            if condition not in state.facts:
                raise ValueError(f"Unknown scene predicate in task: {condition}")
    if task.max_steps < 1 or task.timeout_s <= 0 or task.max_observations < 0:
        raise ValueError("Invalid task budget")
    return task


def parse_instruction(instruction: str, state: State, templates: list[dict] | None = None) -> Task:
    """A deliberately narrow kitchen template, replaceable by structured VLM output."""
    if templates is None:
        path = Path(__file__).parents[1] / "config/defaults/tasks.yaml"
        templates = load_document(path)
    template = next((entry for entry in templates if instruction in entry["instructions"]), None)
    if template is None:
        raise ValueError("没有匹配的任务模板；请使用结构化 JSON 或 VLM 解析。")
    goals = []
    for entry in template["goals"]:
        objects = [o.id for o in state.objects.values() if o.kind == entry["foreach"]] if "foreach" in entry else [None]
        if not objects:
            raise ValueError(f"Task template needs objects of kind {entry['foreach']}")
        for obj in objects:
            def bind(value):
                return value.format(object=obj) if obj is not None else value
            goals.append({"id": bind(entry["id"]),
                          "conditions": {bind(k): v for k, v in entry["conditions"].items()},
                          "after": entry.get("after", [])})
    for goal in goals:
        dependencies = []
        for pattern in goal["after"]:
            matches = [g["id"] for g in goals if fnmatchcase(g["id"], pattern)]
            if not matches:
                raise ValueError(f"Unknown task dependency: {pattern}")
            dependencies.extend(matches)
        goal["after"] = dependencies
    return parse_task({"instruction": instruction, "goals": goals}, state)


class TaskParser:
    def __init__(self, config: ParserConfig, defaults: TaskDefaults, client: ModelClient):
        self.config, self.defaults, self.client = config, defaults, client

    async def parse(self, instruction: str, task_data: dict | None, state: State, skills: list[SkillSpec]) -> Task:
        if task_data is not None:
            task = parse_task({**self.defaults.model_dump(), **task_data}, state)
        elif self.config.type == "vlm":
            context = {"instruction": instruction, "facts": state.values(),
                       "objects": [asdict(o) for o in state.objects.values()],
                       "skills": [{"name": s.name, "description": s.description, "instructions": s.instructions,
                                   "effects": s.effects} for s in skills],
                       "schema": {"goals": [{"id": "unique_string", "conditions": {"EXACT_FACT_KEY": "true"},
                                                      "after": ["earlier_goal_id"]}], "forbidden_skills": []}}
            parsed = await self.client.complete("parse", self.config.prompt +
                "\nTranslate the user's desired outcome into a MINIMAL set of final state predicates. "
                "Only use EXACT keys present in facts. Conditions contain desired FINAL values, not current values or skill preconditions. "
                "For moving an object, use its at:object:destination predicate. For closing, open:container=false. "
                "Do not add reachability, health, hand occupancy or object readiness goals unless explicitly requested. "
                "after is an array of goal ID STRINGS that must finish BEFORE this goal. Never put objects in after. "
                "Return only goals and forbidden_skills, no action sequence.", context, state.observation.images)
            # Model output cannot enlarge the configured task budgets.
            task = parse_task({"instruction": instruction, "goals": parsed["goals"],
                               "forbidden_skills": parsed.get("forbidden_skills", []), **self.defaults.model_dump()}, state)
        else:
            task = replace(parse_instruction(instruction, state, self.config.templates), **self.defaults.model_dump())
        return task
