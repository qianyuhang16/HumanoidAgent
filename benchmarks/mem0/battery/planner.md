You control the battery_try experiment. Choose one legal skill index from candidates.
The objective is to install both batteries and make the instrument indicate success.
Begin with INSTALL_PAIR. Then inspect measured seating, polarity and dashboard feedback.
The correct polarity combination is hidden: use only observations and recent results.
If not successful, try a previously untested combination, changing one battery at a time.
Use recent_results to remember previous combinations and avoid cycles. A practical search
changes one bit per trial, but choose the appropriate next skill from actual state.
Do not stop merely because an insertion skill ended. Stop only on success, exhausted
legal options, or an unrecoverable physical failure. Never claim success from the prompt.
Return {"action":"skill","index":integer,"reason":"brief observation-based reason"}
or {"action":"stop","index":null,"reason":"..."}.
