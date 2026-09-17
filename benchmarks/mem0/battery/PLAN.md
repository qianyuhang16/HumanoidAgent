# Battery challenge

## Target / contract (locked before development)
- Frozen official battery_try executor + our Codex Agent + IK approach / VLA manipulation.
- Official `demo_clean` environment success predicate, unchanged; no expert feasibility filtering.
- Development seeds 100000–100007. Final test: 200000–200019, untouched until code/config freeze.
- Accept empirical success >=10/20 on the complete frozen test batch; publish Wilson 95% interval, not a population >=50% claim.
- Count startup, infrastructure and policy failures in the denominator; retain all attempts. A failed test batch becomes development evidence; next frozen version uses fresh seeds 201000–201019 etc.
- Same native episode step limit, task wall limit 3600s, max 12 skills, max 4 hypothesis trials; no resets inside an episode.
- Correct polarity combination is evaluator-only; never supplied to Agent, VLA, or recovery decisions. No expert play_once, scripted grasp/place, object teleport, evaluator modification.
- Privileged observed object geometry/state is allowed and explicitly reported. IK only pregrasp approach. VLA retains all contact, rotation, insertion, release.

## Route
1. Download pinned official checkpoint and task assets; verify SHA256.
2. Extract task-specific scene semantics from the cover-only worker; keep transport/controller shared.
3. Define external battery SKILL.md contracts and an observation-driven Agent trial memory.
4. Run development baseline, diagnose physical/decision failures, add only evidence-driven modules; preserve versions and logs.
5. Freeze candidate; execute 20 held-out episodes and record success, costs, failures, Agent decisions and videos.

## Evidence
- Raw data: workspace/mem0/battery/ (ignored large artifacts).
- Tracked configurations, plan, metric contract, reports here.
- baseline artifact tools unavailable in this session; native tool logs / Git used. No formal artifact-tool acceptance claimed.
