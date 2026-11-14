#!/usr/bin/env bash
# UserPromptSubmit hook for /ship-feature and /verify-feature:
#  - ties this Claude session to the feature, so several features can run at once in one repo
#  - starts/opens the dashboard (skipped with --no-dashboard in the prompt or FF_DASHBOARD=off)
. "$(dirname "$0")/lib.sh"
input=$(cat)
prompt=$(jq -r '.prompt // ""' <<<"$input")
cwd=$(jq -r '.cwd // "."' <<<"$input")
sid=$(jq -r '.session_id // ""' <<<"$input")
first=$(sed -nE '1s#^[[:space:]]*/((spec2pr|spec2pr):)?(ship|verify)-feature[[:space:]]+([^[:space:]]+).*#\4#p' <<<"$prompt")
grep -Eq '^[[:space:]]*/((spec2pr|spec2pr):)?(ship|verify)-feature([[:space:]]|$)' <<<"$prompt" || exit 0
root=$(ff_root "$cwd")
if [ -n "$first" ] && [ -n "$sid" ] && [ "${first#--}" = "$first" ]; then
  key=$(ff_key_from_arg "$first")
  grep -q -- '--no-jira' <<<"$prompt" && key=$(tr '[:upper:]' '[:lower:]' <<<"$key")
  if [ -n "$key" ]; then
    mkdir -p "$root/.agent-state/sessions" && echo "$key" > "$root/.agent-state/sessions/$sid"
  fi
fi
grep -q -- '--no-dashboard' <<<"$prompt" && exit 0
"$(dirname "$0")/ensure-dashboard.sh" "$root" 2>/dev/null
exit 0
