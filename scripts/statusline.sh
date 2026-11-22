#!/usr/bin/env bash
# Status line: "PROJ-123 │ tester │ build loop 2/3 │ FAIL AC-3"
# Add to ~/.claude/settings.json: {"statusLine":{"type":"command","command":"<path>/statusline.sh"}}
. "$(dirname "$0")/lib.sh"
input=$(cat)
dir=$(jq -r '.workspace.current_dir // .cwd // "."' <<<"$input")
sid=$(jq -r '.session_id // ""' <<<"$input")
root=$(ff_root "$dir")
root=$(ff_program_root "$root" "$sid")
key=$(ff_key "$root" "$dir" "$sid")
[ -n "$key" ] || exit 0
f="$root/.agent-state/$key.json"
[ -f "$f" ] || { echo "$key │ starting"; exit 0; }
line=$(jq -r '"\(.ticket) │ \(.phase // "?") │ spec \(.attempts.spec // 0)/3 · build \(.attempts.build // 0)/3 │ \(.last // "")"' "$f")
# Open questions for this feature (scripts/ask.py): the pipeline is waiting on you.
open_q=$(cat "$root/.agent-state/questions/"*.json 2>/dev/null | jq -s --arg k "$key" '[.[] | select(.status == "open" and (.ticket == $k or .ticket == null))] | length' 2>/dev/null)
[ "${open_q:-0}" -gt 0 ] && line="⚑ NEEDS YOU ($open_q) │ $line"
echo "$line"
