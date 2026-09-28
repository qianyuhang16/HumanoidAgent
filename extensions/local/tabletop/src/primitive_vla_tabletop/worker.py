"""GPU worker; camera inference and SAPIEN remain outside the Agent process."""

import json
import os
import queue
import sys
import threading
import time
import traceback
from uuid import uuid4


def main():
    channel = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    messages = queue.Queue()

    def read():
        for line in sys.stdin:
            try:
                messages.put(json.loads(line))
            except ValueError:
                pass
        messages.put({"op": "close", "id": None})

    def send(message):
        channel.write(json.dumps(message, allow_nan=False) + "\n")

    threading.Thread(target=read, daemon=True).start()
    world = perception = active = None
    cancelled, completed, observations, candidates = set(), {}, {}, {}
    try:
        while True:
            try:
                message = messages.get(timeout=.001 if active else .1)
            except queue.Empty:
                message = None
            if message:
                request_id, op = message["id"], message["op"]
                try:
                    if op == "init":
                        from .scene import TabletopScene
                        from .perception import Perception
                        world = TabletopScene(message["options"], message["media"])
                        perception = Perception(message["options"], message["media"])
                        send({"id": request_id, "result": {"ready": True}})
                    elif op == "close":
                        if active:
                            active["iterator"].close()
                            world.stop()
                        break
                    elif op == "stop":
                        call_id = message["call_id"]
                        cancelled.add(call_id)
                        if active and active["call_id"] == call_id:
                            active["iterator"].close()
                            world.stop()
                            send({"id": active["id"], "error": "Action cancelled"})
                            active = None
                        send({"id": request_id, "result": {"call_id": call_id, "stopped": active is None}})
                    else:
                        if world is None:
                            raise RuntimeError("Scene is not initialized")
                        if active:
                            raise RuntimeError("Robot is busy")
                        if message["call_id"] in cancelled or message["deadline"] <= time.time():
                            raise RuntimeError("Cancelled or expired tool call")
                        if message["call_id"] in completed:
                            previous = completed[message["call_id"]]
                            if previous[0] != (op, message["arguments"]):
                                raise ValueError("Call ID reused with a different payload")
                            send({"id": request_id, "result": previous[1]})
                            continue
                        args = message["arguments"]
                        if op in ("move", "gripper"):
                            iterator = world.move(args["plan_id"], args["waypoint"]) if op == "move" else world.grip(args["width_m"])
                            active = {**message, "iterator": iterator}
                            continue
                        if op == "observe":
                            result = world.observe()
                            observations[result["observation_id"]] = result
                        elif op == "set_goal":
                            result = world.contract.define(args["criteria"])
                        elif op in ("segment", "depth", "propose"):
                            observation = observations[args["observation_id"]]
                            if observation["revision"] != world.revision:
                                raise ValueError("Observation is stale after robot motion; observe again")
                            frame = observation["frame_ref"]
                            if op == "segment":
                                result = perception.segment(frame, args["query"])
                            elif op == "depth":
                                result = perception.depth(frame)
                            else:
                                result = perception.propose(frame, args["mask_ref"], args["depth_ref"])
                                candidate_id = uuid4().hex
                                candidates[candidate_id] = result
                                result = {**result, "candidate_id": candidate_id}
                        elif op == "plan":
                            from .geometry import clearance
                            candidate = candidates[args["candidate_id"]]
                            if candidate["revision"] != world.revision:
                                raise ValueError("Candidate is stale; observe and infer again")
                            checked = clearance(perception.frame(candidate["frame_ref"]), candidate["position_world_m"],
                                                floor=candidate["position_world_m"][2] + .04)
                            result = {"verdict": checked["verdict"], "checks": {"approach_clearance": checked},
                                      "prediction_only": True, "unassessed": ["object attachment", "full-arm collisions", "unseen geometry"]}
                            if checked["verdict"] == "feasible":
                                try:
                                    result.update(world.plan(candidate))
                                    result["checks"]["endpoint_ik"] = {"verdict": "feasible"}
                                except ValueError as error:
                                    result.update(verdict="infeasible", reason=str(error))
                                    result["checks"]["endpoint_ik"] = {"verdict": "infeasible", "reason": str(error)}
                        elif op == "plan_place":
                            from .geometry import clearance
                            observation = observations[args["observation_id"]]
                            if observation["revision"] != world.revision:
                                raise ValueError("Placement observation is stale")
                            checked = clearance(perception.frame(observation["frame_ref"]), world.zones[args["zone"]]["center"],
                                                radius=.075, floor=.015)
                            result = {"verdict": checked["verdict"], "checks": {"destination_clearance": checked}, "prediction_only": True}
                            if checked["verdict"] == "feasible":
                                try:
                                    result.update(world.plan_place(args["grasp_plan_id"], args["zone"]))
                                except ValueError as error:
                                    result.update(verdict="infeasible", reason=str(error))
                        elif op == "verify":
                            result = world.verify()
                        else:
                            raise ValueError(f"Unknown operation: {op}")
                        completed[message["call_id"]] = ((op, args), result)
                        send({"id": request_id, "result": result})
                except Exception as error:
                    traceback.print_exc()
                    send({"id": request_id, "error": f"{type(error).__name__}: {error}"})
            if active:
                try:
                    if active["deadline"] <= time.time():
                        raise TimeoutError("Robot action deadline expired")
                    next(active["iterator"])
                    for _ in range(4):
                        world.step()
                except StopIteration as done:
                    world.record()
                    result = {**(done.value or {}), "video": world.video,
                              "images": {"robot": world.snapshot_overview()}}
                    completed[active["call_id"]] = ((active["op"], active["arguments"]), result)
                    send({"id": active["id"], "result": result})
                    active = None
                except Exception as error:
                    world.stop()
                    active["iterator"].close()
                    traceback.print_exc()
                    send({"id": active["id"], "error": f"{type(error).__name__}: {error}"})
                    active = None
    finally:
        if world:
            world.close()
        channel.close()


if __name__ == "__main__":
    main()
