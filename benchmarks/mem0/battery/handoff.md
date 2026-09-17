Review the current battery operation using images, measured facts and expected effects.
Choose only from the provided actions. Return {"action":"continue"|"reapproach"|"stop","reason":"brief evidence"}.
Continue preserves VLA visual memory and finishes an ongoing grasp, rotation, insertion or retreat.
Reapproach moves empty grippers toward the currently selected battery/batteries using IK.
Never reapproach while holding a battery or while another arm obstructs the approach.
Stop if further actions are unsafe or futile. A classifier end does not imply successful insertion.
Do not use or guess an evaluator's hidden correct polarity combination.
