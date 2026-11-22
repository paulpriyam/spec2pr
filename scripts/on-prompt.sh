#!/usr/bin/env bash
# UserPromptSubmit hook for /ship-feature, /verify-feature and /ship-program:
#  - ties this Claude session to the feature, so several features can run at once in one repo
#  - /ship-program: ties the session to its program dir (~/.spec2pr/programs/<KEY>) instead of the cwd
#  - starts/opens the dashboard (skipped with --no-dashboard in the prompt or FF_DASHBOARD=off)
. "$(dirname "$0")/lib.sh"
input=$(cat)
prompt=$(jq -r '.prompt // ""' <<<"$input")
cwd=$(jq -r '.cwd // "."' <<<"$input")
sid=$(jq -r '.session_id // ""' <<<"$input")
grep -Eq '^[[:space:]]*/((spec2pr|spec2pr):)?((ship|verify)-feature|ship-program)([[:space:]]|$)' <<<"$prompt" || exit 0
cmd=$(sed -nE '1s#^[[:space:]]*/((spec2pr|spec2pr):)?([a-z-]+).*#\3#p' <<<"$prompt")
first=$(sed -nE '1s#^[[:space:]]*/((spec2pr|spec2pr):)?[a-z-]+[[:space:]]+([^[:space:]]+).*#\3#p' <<<"$prompt")
key=""
if [ -n "$first" ] && [ "${first#--}" = "$first" ]; then
  key=$(ff_key_from_arg "$first")
  grep -q -- '--no-jira' <<<"$prompt" && key=$(tr '[:upper:]' '[:lower:]' <<<"$key")
fi
if [ "$cmd" = "ship-program" ]; then
  [ -n "$key" ] || exit 0
  root="${SPEC2PR_HOME:-$HOME/.spec2pr}/programs/$key"
  mkdir -p "$root/.agent-state"
else
  root=$(ff_root "$cwd")
fi
if [ -n "$key" ] && [ -n "$sid" ]; then
  mkdir -p "$root/.agent-state/sessions" && echo "$key" > "$root/.agent-state/sessions/$sid"
fi
grep -q -- '--no-dashboard' <<<"$prompt" && exit 0
"$(dirname "$0")/ensure-dashboard.sh" "$root" 2>/dev/null
exit 0
