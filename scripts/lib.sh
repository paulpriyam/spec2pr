#!/usr/bin/env bash
# Shared helpers for spec2pr scripts. Source it: . "$(dirname "$0")/lib.sh"
#
# State always lives in the MAIN checkout's .agent-state/, even when work happens in a feature
# worktree (<root>/.ff-worktrees/<KEY>), so the dashboard, the commit gate and every session agree.

# ff_root DIR -> the main checkout of the git repo containing DIR (DIR itself outside git).
ff_root() {
  local dir="${1:-$PWD}" common
  common=$(cd "$dir" 2>/dev/null && cd "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null && pwd -P)
  case "$common" in
    */.git) dirname "$common" ;;
    *) (cd "$dir" 2>/dev/null && pwd -P) || echo "$dir" ;;
  esac
}

# ff_key ROOT CWD [SESSION_ID] [COMMAND] -> the active feature KEY for this context, or nothing.
# Candidates, in order: cwd inside a feature worktree; a worktree path named in the command; this
# session's feature (written by on-prompt.sh); the repo's most recent feature (.agent-state/current).
# The first candidate with a state file wins. If none has one, a worktree or `current` candidate is
# still returned (the gate then refuses: state is missing); a session guess alone is ignored.
# A feature whose state says "active": false counts as no feature.
ff_key() {
  local root="$1" cwd="$2" sid="$3" cmd="$4" sd="$1/.agent-state" k from_wt="" from_cmd="" from_sid="" from_cur=""
  case "$(cd "$cwd" 2>/dev/null && pwd -P)/" in
    "$root/.ff-worktrees/"*) from_wt="${cwd#"$root/.ff-worktrees/"}"; from_wt="${from_wt%%/*}" ;;
  esac
  [ -n "$cmd" ] && from_cmd=$(grep -oE '\.ff-worktrees/[A-Za-z0-9._-]+' <<<"$cmd" | head -1 | cut -d/ -f2)
  [ -n "$sid" ] && [ -f "$sd/sessions/$sid" ] && from_sid=$(tr -d '[:space:]' < "$sd/sessions/$sid")
  [ -f "$sd/current" ] && from_cur=$(tr -d '[:space:]' < "$sd/current")
  local key=""
  for k in "$from_wt" "$from_cmd" "$from_sid" "$from_cur"; do
    [ -n "$k" ] && [ -f "$sd/$k.json" ] && { key="$k"; break; }
  done
  [ -z "$key" ] && key="${from_wt:-${from_cmd:-$from_cur}}"
  [ -z "$key" ] && return 0
  [ "$(jq -r 'if .active == false then "off" else "on" end' "$sd/$key.json" 2>/dev/null)" = "off" ] && return 0
  echo "$key"
}

# ff_key_from_arg ARG -> KEY exactly as /ship-feature derives it from its first argument.
ff_key_from_arg() {
  local a="$1"
  if [[ "$a" =~ ^[A-Z][A-Z0-9]+-[0-9]+$ ]]; then echo "$a"
  elif [[ "$a" =~ ^#([0-9]+)$ ]] || [[ "$a" =~ /issues/([0-9]+) ]]; then echo "gh-${BASH_REMATCH[1]}"
  else tr '[:upper:]' '[:lower:]' <<<"$a" | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//'
  fi
}
