---
description: Ship one feature that needs changes in several repos (e.g. Android app + webview) - one shared spec and contract, one approval, then one /ship-feature session per repo, each in its own cmux workspace
argument-hint: <JIRA-KEY | #issue | feature-name> ["description" | --doc <file>] [--repo [name=]<path> ...] [--fast] [--print]
model: opus
---
# spec2pr program orchestrator (the parent elephant)

Arguments: $ARGUMENTS

A **program** is one feature that spans several repos. The spec is written **once** (a shared contract
plus one slice per repo) and approved **once**. Then each repo is built by its own ordinary
`/ship-feature` session (a "child") in its own cmux workspace, so the user can watch every repo's
work and diff separately. You stay the parent: you own the contract, the ticket and the final join.
Children never change the contract themselves.

## Parse the arguments
- KEY, SOURCE, DESC, DOC: exactly as `/ship-feature` derives them (Jira key → jira; `#n` or issue URL →
  github, KEY `gh-<n>`; anything else → local slug; `--no-jira` forces local).
- REPOS = each `--repo [name=]<path>`. Name defaults to a short form of the folder name (lower-case,
  `a-z0-9-`, e.g. `myequal-ai-webviews-app` → `webview`, `myequal-ai-app` → `android`); ask if unclear.
- FAST = `--fast` (skip the goldfish test). PRINT = `--print` (don't create cmux workspaces, print the
  commands for the user to run instead; also used automatically when cmux isn't available).
- `SCRIPTS` = this plugin's `scripts/` dir (as in `/ship-feature`). `PROG` = `python3 SCRIPTS/program.py`.
  `ASK` = `python3 SCRIPTS/ask.py`, used exactly as `/ship-feature` describes in "Asking the user",
  always with `--repo P` (below). `LEARN` is not used by the parent.

## Program home and state
1. `P=$(PROG init KEY --title "<title or KEY>" --source <SOURCE>)` → `~/.spec2pr/programs/KEY`. It also
   records this session's cmux workspace so children can nudge you.
2. `cd "$P"` and stay there for the whole program (use absolute paths for repos). This ties this session's
   hooks, questions and events to the program, and the dashboard shows it as its own project:
   run `SCRIPTS/ensure-dashboard.sh "$P"` and tell the user the link it prints.
3. Your own state file is `P/.agent-state/KEY.json`, same shape as `/ship-feature`'s plus
   `"program": true`, and `echo KEY > P/.agent-state/current`. Use only these phases there:
   `context`, `spec`, `goldfish`, `approval`, `implement` (children are building), `verify` (cross-repo
   check), `in-review`, `done`, `blocked`. `program.json` is managed by `PROG`; never hand-edit its
   `repos` list (use `PROG add-repo`), but do set its `"phase"` with jq when yours changes.
If `P/program.json` already exists with launched children, you are resuming: skip to **Watch**.

## Phase 1 — Think (one spec for every repo)
1. **Repos.** For each REPO: `PROG add-repo "$P" --name <name> --path <abs path> [--base <branch>]`.
   No `--repo` given → read the ticket/description, look at sibling folders of the current directory
   and propose the repos that must change **(ask)** title "Which repos does <title> touch?", choices =
   your proposal (recommended) and "Let me list them". A repo you are unsure about is better asked than
   guessed.
2. **Context.** Spawn one `context-analyst` per repo **in parallel**, each with SOURCE, KEY, DESC/DOC,
   `WORKDIR=<repo path>`, output `P/.agent-state/KEY-context-<name>.md`, and the line "Focus on what THIS
   repo must change for the feature, and on every place it talks to the other repos (<names>): API
   calls, JS bridge methods, deep links, events, shared payloads." Blocking open questions → **(ask)**.
3. **Contract first.** Write `P/contract.md` yourself: every interface between the repos — for each,
   who calls whom, name, exact payload/response shapes (field names, types, optional/required), error
   cases, versioning/feature-flag behaviour, and what happens when one side is older than the other
   (old app + new webview and the reverse). End it with `## Changelog` (`v1: initial`). This is the
   single source of truth both sides build against.
4. **Slices.** For each repo write `P/<name>/spec.md`, `plan.md`, `tasks.md` in speckit layout
   **yourself** (do not run `/speckit.*` here: it needs a git repo and creates branches). spec.md has
   numbered testable AC-1..n for THIS repo only, a Decisions section, and "Contract: see contract.md"
   (never restate the contract differently). tasks.md has numbered tasks with the files they touch.
5. **Cross-repo.** Write `P/cross-repo.md`: end-to-end ACs (X-AC-1..n) that only hold when both sides
   work together, how to check them (e.g. a project skill such as `webview-paywall-flow`, or manual
   steps), and the **merge/release order** (which side must ship first, flags that protect old clients).
6. **Goldfish test** (unless FAST). Spawn `goldfish-spec-test` with ONLY the paths to contract.md,
   cross-repo.md and every slice's spec.md/plan.md/tasks.md, and the line "These are the specs for
   several repos that must work together; also fail it if two slices could implement the contract
   differently." FAIL → fix, re-run, at most 3 rounds, as in `/ship-feature` step 3.
7. **One human gate for everything.** **(ask)** title "Spec for <title> across <n> repos is ready:
   start building?", context = contract summary (one line per interface), per repo its AC list and task
   count, the cross-repo ACs, merge order and the goldfish verdict; choices "Go" (recommended), "Change
   the spec", "Stop". Only Go passes. This approval covers every child: children do not ask again.
8. Ticket update (jira: `jira-sync` "In Progress"; github: issue comment; local: skip):
   "Spec approved for <n> repos: <names>. Building each in its own session." Children never update the
   ticket; you do it for all of them.

## Phase 2 — Fan out (one session per repo)
9. `PROG launch "$P"` (add `--print` if PRINT). It refuses if contract.md or a slice is missing.
   - With cmux it creates one workspace per repo named `KEY · <name>`, each running
     `claude '/spec2pr:ship-feature KEY --program <P>'` in that repo. Tell the user the workspace names
     and that each one is a normal spec2pr run they can watch or talk to; `PROG diff "$P"` opens every
     repo's diff side by side.
   - Without cmux it prints one command per repo: show them and ask the user to run each in its own
     terminal tab.
   Set your phase to `implement`.

## Watch (until every child has opened its PR)
10. Run `PROG status "$P"` once, then start `PROG wait "$P"` with the Bash tool's **run_in_background**
    option and end your turn with one line: "<n> repos building; I'll step in when one needs me."
    Children (and cmux) may also wake you with a message; treat it the same way: re-run `PROG status`.
    When the wait returns, act on its `reasons`, then start a new wait (unless all children are done):
    - `contract-request`: a child needs the contract changed. Decide (ask the user **(ask)** if it is a
      product decision or changes behaviour the user approved). If yes: edit `P/contract.md` (and any
      affected slice), add a Changelog line, bump `contract_version` in program.json with jq, then
      `PROG reply "$P" <id> --text "<what changed, version>"` and
      `PROG notify "$P" --to all --text "Contract is now v<n>: <one line>. Run sync-spec and re-check."`
      If no: `PROG reply` with the reason and what to do instead. Never let two repos run on different
      contract versions: `PROG status` flags a child still on an older version; nudge it again.
    - `child-blocked`: show the user which repo is stuck and its `last` line. The child already asked
      its own question in its workspace; only step in when the block is about the contract or another
      repo.
    - `child-pr-opened`: note the PR, nothing else to do yet.
    - `all-children-in-review`: go to Phase 3.

## Phase 3 — Join
11. **Cross-repo check.** Set phase `verify`. Check the X-ACs from cross-repo.md against the children's
    branches (worktrees from `PROG status --json`). If a project skill covers the flow (e.g.
    `webview-paywall-flow` for the Android paywall + webview), run it; otherwise give the user the
    manual steps from cross-repo.md and **(ask)** for the result per X-AC. A failure that belongs to one
    repo → `PROG notify "$P" --to <name> --text "<X-AC that failed, evidence, what to fix>"` and go back
    to Watch; that child fixes it on its open PR ("address PR comments" flow).
12. **Link the PRs.** On every child PR add one comment with `gh pr comment <url> --body` listing all
    the program's PRs, the contract version, the X-AC results and the merge order. Never merge.
13. Ticket update with every PR link and the merge order (jira: `jira-sync` "In Review"; github: issue
    comment; local: skip). Set phase `in-review`, program.json `"phase": "in-review"`.
14. Tell the user: one line per repo with its PR, the merge order, and that each child session handles
    its own PR comments.

When every child is `done` (or the user ends the program): set phase `done`, `.active=false`,
`rm P/.agent-state/current`. Children clean up their own worktrees.
