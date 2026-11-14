---
description: Check work in progress in the running app (web, Chrome extension, Android, iOS) against its spec, show the result per AC, then choose - build the rest and ship, ship as is, or stop
argument-hint: [KEY | feature-name] [--spec <dir>] [--platform web|extension|android|ios] [--url <already running app>] [--base <branch>]
model: opus
---
# feature-factory: verify first, decide after

Arguments: $ARGUMENTS

The user is in the middle of a feature and wants to see how far it really works **before** deciding
what to do next. You are the elephant (see `commands/ship-feature.md` in this plugin, next to this
file: read it now, you will follow parts of it). Same rules: you never edit production code or tests.
Uncommitted work: never stash, reset or discard it.

## 1. Which feature, where
- **ROOT**, **STATE**, **SCRIPTS**, **ASK**: as defined in ship-feature.md.
- **KEY**: the first argument (derived exactly as ship-feature.md says), else the feature whose
  working copy you are in (`…/.ff-worktrees/<KEY>`), else `STATE/current`, else the current branch
  name lower-cased and hyphenated. Tell the user which KEY you picked.
- **WORKDIR**: `.worktree` from `STATE/KEY.json` if set and it exists, else the current checkout
  (`git rev-parse --show-toplevel`). Do not create a worktree here: the work is where it is.
- **BASE**: `--base`, else the repo's default branch.
- If `STATE/KEY.json` does not exist, create it as ship-feature.md describes, with `"phase":"verify"`,
  `"active":true`, `"worktree":null`, `"source":"local"` (or jira/github from the KEY's form), and
  `echo KEY > STATE/current`. If it exists, keep everything and only set `.active=true`.

## 2. A spec to check against
- Use `--spec <dir>`, else `.spec_dir` from state, else a `specs/*/spec.md` that clearly belongs to
  this feature (name or branch match).
- None found → write one from what exists, exactly like ship-feature.md "Mid-development" steps 1–2
  (this conversation, `git log/diff BASE`, uncommitted changes, notes): `specs/KEY/spec.md` with
  Problem, AC-1..n (what the finished feature must do, including parts not built yet), Decisions, Out
  of scope; and `tasks.md` with `[x]` done / `[ ]` remaining.
  **(ask)** title "Is this what <title> should do?", context = the AC list and done/remaining split,
  choices "Looks right" (recommended) / "Change it" / "Stop". Wait for the answer.
- Set `.spec_dir`, `.title`.

## 3. Run the app check
Set phase="verify". Empty `STATE/KEY-verify/` (keep the folder). Spawn `app-verifier` with spec_dir,
BASE, WORKDIR, `EVIDENCE=STATE/KEY-verify`, SCRIPTS, and `--platform` / `--url` if given (with
`--url`, it uses the app that is already running and does not start or stop it).
- NEEDS_SETUP → the same **(ask)** as ship-feature.md step 8b (Install / Skip). Skip → stop here and
  tell the user the check could not run.
- Record `.verdicts.verifier` and `.last` (e.g. "app check: 4 pass, 2 fail, 1 not verifiable").

## 4. Show the result, then let the user decide
Print a short table: AC | result | note (what was expected vs seen for failures), plus other issues,
the screenshots folder and the dashboard link. Then count the open work: failing ACs, plus `[ ]`
tasks in tasks.md. **(ask)** title "<title>: <n> of <m> checks pass in the app. What next?",
context = the table; choices:
- "Build the rest and ship" (recommended when anything failed or tasks remain; detail "fixes the
  failures, builds unfinished tasks, then tests, review and PR")
- "Ship it" (recommended when everything passed; detail "spec check, tests, review, PR")
- "Stop here" (detail "keep the result; run /ship-feature KEY later")

Then:
- **Build the rest and ship** → continue in this session by following `commands/ship-feature.md` with
  `KEY --from implement` (and `--here` unless state has a worktree). The failing ACs from the app check
  go to the implementers as extra gaps, alongside the `[ ]` tasks.
- **Ship it** → follow `commands/ship-feature.md` with `KEY --from guardian`.
- **Stop here** → leave state as is (phase "verify"), and tell the user they can continue any time
  with `/ship-feature KEY --from implement` or `/ship-feature KEY --from guardian`.
