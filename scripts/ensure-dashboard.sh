#!/usr/bin/env bash
# Make sure the dashboard server is running, register a repo with it, and open it in the browser
# on that repo's view (http://127.0.0.1:7788/?repo=<path>).
# Usage: ensure-dashboard.sh <repo-dir> [--no-open]
# One server watches every registered repo (~/.feature-factory/repos.txt); the page shows one project at a
# time with a switcher. Set FF_DASHBOARD=off to disable.
[ "${FF_DASHBOARD:-on}" = "off" ] && exit 0
repo="${1:-$PWD}"; open_it=1; [ "$2" = "--no-open" ] && open_it=0
repo="$(cd "$repo" 2>/dev/null && pwd -P)" || exit 0
. "$(dirname "$0")/lib.sh"
repo="$(ff_root "$repo")"   # a feature worktree belongs to its main checkout's view
port="${FF_DASHBOARD_PORT:-7788}"
home="$HOME/.feature-factory"; mkdir -p "$home"
touch "$home/repos.txt"
grep -qxF "$repo" "$home/repos.txt" || echo "$repo" >> "$home/repos.txt"
url="http://127.0.0.1:$port"
root="$(cd "$(dirname "$0")/.." && pwd -P)"

up() { [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 1 "$url/api/state")" = "200" ]; }
start() {
  nohup python3 "$root/dashboard/server.py" --port "$port" >"$home/dashboard.log" 2>&1 &
  disown 2>/dev/null
  for _ in 1 2 3 4 5 6 7 8 9 10; do up && break; sleep 0.3; done
}

if up; then
  # Restart a server that runs older code or another copy of the plugin, so the page always matches.
  want="$(cat "$root/dashboard/server.py" "$root/dashboard/index.html" | shasum -a 256 | cut -d' ' -f1)"
  info="$(curl -s --max-time 1 "$url/api/info")"
  have="$(jq -r '.version // empty' <<<"$info" 2>/dev/null)"
  if [ "$have" != "$want" ]; then
    pid="$(jq -r '.pid // empty' <<<"$info" 2>/dev/null)"
    # Servers from before /api/info existed: find the python process listening on the port.
    [ -z "$pid" ] && pid="$(lsof -ti "tcp:$port" -sTCP:LISTEN 2>/dev/null | head -1)"
    if [ -n "$pid" ] && ps -p "$pid" -o command= 2>/dev/null | grep -q 'dashboard/server.py'; then
      kill "$pid" 2>/dev/null
      for _ in 1 2 3 4 5 6 7 8 9 10; do up || break; sleep 0.2; done
      start
    fi
  fi
else
  start
fi

page="$url/?repo=$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1],safe=""))' "$repo")"
[ $open_it = 1 ] && command -v open >/dev/null && open "$page" >/dev/null 2>&1
echo "feature-factory live dashboard: $page (project: $(basename "$repo"))"
