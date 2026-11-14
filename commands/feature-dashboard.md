---
description: Open the live spec2pr dashboard (http://127.0.0.1:7788). /ship-feature opens it automatically; use this to reopen it.
allowed-tools: Bash
---
Run `"${CLAUDE_PLUGIN_ROOT}/scripts/ensure-dashboard.sh" "$PWD"`. If that path was not expanded or does
not exist, find the script with
`find ~/.claude/plugins /Users/*/Documents/GitHub -path '*/scripts/ensure-dashboard.sh' -path '*spec2pr*' 2>/dev/null | head -1`
and run that instead. It starts the server in the background if needed, registers this repo and
opens the browser on this project's view. Report the URL it prints (it ends in `?repo=<this repo>`).
