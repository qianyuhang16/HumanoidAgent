# Battery prompt pilot

Question: does the downloaded RMBench subtask Pi0.5 obey paraphrases and changed goals?
Tier: auxiliary/dev, not a reproduced official benchmark or statistically powered success rate.

Use pinned Myungkyu/pi0_5_rmbench_preset_b64_60k, 25-action chunks, 10 denoising steps,
three RGB cameras and qpos; current head image fills the no-retrieval keyframe.
Identical simulator seeds and reset noise seeds across prompt conditions.

Direct conditions: released left-placement label, same-meaning paraphrase, right-placement
label (target switch), and left-grasp-and-hold (new stopping goal). Pilot 2 seeds,
300 actions each. Save prompt/config, simulator trajectory/video, final RGB, diagnostics,
and command result. Placement/flip labels are subtask labels, not full-task success criteria.
Use visual review plus evaluator-only object displacement/height and gripper feedback;
never count XY proximity alone as successful insertion. Report uncertainty about stable grasp.

Then run the unchanged generic-tool Agent with the same Pi0.5 adapter on the full battery
goal, 2 seeds, at most 16 tool calls / 1200 seconds per episode; no battery-specific skill or fixed action sequence. Agent sees cameras,
joint feedback, Grounding/SAM output, and tool results, not simulator grading.
Report declared completion separately from terminal simulator success and tool trace.

Minimum: verified strict load and executable direct + Agent pilots.
Solid: paired direct results and recorded Agent outcomes, including failures.
No significance claim at this sample size. Stop on invalid adapter/numeric outputs;
repair infrastructure before interpreting policy behavior. Do not tune weights or prompts
against the evaluation seeds; any follow-up is separately labelled.

Supplement: after the 8 core paired runs, execute the original RMBench full-task
annotation at the same 300-action budget on both seeds. This is a cross-model
reference, not the community checkpoint's released subtask baseline. Do not treat
its full-task outcome as comparable to a single-object placement criterion.
