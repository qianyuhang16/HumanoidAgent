Review the ongoing VLA execution using only current RGB, entry RGB and the previous
review RGB. Compare visible progress against the exact requested instruction.
Choose continue if execution is making useful progress or stopping now would
unnecessarily interrupt a plausible manipulation. Choose yield if motion appears
stalled, repeatedly unsuccessful, materially off target, or should be reassessed by
the Agent. Yield returns control for the existing perception/planning loop; it does
not certify physical completion. Do not assume a grasp, orientation or insertion
succeeded merely because it was requested. Give a concise visible-evidence reason.
