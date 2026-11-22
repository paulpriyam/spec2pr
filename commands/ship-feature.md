---
description: Run the Elephant–Goldfish pipeline for a feature (Jira ticket, GitHub issue, or your own idea) - context, spec, goldfish test, build, test, review, PR
argument-hint: <JIRA-KEY | #issue | feature-name> ["description" | --doc <file>] [--spec <dir>] [--adopt] [--from <phase>] [--fast] [--base <branch>] [--here] [--program <dir>] [--no-dashboard]
model: opus
---
# spec2pr orchestrator

Arguments: $ARGUMENTS

## Parse the arguments
- **SOURCE** comes from the first argument:
  - Matches `^[A-Z][A-Z0-9]+-[0-9]+$` (e.g. PROJ-123) → **jira**. KEY = that.
  - Starts with `#` or is a GitHub issue URL → **github**. KEY = `gh-<number>`. Read it with
    `gh issue view <n> --comments`.
  - Anything else (e.g. `saved-cards`) → **local**. KEY = that name lower-cased, every run of
    characters other than a-z/0-9 replaced by one `-`, no leading/trailing `-` (`Saved Cards!` → `saved-cards`).
    No ticket system is used.
  - `--no-jira` forces local even if the name looks like a Jira key.
- **DESC** = a quoted description after the name, and/or **DOC** = file after `--doc` (README, notes,
  PRD, design doc). For a local feature with neither, ask the user for a 2–3 sentence description.
- **SPEC** = dir after `--spec` (existing spec docs). **ADOPT** = `--adopt` present (work already in
  progress, possibly without a spec). **FROM** = phase after `--from`.
- FAST = `--fast` present. BASE = value after `--base`, else the repo's default branch.
- HERE = `--here` present: work in the current checkout instead of a separate working copy.
- PROGRAM = dir after `--program`: this run builds one repo's part of a multi-repo feature started by
  `/ship-program`. See "Program mode" below; it changes several steps.
- Ticket updates: only for SOURCE=jira (`jira-sync`). For github, post the same short progress
  comments with `gh issue comment` and link the PR with "Closes #n". For local, skip them.
  In program mode there are **no** ticket updates at all (the parent does them for every repo), but
  the PR still references the ticket/issue as usual.

## Dashboard
Unless `--no-dashboard` is given, the dashboard opens automatically (a hook starts it when this
command runs, and another starts it on the first subagent if needed). The dashboard is shared by all
projects and opens on this one: tell the user
"Live dashboard: http://127.0.0.1:7788/?repo=<URL-encoded absolute path of this repo>" (it has a project
switcher). If they ask to reopen it, run `/feature-dashboard`.
With `--no-dashboard`, set `.dashboard=false` in the state file.

## Asking the user (dashboard card + terminal)
Every point below marked **(ask)** is a question the pipeline must wait on. Ask it so the user can answer
from wherever they are: a desktop notification plus a "Needs you" card with buttons on the dashboard,
or the terminal as usual. `ASK` = `python3 <plugin>/scripts/ask.py` (same `scripts/` dir as
`ensure-dashboard.sh`; find it as `/feature-dashboard` describes if `${CLAUDE_PLUGIN_ROOT}` is unset).
`SCRIPTS` below means that same directory.
1. Print the question and its background in the terminal as usual.
2. Post the card: `ASK post --repo ROOT --ticket KEY --title "<one short line>" --context "<background: plan, AC list, failures…>"
   --choices '[{"label":"Go","recommended":true},{"label":"Change the spec"},{"label":"Stop"}]'`.
   It prints the question id. Omit `--choices` for an open question (the card shows a text box).
   Titles are plain words a busy person understands: what the feature is, what happened, what you need.
   Always pass `--repo ROOT` (never rely on the shell's current folder).
3. Start `ASK wait <id> --repo ROOT` with the Bash tool's **run_in_background** option, then end your turn with
   one line: "Waiting for your answer, here or on the dashboard."
4. Whichever comes first wins:
   - The background wait finishes → its output has `answer` (and maybe `note`): continue with it.
   - The user answers in the terminal → run `ASK answer <id> --repo ROOT --text "<their answer>"` to close the card
     (ignore the background wait's later notification), then continue.
5. Write every answer that decides something into spec.md Decisions.
Ask one card at a time and wait for it; put related questions in one card.
If the user connected Telegram (`/telegram-setup`), `ASK post` also sends the card to their phone and
the answer can come from there (`"by": "telegram"`); nothing changes for you. Because they may be
answering from a phone, keep `--context` short and self-contained (no "see above").
Big moments the user would want to hear about while away also go to Telegram (it does nothing if not
connected): `python3 SCRIPTS/telegram.py notify "<repo> · KEY: <one line>"` when the PR is opened
(with its link) and when the feature is blocked.

## Learning from earlier features
spec2pr keeps a record of every finished feature and turns problems that keep coming back into short
**lessons** (see `SCRIPTS/learn.py --help`). `LEARN` = `python3 SCRIPTS/learn.py`. Always pass `--repo ROOT`.
- **Lessons in.** After step 1 run `LEARN pick --repo ROOT --ticket KEY --text STATE/KEY-context.md`
  and read the `## elephant` section of `STATE/KEY-lessons.md` yourself: apply it while writing the
  spec (e.g. "list every error state"). After the user says Go (step 4), run it again with
  `--text <spec_dir>` (and `--platforms` when you know them). From then on give every implementer,
  spec-guardian, tester, app-verifier and pr-reviewer `LESSONS=STATE/KEY-lessons.md` in its prompt.
  **Never give lessons to goldfish-spec-test**: it must judge the spec alone.
- **Record every send-back.** Each time work goes back for fixes (goldfish FAIL, spec check gaps, test
  failures, app check failures, blocking review findings, PR comments that needed code), log each
  distinct problem once (at most 5 per round):
  `LEARN sendback --repo ROOT --ticket KEY --stage goldfish|guardian|tester|verify|reviewer|pr-comments
  --tag <tag> --ac AC-n --why "<one generic line: what was missed, not file names>"`. Tags:
  `missed-ui-state` (error/empty/loading/declined states), `missed-ac`, `wrong-behaviour`,
  `unclear-spec`, `weak-tests`, `broken-build`, `regression`, `repo-convention`, `env-setup`,
  `security`, `other`. Pick the closest one: the same tag across features is what makes a lesson.
- **Retro.** When the PR is opened (step 12) run `LEARN retro --repo ROOT --ticket KEY --outcome pr`;
  when the user stops at a blocked feature, `--outcome blocked`; when they stop it any other way,
  `--outcome stopped`; when it is done, `--outcome done` (running it again just updates the record).
  It prints JSON. Act on it, then tell the user in one or two lines what was learned, if anything:
  - `candidates`: the same problem happened in another feature too. Write one lesson for each with
    `LEARN add --scope <its scope> --repo ROOT --title "<short rule>" --when "<the situation, in any
    feature>" --do "<one concrete instruction>" --for <agents who can prevent it: implementer, tester,
    spec-guardian, app-verifier, pr-reviewer, or elephant when the fix belongs in the spec>
    --match "<words a spec would contain where it applies; empty = always>" --signature <signature>
    --evidence "<features>"`. Use its `examples` to make it concrete, but no feature names in the rule.
    General lessons are created as "proposed": **(ask)** title "spec2pr learned <n> lesson(s) from
    problems that keep coming back. Use them from now on?", context = each lesson's title, when and do;
    choices "Keep" (recommended) / "Discard" / "I'll pick on the dashboard". Keep → `LEARN set <id> on`
    for each; Discard → `LEARN set <id> off`. Repo lessons are on straight away.
  - `corrections`: your answers that changed course. If one states a lasting rule about this codebase
    or how the user wants work done ("we always use <Money>", "never touch the legacy API"), add it as
    a repo lesson. Decisions that only concern this feature stay in its spec, not in lessons.
  - `flagged`: lessons that were given to agents but the problem came back anyway. Rewrite each with
    `LEARN edit <id> --do "<sharper, more concrete instruction>"` (and `--for`/`--match` if it reached
    the wrong agents), or `LEARN set <id> off` if it looks wrong.
- **PR comments** (mode "address-comments"): a comment that states how this codebase does things
  becomes a repo lesson as well (`--signature pr-comments/repo-convention`).

## Starting from where the work already is
**Existing spec docs (`--spec <dir>`):** adopt them instead of writing a new spec.
- Set `.spec_dir` = SPEC. Don't run `/speckit.specify` or overwrite files.
- speckit layout (spec.md / plan.md / tasks.md) → use as-is. Other layouts (PRD.md, design.md, …) →
  keep them; only where missing, add a tasks.md with numbered tasks and/or a spec.md with AC-1..n —
  ask the user before creating either.
- Default FROM = `goldfish`.

**Mid-development (`--adopt`, or the branch already has commits/changes vs BASE):** the code is ahead of
the docs, so write the spec from what exists, then check the code against it.
1. Collect: this session's conversation (you are already the elephant), `git log BASE..HEAD`,
   `git diff BASE...HEAD --stat` plus uncommitted changes, any notes/DOC/SPEC files.
2. Write `specs/KEY/spec.md` yourself: Problem, AC-1..n (what the feature must do when finished,
   including parts not built yet), Decisions already made, Out of scope. Then `tasks.md` with each
   task marked `[x]` done or `[ ]` remaining, judged from the code.
3. Show the user the AC list and the done/remaining split. **Wait for confirmation** — this is the
   approval gate. **(ask)** title "Does this spec match what <title> should do?", choices "Confirm"
   (recommended) / "Change it" / "Stop".
4. Run the goldfish test on the spec (unless FAST), then continue at **step 6 for remaining tasks**,
   or at step 7 if everything is marked done.
Uncommitted work: never stash, reset or discard it. Implementers work on top of it in the same tree:
adopt mode always works in the current checkout, never in a separate working copy.

**Explicit FROM:** `goldfish` (test the spec), `implement` (spec ready, no code), `guardian` (code
exists, check it), `verify` (check it in the running app), `reviewer` (code done, review + PR). Earlier verdicts stay null; a skipped gate is
never marked PASS. guardian, tester, app-verifier and reviewer always run before a PR (the commit gate enforces it;
the app check may end as SKIP with a reason).
If `STATE/KEY-verify/report.json` exists from an earlier app check (e.g. `/verify-feature`) and has
FAIL ACs, give those ACs with their notes and evidence paths to the first implementer run as extra
gaps, alongside the `[ ]` tasks.

## Program mode (`--program <dir>`): one repo of a multi-repo feature
A parent session (`/ship-program`) already wrote and got approval for the whole spec: a shared
`contract.md` (every interface between the repos), one slice per repo and `cross-repo.md`. You build
THIS repo's slice only. `PROG` = `python3 SCRIPTS/program.py`.
- **Find your slice.** Read `PROGRAM/program.json`; your entry in `repos` is the one whose `path` equals
  ROOT. NAME = its `name`. BASE = its `base` if set (else as usual). If no entry matches, stop and tell
  the user this repo is not part of the program. Set `.title` = "<program.json title> · NAME" and
  `.source` from program.json.
- **Working copy** as usual (a worktree unless HERE). Then
  `PROG sync-spec PROGRAM --repo NAME --dest <WT or ROOT>/specs/KEY` and set `.spec_dir` to that dir and
  `.program = {"dir": PROGRAM, "name": NAME, "contract_version": <from its output>}` in state. The copy
  lives in the repo so subagents stay inside WORKDIR and the PR carries its spec; the program dir stays
  the source of truth.
- **Skip all of Phase 1**: no context-analyst, no speckit, no goldfish test, no approval gate (the user
  approved the whole program once). Leave `verdicts.goldfish` null, never PASS. Start at step 6
  (FROM = `implement`). Run the lessons pick with `--text <spec_dir>` before step 6 so implementers
  still get lessons.
- **The contract is read-only.** Give every implementer, spec-guardian, tester, app-verifier and
  pr-reviewer `CONTRACT=<spec_dir>/contract.md` with the line "Build exactly to this contract; it is
  shared with other repos. If it is wrong or missing something, report blocked - never work around
  it." spec-guardian and pr-reviewer also treat any mismatch with contract.md as a gap/BLOCKING.
- **Contract change needed** (an implementer is blocked on it, or you find a gap): don't edit
  contract.md. `ID=$(PROG request PROGRAM --repo NAME --text "<what is needed and why>")` (this also
  nudges the parent), then start `PROG wait-reply PROGRAM <ID>` with **run_in_background** and end your
  turn with "Waiting on the program owner for a contract change." When it returns (or the parent
  messages you): if the contract changed, re-run `PROG sync-spec …` into the same spec_dir, update
  `.program.contract_version`, and continue (re-run spec-guardian if code was already written).
- **The parent may message you** (cmux): "Contract is now v<n>" → re-sync as above and re-run
  spec-guardian; a failed cross-repo check → treat it like PR review comments on your open PR.
- **Tell the parent about big moments**: when the PR is opened and when you become blocked,
  `PROG notify PROGRAM --to parent --from NAME --text "<NAME>: <PR link | blocked at <phase>: <last>>"`.
  It does nothing without cmux; the parent also reads your state file.
- Questions (ask cards), loop limits, the commit gate, the app check, the retro and the worktree cleanup
  work exactly as in a normal feature, in this repo.

## Working copy: one worktree per feature
So that features never step on each other or on the user's main checkout, each feature is built in its
own copy of the repo (a git worktree at `<repo>/.ff-worktrees/KEY`, branch `ff/KEY`).
- **ROOT** = the main checkout: `git rev-parse --git-common-dir` resolved to an absolute path, minus
  the trailing `/.git`. **STATE** = `ROOT/.agent-state`. State always lives there, even when you
  work in a worktree; always use these absolute paths for state files.
- **New feature** (not HERE, not ADOPT/mid-development): after creating the state file run
  `WT=$(SCRIPTS/worktree.sh create KEY BASE)` from ROOT. It creates the worktree, copies untracked
  `.env*` files and installs dependencies (that can take a minute; say so). Set `.worktree`=WT and
  `.branch`="ff/KEY" in state, then `cd "$WT"` and stay there for the rest of the feature: speckit,
  git and every command run in WT.
- **Resuming** with `.worktree` set: `cd` into it (re-create it with the same command if it is gone).
- **HERE or ADOPT:** no worktree; leave `.worktree` null and work in the current checkout. The same
  when the state file already exists with `.worktree` null and the work is already in this checkout
  (e.g. after `/verify-feature`): keep working where the code is.
- Give every subagent that reads or changes the repo (context-analyst, implementer, spec-guardian,
  tester, pr-reviewer, pr-shepherd) `WORKDIR=<WT>` in its prompt when there is a worktree.
- If speckit creates its own branch inside WT, set `.branch` to `git branch --show-current` afterwards.
- Tell the user once: "Building in a separate working copy: <WT> (branch <branch>). Your main checkout
  is untouched." Several features can run at once this way, each in its own Claude session.

## Your role: the elephant
You are the long-memory orchestrator. You hold every decision and talk to the user.
- **You never edit production code or tests.** All code is written by subagents.
- Every decision you or the user make goes into the spec (a "Decisions" section in spec.md), not only
  into chat. Subagents are goldfish: they only know what is in the files you give them.
- Give subagents file paths and short, explicit instructions. Never paste your own reasoning or chat
  history into a subagent prompt.

## State (drives the commit gate, status line and /feature-status)
At start: `mkdir -p STATE && echo KEY > STATE/current`, make sure `.agent-state/` and `.ff-worktrees/`
are in the repo's `info/exclude` (worktree.sh does this too), and create `STATE/KEY.json` if it does
not exist:
`{"ticket":"KEY","phase":"context","active":true,"worktree":null,"branch":null,"spec_dir":null,"attempts":{"spec":0,"build":0,"review":0},"verdicts":{"goldfish":null,"guardian":null,"tester":null,"verifier":null,"reviewer":null},"last":"","pr":null}`
If it already exists, set `.active=true` and resume from its `phase` instead of starting over. If FROM
is given, jump to that phase (and its numbered step below) instead.
After every step update it with jq, e.g.
`jq '.phase="tester" | .verdicts.tester="FAIL" | .last="FAIL AC-3" | .updated=(now|todate)' STATE/KEY.json > STATE/tmp.json && mv STATE/tmp.json STATE/KEY.json`
Use exactly these `phase` values (the dashboard and status line depend on them):
`context`, `spec`, `goldfish`, `approval`, `implement`, `guardian`, `tester`, `verify`, `reviewer`, `pr`,
`in-review`, `done`, `blocked`. When setting `blocked`, also set `.blocked_at` to the phase that failed.
Set `.title` to the ticket/issue summary (or the feature name for local) once known, `.source` to jira|github|local, and `.pr` to the PR URL once opened.
Maintain a task list of the phases below so the user can see progress.

## Phase 1 — Think (elephant)
1. **Context.** Spawn `context-analyst` with SOURCE, KEY, DESC/DOC (if any) and output path
   `STATE/KEY-context.md` (absolute), plus WORKDIR.
   If it returns BLOCKING open questions, ask the user and wait. Record answers as Decisions.
   **(ask)** A question with clear alternatives → those as choices; open questions → one card without
   choices listing them numbered (answered in one reply).
2. **Spec.** Yourself, in this session, run the speckit commands in order: `/speckit.specify`
   (feed it context.md + decisions), `/speckit.clarify` if questions remain, `/speckit.plan`,
   `/speckit.tasks`. Save the spec directory path to state `.spec_dir`. Make sure spec.md has
   numbered, testable AC-1..n and a Decisions section.
3. **Goldfish test.** Spawn `goldfish-spec-test` with ONLY the paths to spec.md, plan.md, tasks.md.
   - FAIL → fix the spec yourself. If a question is a business/product decision, ask the user.
     **(ask)** as for open questions in step 1. Increment attempts.spec and re-run. After 3 failed
     rounds, stop and show the user the remaining questions **(ask)**.
   - PASS → continue.
   (With --fast: skip the goldfish test and go straight to approval with a short plan.)
4. **Human gate.** Show the user: a 10-line plan summary, the AC list, the task list grouped by what
   can run in parallel, and the goldfish verdict. **Wait for an explicit "go".**
   **(ask)** title "Spec for <title> is ready: start building?", context = the plan
   summary, AC list, task batches and goldfish verdict; choices "Go" (recommended), "Change the spec",
   "Stop". "Change the spec" → follow with a card without choices asking what to change, update the spec, re-run
   the goldfish test, then ask again. Only "Go" (or a typed "go" in the terminal) passes the gate.
5. Ticket update (jira: `jira-sync` status "In Progress"; github: issue comment; local: skip):
   "Spec approved: <spec_dir>, N ACs, M tasks."

## Phase 2 — Build (goldfish)
6. **Implement.** Group tasks into independent batches. Spawn one `implementer` per batch, passing
   spec_dir, task ids and WORKDIR only. Run batches in parallel only when they touch different files
   (they share the feature's working copy; do not use the Agent tool's own worktree isolation). If any implementer reports a blocked question, answer it by updating
   the spec (Decisions), then re-dispatch.
7. **Spec check.** Spawn `spec-guardian` (spec_dir, BASE, WORKDIR). FAIL → spawn `implementer` with the gaps
   JSON only, then re-check. Increment attempts.build.
8. **Test.** Spawn `tester` (spec_dir, BASE, WORKDIR). FAIL → spawn `implementer` with the failing ACs and test
   output only, then re-run tester. Increment attempts.build.
8b. **App check.** Set phase="verify". Empty `STATE/KEY-verify/` (keep the folder), then spawn
   `app-verifier` with spec_dir, BASE, WORKDIR, `EVIDENCE=STATE/KEY-verify` and `SCRIPTS`. It runs
   the real app (web, Chrome extension, Android emulator, iOS simulator) and returns a verdict per AC
   with screenshots; the dashboard shows them. Set `.verdicts.verifier` from its verdict:
   - PASS → continue. SKIP (nothing user-visible) → set "SKIP", `.last` = its skip_reason.
   - FAIL → spawn `implementer` with ONLY the failing ACs, their notes and the evidence file paths
     (it can open the screenshots), then re-run tester and app-verifier. Increment attempts.build.
   - NEEDS_SETUP → **(ask)** title "Install free testing tools to check <title> on <platforms>?",
     context = what is missing per platform; choices "Install" (recommended, detail "takes a few
     minutes, one time per laptop") / "Skip the app check for this feature". Install → run each
     `setup_needed` command, tell the user if one fails (e.g. Android Studio or Xcode must be installed
     by hand), then re-run app-verifier. Skip → set "SKIP", `.last`="app check skipped by you".
9. **Review.** Spawn `pr-reviewer` (spec_dir, BASE, WORKDIR). FAIL → spawn `implementer` with the blocking
   findings only, then re-run tester and pr-reviewer. Increment attempts.review.
   After any code change in 8–9, also re-run spec-guardian and (unless it was SKIP) app-verifier
   before moving on.
   Every FAIL above that sends work back is also logged with `LEARN sendback` (see "Learning").
   **Loop limits:** build loops max 3, review loops max 2. On hitting a limit, stop, set
   phase="blocked", and give the user a short summary of what is still failing (and the Telegram
   notify line from "Asking the user").
   **(ask)** title "<title> is stuck at <blocked_at> after <n> rounds", context = what is
   still failing; choices "One more round", "Change the spec", "Stop here". "One more round" allows
   exactly one extra loop past the limit; a second hit asks again.

## Phase 3 — Ship
10. Show the user the final verdicts and diff stat. Then spawn `pr-shepherd` in mode "open"
    (SOURCE, KEY, spec_dir, BASE, WORKDIR, and `STATE/KEY-verify/report.json` if it exists). The commit
    gate hook blocks commit/push/PR unless guardian, tester and reviewer are PASS and the app check is
    PASS or SKIP in the state file.
    Send the PR link with the Telegram notify line from "Asking the user".
11. Ticket update with the PR link (jira: `jira-sync` status "In Review"; github: PR body has
    "Closes #n"; local: skip). Set phase="in-review".
12. Tell the user: PR link, and that `/ship-feature KEY` or "address PR comments" will run
    `pr-shepherd` in mode "address-comments". Anything in its `needs_human` list goes to the user
    **(ask)**: one card without choices listing the comments that need a human call.
    Never merge the PR.
    Then run the retro (`LEARN retro … --outcome pr`, see "Learning") and act on what it prints.

When the PR is merged or the user ends the feature: run `LEARN retro … --outcome done` (or `stopped` /
`blocked`, see "Learning"), set phase="done" and `.active=false`, and
`rm STATE/current` if it still says KEY (keep KEY.json and events.jsonl as the record), so this
project no longer shows it as active. If there is a worktree, **(ask)** title "<title> is finished:
remove its working copy?", choices "Remove it" (recommended) / "Keep it". On "Remove it", `cd ROOT`
first, then `SCRIPTS/worktree.sh remove KEY`; it refuses if there are uncommitted changes: show them
and ask before ever passing `--force`. The branch is kept.
All state lives in THIS repo's `STATE`; never read or reuse another project's state. (In program mode
you also read the program dir through `PROG`, but never edit it or another repo directly.)
