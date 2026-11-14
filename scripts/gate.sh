#!/usr/bin/env bash
# PreToolUse(Bash) guard. While a feature pipeline is active, blocks git commit / git push /
# gh pr create until spec-guardian, tester and pr-reviewer all PASS for that feature.
# Outside a pipeline it does nothing. Exit 2 = block, stderr is shown to Claude.
. "$(dirname "$0")/lib.sh"
input=$(cat)
cmd=$(jq -r '.tool_input.command // ""' <<<"$input")
echo "$cmd" | grep -Eq '(^|[;&|[:space:]])(git[[:space:]]+(-C[[:space:]]+[^[:space:]]+[[:space:]]+)?(commit|push)|gh[[:space:]]+pr[[:space:]]+create)' || exit 0
cwd=$(jq -r '.cwd // "."' <<<"$input")
sid=$(jq -r '.session_id // ""' <<<"$input")
root=$(ff_root "$cwd")
key=$(ff_key "$root" "$cwd" "$sid" "$cmd")
[ -n "$key" ] || exit 0

state="$root/.agent-state/$key.json"
[ -f "$state" ] || { echo "feature-factory: no state file for $key; cannot verify gates." >&2; exit 2; }

missing=()
for gate in guardian tester reviewer; do
  v=$(jq -r ".verdicts.$gate // \"none\"" "$state")
  [ "$v" = "PASS" ] || missing+=("$gate=$v")
done
# App check: required for features started with it (state has a "verifier" verdict); SKIP is allowed.
if [ "$(jq -r '.verdicts | has("verifier")' "$state")" = "true" ]; then
  v=$(jq -r '.verdicts.verifier // "none"' "$state")
  [ "$v" = "PASS" ] || [ "$v" = "SKIP" ] || missing+=("app-check=$v")
fi
if [ ${#missing[@]} -gt 0 ]; then
  echo "feature-factory gate: $key is not ready to commit/PR. Not passed: ${missing[*]}. Run the missing steps first." >&2
  exit 2
fi
exit 0
