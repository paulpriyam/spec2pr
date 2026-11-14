---
description: Run the zero-context goldfish test on any spec or design doc
argument-hint: <path-to-spec-or-dir>
---
Spawn the `goldfish-spec-test` subagent with ONLY these paths: $ARGUMENTS
(if a directory, pass its spec.md, plan.md and tasks.md if present, otherwise its .md files).
Do not add any context of your own to its prompt beyond the file paths.

Show the user its verdict, then for each question/assumption/contradiction suggest the exact line
to add or change in the doc. Don't edit the doc unless the user asks.
