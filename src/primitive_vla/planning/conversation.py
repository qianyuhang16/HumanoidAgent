"""Project the session journal into a bounded, multimodal conversation."""

import json

from primitive_vla.contracts import Record
from primitive_vla.vlm import FunctionCall, Message

# A call and its result form a single unit when reducing history.
Turn = tuple[tuple[Message, tuple[str, ...]], ...]
TERMINAL_EVENTS = {"tool_finished", "verification"}


def build_conversation(data: dict, journal: list[Record], images: tuple[dict, ...],
                       project, budget: int) -> Turn:
    attachments = {}
    for ref, label in zip(images, data["images"]):
        attachments.setdefault(ref["record_id"], []).append((ref["id"], label))

    # A planner dispatch is serial. Background tool events can interleave with it.
    feedback = {}
    paired_results = {}
    terminal = {}
    evidence_reads = {}
    decision = None
    for record in journal:
        if record.event_type in TERMINAL_EVENTS:
            terminal[record.call_id] = record
        elif record.event_type == "decision":
            decision = record
        elif record.event_type == "action_result":
            if decision is not None:
                if decision.payload["action"] == "evidence_get" and "content" in record.payload:
                    evidence_reads[record.payload["id"]] = record.id
                feedback[record.id] = decision
                result = terminal.get(record.payload.get("call_id"))
                if decision.payload["action"] == "call_tool" and result:
                    paired_results[record.id] = result
            decision = None
        elif record.event_type == "user_input":
            decision = None

    for record_id, read_id in evidence_reads.items():
        if record_id in attachments:
            attachments[read_id] = attachments.pop(record_id)

    synchronous = {record.id for record in paired_results.values()}
    visible_records = {record["id"] for record in data["records"]}
    timeline: list[Turn] = []
    latest_user = None
    for record in journal:
        kind = record.event_type
        if kind == "user_input":
            answer = record.payload["answer"]
            selections = {key: value for key, value in record.payload.items() if key != "answer"}
            if selections:
                answer += "\n" + json.dumps(selections, ensure_ascii=False)
            latest_user = len(timeline)
            timeline.append(((Message("user", answer), ()),))
            continue

        decision = feedback.get(record.id)
        source = paired_results.get(record.id, record)
        returned_images = attachments.get(source.id, ())
        call = None
        if kind == "action_result":
            output = dict(project(source.payload))
            if decision:
                call = FunctionCall("decide", decision.payload)
                action = decision.payload["action"]
                if record.status == "completed" and action in {"ask_user", "finish"}:
                    key = "question" if action == "ask_user" else "message"
                    if key in output:
                        timeline.append(((Message("assistant", output[key]), ()),))
                        continue
            if record.id in paired_results and "name" in output:
                output["evidence_id"] = source.id
        elif kind in TERMINAL_EVENTS and record.id not in synchronous:
            output = project({**record.payload, "call_id": record.call_id,
                              "name": record.source, "status": record.status})
            if "name" in output:
                output["evidence_id"] = record.id
        elif (kind in {"observation", "tool_progress"}
              and (returned_images or record.id in visible_records)):
            output = {"observation": record.source, **record.payload}
        else:
            continue

        if "images" in output and (kind in TERMINAL_EVENTS or "call_id" in record.payload):
            del output["images"]
            if not output and not returned_images:
                output["observation"] = "Image was returned earlier and is outside the attached views."
        if returned_images:
            output["images"] = [label for _, label in returned_images]
        text = json.dumps(output, ensure_ascii=False)
        if len(text) > 3500:
            text = json.dumps({"preview": text[:1500], "record_id": source.id,
                               **({"images": output["images"]} if returned_images else {})},
                              ensure_ascii=False)
        ids = tuple(id for id, _ in returned_images)
        if call is None:
            prefix = f"Runtime update ({record.source})" if kind in TERMINAL_EVENTS else "Runtime update"
            timeline.append(((Message("user", prefix + "\n" + text), ids),))
        else:
            call_id = f"call_{decision.id}"
            timeline.append(((Message("assistant", "", call=call, call_id=call_id), ()),
                             (Message("tool", text, call_id=call_id), ids)))

    goal = ((Message("user", data["goal"]), ()),)
    # Keep the goal, the latest user input and attached observations independently of routine calls.
    preferred = {index for index, turn in enumerate(timeline) if any(ids for _, ids in turn)}
    if latest_user is not None:
        preferred.add(latest_user)
    if timeline:
        preferred.add(len(timeline) - 1)
    used = len(data["goal"]) + 64
    selected = {}
    extras = {"history": "Some earlier events are outside this context. Retrieve evidence on demand."} if timeline else {}

    def context_size():
        return len(json.dumps(extras, ensure_ascii=False)) + 80 if extras else 0

    def retain_turn(index):
        nonlocal used
        turn = timeline[index]
        size = sum(len(message.text) + 100 +
                   (len(json.dumps(message.call.arguments, ensure_ascii=False)) if message.call else 0)
                   for message, _ in turn)
        if used + size + context_size() <= budget:
            selected[index] = turn
            used += size

    def retain_context(key, rows):
        if not rows:
            return
        extras[key] = []
        for row in rows:
            extras[key].append(row)
            if used + context_size() > budget:
                extras[key].pop()
        if not extras[key]:
            del extras[key]

    for index in sorted(preferred, reverse=True):
        retain_turn(index)
    # The model must still know what it can do when routine action history fills the window.
    retain_context("active_calls", data["active_calls"])
    retain_context("tools", data["tools"])
    for index in reversed(range(len(timeline))):
        if index not in preferred:
            retain_turn(index)
    if len(selected) == len(timeline):
        extras.pop("history", None)

    dialog_ids = {record.id for record in journal if record.event_type in
                  {"user_input", "user_question", "task_finished", "observation", "tool_progress"}}
    notes = [record for record in data["records"] if record["id"] not in dialog_ids]
    retained_results = {message.text for turn in selected.values() for message, _ in turn
                        if message.role == "tool"}
    definitions = [row for row in data["capabilities"]
                   if json.dumps(row, ensure_ascii=False) not in retained_results]
    for key, rows in (("capabilities", definitions), ("skills", data["skills"]), ("records", notes)):
        retain_context(key, rows)
    context = ()
    if extras and used + context_size() <= budget:
        text = "Available context\n" + json.dumps(extras, ensure_ascii=False)
        context = ((Message("user", text), ()),)
    return goal + context + tuple(item for index in sorted(selected) for item in selected[index])
