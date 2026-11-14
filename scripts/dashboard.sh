#!/usr/bin/env bash
# Start/open the dashboard from any terminal:  scripts/dashboard.sh [repo-dir]   (default: current dir)
exec "$(dirname "$0")/ensure-dashboard.sh" "${1:-$PWD}"
