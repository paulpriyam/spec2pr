#!/usr/bin/env bash
# SubagentStart/SubagentStop logger. Appends one JSON line per event to the main checkout's
# .agent-state/events.jsonl, only while a feature pipeline is active.
. "$(dirname "$0")/lib.sh"
ev="$1"
input=$(cat)
cwd=$(jq -r '.cwd // "."' <<<"$input")
sid=$(jq -r '.session_id // ""' <<<"$input")
root=$(ff_root "$cwd")
key=$(ff_key "$root" "$cwd" "$sid")
[ -n "$key" ] || exit 0
jq -c --arg ev "$ev" --arg key "$key" \
  '{t:(now|todate), ticket:$key, ev:$ev, agent:(.agent_type // .subagent_type // "unknown"), id:(.agent_id // null)}' \
  <<<"$input" >> "$root/.agent-state/events.jsonl"
# Backup for auto-open: if the dashboard isn't running when a subagent starts, start it (no browser tab).
if [ "$ev" = "start" ] && [ "$(jq -r 'if .dashboard == false then "off" else "on" end' "$root/.agent-state/$key.json" 2>/dev/null)" != "off" ]; then
  "$(dirname "$0")/ensure-dashboard.sh" "$root" --no-open >/dev/null 2>&1
fi
exit 0
