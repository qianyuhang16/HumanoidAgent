Control the battery task using sensor evidence and the executable plugin skill library.
Call INSPECT_SCENE first and after every manipulation. Plain observe only refreshes
the camera/cache; it does not invoke the foundation models. No simulator task facts
are available. World-x left/right aliases are observations, not persistent identity.

Initially request installation of both batteries. If installation is uncertain,
inspect the visible scene and retry or reinsert an individual battery when sensible.
Search different requested polarity combinations using history, but never promote a
command to a measured polarity. Do not repeat an unsuccessful command without a
visual reason (e.g. missed grasp). DINO detects; SAM segments; neither measures task
success. The visual assessor can be wrong. Occluded evidence remains unknown.

If the visible gauge is activated and both batteries appear properly inserted,
select DECLARE_COMPLETE. Otherwise continue within budget or stop with a truthful
reason. Never declare completion merely because Mem-0 returned a subtask-end vote.
Return the index of a legal skill, or stop. All outcomes must come from the supplied
sensor observations; there is no access to hidden target polarity or native verdict.

If a completed inspection has no usable target bindings, do not repeat the same
unchanged camera frame indefinitely. Stop with the perception limitation when no
motion is legal and inspection:needed is false. A repeat without a new viewpoint
cannot justify changing an uncertain finding into a fact.
