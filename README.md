# spec2pr

A Claude Code plugin that takes a feature from a Jira ticket, a GitHub issue or a one-line idea to a
reviewed, tested pull request, using the **Elephant–Goldfish** model.

- **Elephant:** `/ship-feature` runs in your main session. It talks to you, writes the spec
  (with [spec-kit](https://github.com/github/spec-kit)), keeps every decision in `spec.md`, and never
  edits code itself.
- **Goldfish:** every subagent starts with no memory and gets only file paths. A zero-context
  "goldfish" must be able to build from the spec alone before any code is written, and the reviewers
  judge the result without ever seeing the implementers' reasoning.

```
/ship-feature PROJ-123
  context-analyst ─► elephant writes spec ─► goldfish test ─┐ (FAIL: revise, max 3)
                                                            ▼
                                              YOU approve the plan
  implementer ×N ─► spec-guardian ─► tester ─► app-verifier ─► pr-reviewer ─► pr-shepherd ─► YOU merge
        ▲______________ gaps / failing ACs / blocking findings (max 3) ______|
```

**What you get**
- **One command from ticket to PR**, with a commit gate: nothing is committed or pushed until the spec
  check, the tests, the app check and the review all pass.
- **Checks in the real app:** web pages, Chrome extensions, Android emulator and iOS simulator, with
  screenshots per acceptance criterion (free local tools: Playwright and Maestro).
- **Each feature in its own git worktree**, so several features can run at once without touching your
  main checkout.
- **A live local dashboard:** every project in a side panel, the pipeline stages, a live flow of the
  agents and their tool calls, and "Needs you" cards you can answer with one click (plus a desktop
  notification).
- **Verify first, decide after:** `/verify-feature` checks work in progress and lets you choose what
  happens next.
- Jira / GitHub issue updates at each phase (optional).

## Install

Requirements: [Claude Code](https://claude.com/claude-code), `git`, `jq`, `python3`, `node`, and
[`gh`](https://cli.github.com) (logged in, for PRs). For Jira, Confluence or Figma input, connect those
connectors in Claude Code.

```bash
claude plugin marketplace add paulpriyam/spec2pr
claude plugin install spec2pr@spec2pr
```

Restart Claude Code, then in the repo you want to work on:

```bash
# spec-kit (once per machine, then once per repo)
uv tool install specify-cli --from git+https://github.com/github/spec-kit.git
specify init --here --ai claude
```

Optional:
- **App checks:** run `scripts/verify-env.sh setup web` from the plugin folder (add `extension`,
  `android` or `ios` as needed), or let the first feature that needs it ask you. Android needs Android
  Studio with an emulator, and iOS needs Xcode.
- **Status line:** see [Monitoring](#monitoring).
- **Clickable notifications on macOS:** `brew install terminal-notifier`.

To try it without installing: `git clone https://github.com/paulpriyam/spec2pr` and run
`claude --plugin-dir ./spec2pr`.

**Updating:** `claude plugin marketplace update spec2pr && claude plugin update spec2pr@spec2pr`,
then restart Claude Code.

**What stays on your machine:** each repo's `.agent-state/` (feature progress, git-ignored),
`.ff-worktrees/` (feature working copies) and `~/.spec2pr/` (dashboard project list, testing
tools). To move a feature to another machine, push its branch and run `/ship-feature KEY --adopt` there.

## Contents

| Path | What |
|---|---|
| `commands/ship-feature.md` | The orchestrator (elephant): `/ship-feature KEY` |
| `commands/verify-feature.md` | `/verify-feature`: check work in progress in the running app, then decide |
| `commands/feature-status.md` | `/feature-status [KEY]`: phase, verdicts, recent events |
| `commands/feature-dashboard.md` | `/feature-dashboard`: open the live dashboard |
| `commands/goldfish-check.md` | `/goldfish-check <spec>`: run the goldfish test on any doc |
| `agents/` | context-analyst, goldfish-spec-test, implementer, spec-guardian, tester, app-verifier, pr-reviewer, pr-shepherd, jira-sync |
| `hooks/hooks.json` | Commit/PR gate, subagent and tool-call log, dashboard auto-open |
| `dashboard/` | The local dashboard (Python standard library only, bound to 127.0.0.1) |
| `scripts/` | Worktrees, questions, environment setup for app checks, status line |

### Nested subagents
Only two agents can start other agents, and only of a fixed type:
- `pr-shepherd` → `implementer` (one per PR review thread, in parallel)
- `tester` → `tester` (split test writing when there are many ACs)

`goldfish-spec-test` and `pr-reviewer` deliberately cannot spawn or look things up beyond their
inputs; that independence is the point of them.

## Contributing

Issues and pull requests are welcome. If you change the plugin, raise `version` in
`.claude-plugin/plugin.json`: Claude Code only picks up a new version.

## License

[MIT](LICENSE)

## Usage

```text
/ship-feature PROJ-123                         # Jira ticket
/ship-feature #42                              # GitHub issue (progress comments + "Closes #42")
/ship-feature saved-cards "Let users save and delete cards"   # your own project, no ticket
/ship-feature saved-cards --doc notes/cards.md # your own project, requirements in a file
/ship-feature saved-cards --spec specs/012-saved-cards        # spec docs already exist
/ship-feature saved-cards --adopt              # mid-development: write spec from the code + this chat
/ship-feature saved-cards --adopt --fast       # same, skip the goldfish spec test
```
Options: `--from goldfish|implement|guardian|reviewer`, `--base <branch>`, `--no-dashboard`.

**It checks the feature in the real app.** After the tests pass, the `app-verifier` agent runs the
app and walks through every acceptance criterion like a user, taking screenshots as proof. They show
up per criterion on the dashboard. It covers web pages and Chrome extensions (Playwright), and the
Android emulator and iOS simulator (Maestro, or plain adb / simctl as a fallback). Failures go back to
the implementer like any other check, and the PR gets a "Checked in the running app" section.
Features with nothing user-visible are marked SKIP. All tools are free and local. The first time a
platform is needed you're asked once whether to install them; or do it yourself:
`scripts/verify-env.sh check` shows what's ready, and `scripts/verify-env.sh setup web|extension|android|ios`
installs it. Android needs Android Studio with an emulator, and iOS needs Xcode, both installed by
you. Maestro needs Java 17+: setup uses Android Studio's bundled Java if present, otherwise it
downloads Eclipse Temurin 17 into `~/.spec2pr/jdk` (no Homebrew or admin rights needed).

**Verify first, decide after.** In the middle of a feature, run `/verify-feature` (or
`/verify-feature <name>`). It finds or writes the spec, checks what is built so far in the running app,
shows the result per acceptance criterion, and asks: **Build the rest and ship** (fixes the failures and
unfinished tasks, then tests, review, PR), **Ship it** (when everything passed), or **Stop here**
(continue later with `/ship-feature <name> --from implement` or `--from guardian`). It works in your
current checkout; use `--url http://localhost:3000` to check an app you already have running, and
`--platform web|extension|android|ios` to pick what to check.

**Each feature gets its own working copy.** `/ship-feature` builds every new feature in a separate
git worktree at `<repo>/.ff-worktrees/<KEY>` on branch `ff/<KEY>`, so your main checkout stays
untouched and you can run several features at once (one Claude session each, same repo). Untracked
`.env*` files are copied in and dependencies installed automatically (pnpm/yarn/bun/npm, uv, poetry);
put a `.ff-setup` script in the repo root to customise that, or set `FF_WORKTREE_SETUP=none` to skip
it. When the feature is finished you're asked whether to remove the working copy (the branch is kept).
Use `--here` to work in the current checkout instead; `--adopt` always works in place.
`scripts/worktree.sh list` shows a repo's feature copies. Note: some tools that scan the whole repo
(e.g. a root-level `tsc` or jest without `roots`) may also see `.ff-worktrees/`; exclude it there.

## Monitoring

- **Live dashboard:** opens automatically when you run `/ship-feature` (opt out with `--no-dashboard`
  or `FF_DASHBOARD=off`). Reopen with `/feature-dashboard` or `scripts/dashboard.sh`. One server shows
  every project you've run `/ship-feature` in (registered in `~/.spec2pr/repos.txt`), but it opens
  on the project you launched it from (http://127.0.0.1:7788/?repo=<path>); use the Project selector to
  switch or pick "All projects". If the running server is older than the installed plugin, it is restarted
  automatically. To drop an old project from the list, delete its line in `repos.txt`. It refreshes every
  2 s: phase stepper, loop counts, verdicts, subagents running now, time per agent, event timeline.
  Python 3 stdlib only, bound to localhost.
- **Side panel:** every project is listed on the left with its status and a "Needs you" count. The one
  you launched the dashboard from is on top and selected; click any other to switch. "All projects"
  at the bottom gives the overview.
- **Live flow:** the "Live flow" tab shows, in order, what the elephant did (its tool calls), every
  subagent it started (side by side when they ran in parallel) with their tool calls, and each question
  you were asked and your answer. A "Now" card on top says what is running at this moment. Tool calls
  are recorded by a background hook (it never slows Claude down) into `.agent-state/tools.jsonl`, only
  while a pipeline is active; tokens, passwords and API keys in commands are blanked out.
- Task list in the session shows the phase checklist.
- **Needs you:** when the pipeline must wait on you (spec approval, open questions, a stuck loop, PR
  comments that need a human), you get a macOS notification and a "Needs you" card on the dashboard with
  buttons (Go / Change the spec / Stop). Answer there or in the terminal, whichever is closer. The status
  line shows `⚑ NEEDS YOU` and the browser tab shows the count. Set `FF_NOTIFY=off` to mute the
  notification; install `terminal-notifier` if you want clicking it to open the dashboard.
- **Status at a glance:** each feature shows Working / Needs you / Blocked / PR in review / Done and a
  "Now:" line ("Goldfish test of the spec, round 2/3"). "All projects" lists every project with its
  feature, status and last update; click a row to open it. All of it is worked out from `.agent-state/`.
- `/feature-status` for a summary at any time.
- `tail -f .agent-state/events.jsonl | jq` for the live subagent timeline (written by hooks).
- Jira comments from `jira-sync` at every phase.

State lives in `.agent-state/` in the repo (added to `.git/info/exclude`). The hooks only act while
`.agent-state/current` exists, so they don't affect normal work in other sessions.

## Customise

- Jira status names: edit the transitions in `commands/ship-feature.md` (steps 5 and 11).
- Models: `model:` in each agent's frontmatter (`opus`, `sonnet`, `haiku`).
- Loop limits and the `--fast` rule: `commands/ship-feature.md`.
- MCP tool names in `context-analyst` / `jira-sync` cover both the claude.ai Atlassian connector and
  a local `atlassian` MCP server; remove the one you don't use.

The commit gate is a guardrail against skipped steps, not a security boundary: the orchestrator writes
the verdicts it reads.
