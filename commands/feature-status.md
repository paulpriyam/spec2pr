---
description: Show where the current feature-factory pipeline is - phase, verdicts, loops and recent agent events
argument-hint: [JIRA-KEY]
model: haiku
allowed-tools: Bash(cat:*), Bash(jq:*), Bash(tail:*), Bash(ls:*), Bash(git rev-parse:*)
---
State lives in the MAIN checkout's `.agent-state/` (STATE), even inside a feature working copy: STATE is
`<git rev-parse --git-common-dir, made absolute, minus /.git>/.agent-state`.
Show the pipeline status for $ARGUMENTS, else for the feature whose working copy you are in
(`.../.ff-worktrees/<KEY>`), else the key in `STATE/current`.

Read `STATE/<KEY>.json` and the last 15 lines of `STATE/events.jsonl` filtered to that
ticket. Present:
- One line: ticket │ phase │ loop counts │ last result
- A checklist of phases (context, spec, goldfish, approval, implement, spec-check, test, review, PR)
  with ✅ / 🔄 / ❌ / ⬜ based on phase and verdicts
- The recent events as a short timeline (time, start/stop, agent)
- If phase is "blocked", what is failing
- The working copy (`.worktree`, `.branch`) if there is one
- Other active features in this repo (`"active": true` in other STATE/*.json), one line each
Do not change anything.
