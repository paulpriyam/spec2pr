#!/usr/bin/env bash
# PreToolUse/PostToolUse logger (runs async, never blocks Claude). While a feature pipeline is active,
# appends one line per tool call start/end to the main checkout's .agent-state/tools.jsonl, tagged with
# the agent that made it (agent_id/agent_type; none = the main "elephant" session). The dashboard's
# Flow view is built from it. Secrets in commands are blanked before anything is written.
. "$(dirname "$0")/lib.sh"
ev="$1"
input=$(cat)
cwd=$(jq -r '.cwd // "."' <<<"$input")
sid=$(jq -r '.session_id // ""' <<<"$input")
root=$(ff_root "$cwd")
key=$(ff_key "$root" "$cwd" "$sid")
[ -n "$key" ] || exit 0
jq -c --arg ev "$ev" --arg key "$key" --arg cwd "$cwd/" '
  def short(n): if length > n then .[0:n] + "…" else . end;
  def rel: if startswith($cwd) then .[($cwd|length):] else . end;
  def redact: gsub("(?<k>(?i)(token|secret|password|passwd|api[_-]?key|authorization)[\"'"'"']?\\s*[:=]\\s*(bearer\\s+)?)[^\\s\"'"'"'&]+"; "\(.k)***")
            | gsub("(?<k>(?i)--(token|password|secret|api-key)[= ])[^\\s]+"; "\(.k)***")
            | gsub("gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|xox[abp]-[A-Za-z0-9-]{10,}"; "***");
  (.tool_input // {}) as $i |
  (.tool_name // "?") as $tool |
  (if $tool == "Bash" then ($i.command // "")
   elif ($tool | test("^(Read|Edit|Write|MultiEdit|NotebookEdit)$")) then (($i.file_path // $i.notebook_path // "") | rel)
   elif $tool == "Grep" then "\($i.pattern // "")\(if $i.path then "  in " + ($i.path|rel) else "" end)"
   elif $tool == "Glob" then ($i.pattern // "")
   elif ($tool == "Agent" or $tool == "Task") then "\($i.subagent_type // "agent"): \($i.description // "")"
   elif $tool == "WebFetch" then ($i.url // "")
   elif $tool == "WebSearch" then ($i.query // "")
   elif $tool == "Skill" then ($i.skill // "")
   else "" end) as $s |
  {t: (now|todate), ticket: $key, ev: $ev, tool: $tool, id: (.tool_use_id // null),
   agent_id: (.agent_id // null), agent_type: (.agent_type // null),
   session: (.session_id // null), transcript: (.transcript_path // null),
   summary: ($s | tostring | gsub("\\s+"; " ") | redact | short(160))}
  + (if $ev == "post" then {ok: ((.tool_response | type) != "object" or (.tool_response.is_error != true and .tool_response.success != false and .tool_response.interrupted != true))} else {} end)
' <<<"$input" >> "$root/.agent-state/tools.jsonl" 2>/dev/null

# Loop alert: the same agent ran the same failing command for the 3rd time -> one desktop notification.
if [ "$ev" = "post" ] && [ "${FF_NOTIFY:-on}" != "off" ]; then
  last=$(tail -n 1 "$root/.agent-state/tools.jsonl")
  if [ "$(jq -r '.ok' <<<"$last")" = "false" ]; then
    n=$(tail -n 600 "$root/.agent-state/tools.jsonl" | jq -s --argjson l "$last" \
      '[.[] | select(.ev == "post" and .ok == false and .agent_id == $l.agent_id and .tool == $l.tool and .summary == $l.summary)] | length')
    if [ "$n" = "3" ]; then
      who=$(jq -r '(.agent_type // "elephant") | sub("^(spec2pr|feature-factory):"; "")' <<<"$last")
      what=$(jq -r '"\(.tool): \(.summary)" | .[0:90]' <<<"$last")
      [ "$(uname)" = "Darwin" ] && osascript -e 'on run argv
display notification (item 2 of argv) with title "spec2pr · possible loop" subtitle (item 1 of argv) sound name "Basso"
end run' "$who has hit the same failure 3 times" "$what" >/dev/null 2>&1
      python3 "$(dirname "$0")/telegram.py" notify "⚠️ $(basename "$root") · $key: possible loop. $who has hit the same failure 3 times: $what" >/dev/null 2>&1 &
    fi
  fi
fi
exit 0
