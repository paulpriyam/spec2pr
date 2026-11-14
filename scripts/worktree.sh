#!/usr/bin/env bash
# Each feature gets its own copy of the repo (a git worktree), so features don't step on each other
# or on your main checkout.
#
#   worktree.sh create KEY [BASE]   -> prints the worktree path (reuses it if it already exists)
#   worktree.sh path   KEY          -> prints the worktree path if it exists
#   worktree.sh remove KEY [--force] -> removes the worktree (refuses if it has uncommitted changes)
#   worktree.sh list                -> feature worktrees of this repo
#
# Worktrees live in <repo>/.ff-worktrees/<KEY> (git-excluded). The branch is ff/<KEY>, created from
# BASE (default: the repo's default branch). On create it copies untracked .env files from the main
# checkout and installs dependencies. Override the install with a .ff-setup script in the repo root
# (run inside the new worktree) or FF_WORKTREE_SETUP="<command>"; set FF_WORKTREE_SETUP=none to skip.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
cmd="${1:-}"; key="${2:-}"
root="$(ff_root "$PWD")"
git -C "$root" rev-parse --git-dir >/dev/null 2>&1 || { echo "worktree.sh: $root is not a git repo" >&2; exit 1; }
wt_dir="$root/.ff-worktrees"
need_key() { [ -n "$key" ] || { echo "worktree.sh: $cmd needs a KEY" >&2; exit 1; }; }

default_base() {
  local b
  b=$(git -C "$root" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##') || true
  [ -n "$b" ] && { echo "$b"; return; }
  for b in main master develop; do git -C "$root" rev-parse --verify --quiet "$b" >/dev/null && { echo "$b"; return; }; done
  git -C "$root" branch --show-current
}

setup() {
  local wt="$1"
  # Untracked env files are not in git, so a fresh worktree would not have them.
  for f in "$root"/.env "$root"/.env.*; do
    [ -f "$f" ] || continue
    git -C "$root" ls-files --error-unmatch "$(basename "$f")" >/dev/null 2>&1 && continue
    [ -e "$wt/$(basename "$f")" ] || cp "$f" "$wt/"
  done
  local how="${FF_WORKTREE_SETUP:-}"
  [ "$how" = "none" ] && return 0
  if [ -z "$how" ]; then
    if [ -x "$root/.ff-setup" ]; then how="$root/.ff-setup"
    elif [ -f "$wt/pnpm-lock.yaml" ]; then how="pnpm install --frozen-lockfile"
    elif [ -f "$wt/yarn.lock" ]; then how="yarn install --frozen-lockfile"
    elif [ -f "$wt/bun.lockb" ] || [ -f "$wt/bun.lock" ]; then how="bun install"
    elif [ -f "$wt/package-lock.json" ]; then how="npm ci"
    elif [ -f "$wt/uv.lock" ]; then how="uv sync"
    elif [ -f "$wt/poetry.lock" ]; then how="poetry install"
    fi
  fi
  [ -z "$how" ] && return 0
  echo "worktree.sh: setting up with: $how" >&2
  (cd "$wt" && bash -c "$how") >&2 || echo "worktree.sh: setup failed; the worktree is there, fix the install inside it" >&2
}

case "$cmd" in
  create)
    need_key
    base="${3:-$(default_base)}"
    wt="$wt_dir/$key"
    exclude="$(git -C "$root" rev-parse --git-common-dir)"; exclude="$(cd "$root" && cd "$exclude" && pwd -P)/info/exclude"
    mkdir -p "$(dirname "$exclude")"; touch "$exclude"
    for pat in ".ff-worktrees/" ".agent-state/"; do grep -qxF "$pat" "$exclude" || echo "$pat" >> "$exclude"; done
    if [ -d "$wt" ]; then echo "$wt"; exit 0; fi
    mkdir -p "$wt_dir"
    branch="ff/$key"
    if git -C "$root" rev-parse --verify --quiet "refs/heads/$branch" >/dev/null; then
      git -C "$root" worktree add "$wt" "$branch" >&2
    else
      git -C "$root" worktree add -b "$branch" "$wt" "$base" >&2
    fi
    setup "$wt"
    echo "$wt"
    ;;
  path)
    need_key; [ -d "$wt_dir/$key" ] && echo "$wt_dir/$key" || exit 1
    ;;
  remove)
    need_key; wt="$wt_dir/$key"
    [ -d "$wt" ] || { echo "worktree.sh: no worktree for $key" >&2; exit 0; }
    if [ "${3:-}" != "--force" ] && [ -n "$(git -C "$wt" status --porcelain)" ]; then
      echo "worktree.sh: $wt has uncommitted changes; not removing (use --force to discard them)" >&2; exit 1
    fi
    git -C "$root" worktree remove ${3:+--force} "$wt" >&2
    echo "removed $wt (branch $(git -C "$root" rev-parse --abbrev-ref "ff/$key" 2>/dev/null || echo "ff/$key") kept)"
    ;;
  list)
    git -C "$root" worktree list --porcelain | awk -v d="$wt_dir/" '/^worktree /{p=$2} /^branch /{if (index(p,d)==1) print p"\t"substr($2,12)}'
    ;;
  *) sed -n '2,12p' "$0"; exit 1 ;;
esac
