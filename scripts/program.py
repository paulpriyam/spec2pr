#!/usr/bin/env python3
"""spec2pr programs: one feature that needs changes in several repos.

A program has ONE spec (a shared contract plus one slice per repo) and one approval, then each repo is
built by its own normal /ship-feature session ("child"), each in its own cmux workspace when cmux is
available. Files are the source of truth; cmux messages are only wake-up nudges.

  program.py init KEY [--title T] [--source jira|github|local]      -> prints the program dir
  program.py add-repo PROGRAM --name N --path DIR [--base BRANCH]
  program.py launch PROGRAM [--print] [--only NAME]                 -> one workspace (or command) per repo
  program.py status PROGRAM [--json]                                -> each child's phase, verdicts, PR
  program.py wait PROGRAM [--timeout SECONDS]                       -> blocks until something new needs the parent
  program.py sync-spec PROGRAM --repo NAME --dest DIR               -> copies the slice + contract into DIR
  program.py request PROGRAM --repo NAME --text T                   -> child asks for a contract change
  program.py reply PROGRAM ID --text T                              -> parent answers a request
  program.py wait-reply PROGRAM ID [--timeout SECONDS]              -> child blocks until answered
  program.py notify PROGRAM --to NAME|parent|all --text T           -> cmux agent message (no-op without cmux)
  program.py diff PROGRAM                                           -> each child's branch diff (cmux viewer or stat)

PROGRAM is the program dir (~/.spec2pr/programs/<KEY>) or just its KEY. Layout:
  program.json   contract.md   cross-repo.md   <name>/{spec.md,plan.md,tasks.md}
  requests/<id>.json   .agent-state/ (the parent's own state, questions and events: shown on the dashboard)
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

HOME = Path(os.environ.get("SPEC2PR_HOME", Path.home() / ".spec2pr"))
PROGRAMS = HOME / "programs"
DONE_PHASES = {"in-review", "done"}
SLICE_FILES = ("spec.md", "plan.md", "tasks.md")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def die(msg):
    print(f"program.py: {msg}", file=sys.stderr)
    sys.exit(1)


def write_atomic(path, data):
    path = Path(path)
    tmp = path.with_name(f".tmp-{path.name}")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return None


def pdir(arg):
    p = Path(arg).expanduser()
    if not p.is_absolute() and not p.exists():
        p = PROGRAMS / arg
    p = p.resolve()
    if not (p / "program.json").is_file():
        die(f"no program at {p} (run: program.py init KEY)")
    return p


def load(p):
    return json.loads((p / "program.json").read_text())


def save(p, prog):
    prog["updated"] = now()
    write_atomic(p / "program.json", prog)


def repo_entry(prog, name):
    for r in prog["repos"]:
        if r["name"] == name:
            return r
    die(f"no repo named {name!r} in this program (have: {', '.join(r['name'] for r in prog['repos'])})")


def has_cmux():
    return shutil.which("cmux") is not None and bool(os.environ.get("CMUX_SOCKET_PATH") or os.environ.get("CMUX_SOCKET"))


def workspace_title(prog, r):
    return f"{prog['key']} · {r['name']}"


def cmux_message(target, text, sender):
    if not target or not has_cmux():
        return False
    try:
        res = subprocess.run(["cmux", "agent", "message", target, "--from", sender, "--", text],
                             capture_output=True, text=True, timeout=15)
        return res.returncode == 0
    except Exception:
        return False


def child_state(r, key):
    st = read_json(Path(r["path"]) / ".agent-state" / f"{key}.json")
    return st if isinstance(st, dict) else None


# ---------------------------------------------------------------- commands

def cmd_init(a):
    key = a.key.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", key):
        die("KEY may only contain letters, digits, '.', '_' and '-'")
    p = PROGRAMS / key
    (p / ".agent-state").mkdir(parents=True, exist_ok=True)
    (p / "requests").mkdir(exist_ok=True)
    if (p / "program.json").exists():
        prog = load(p)
        if a.title:
            prog["title"] = a.title
        if os.environ.get("CMUX_WORKSPACE_ID"):
            prog["parent_workspace"] = os.environ["CMUX_WORKSPACE_ID"]
        save(p, prog)
    else:
        save(p, {"key": key, "title": a.title or key, "source": a.source, "phase": "context",
                 "contract_version": 1, "parent_workspace": os.environ.get("CMUX_WORKSPACE_ID"),
                 "repos": [], "created": now()})
    print(p)


def cmd_add_repo(a):
    p = pdir(a.program)
    prog = load(p)
    path = Path(a.path).expanduser().resolve()
    if not (path / ".git").exists():
        die(f"{path} is not the main checkout of a git repo")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", a.name):
        die("--name must be lower-case letters, digits and '-' (e.g. android, webview)")
    entry = {"name": a.name, "path": str(path), "base": a.base, "launched": False}
    prog["repos"] = [r for r in prog["repos"] if r["name"] != a.name] + [entry]
    (p / a.name).mkdir(exist_ok=True)
    save(p, prog)
    print(f"{a.name}: {path}")


PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def child_command(prog, p, r):
    claude = os.environ.get("SPEC2PR_CLAUDE", "claude")
    # A copy of spec2pr that is not the installed one (e.g. a checkout being tried out): load the same
    # copy in the children, or they would run the installed version, which may not know --program.
    installed = Path.home() / ".claude" / "plugins"
    if installed not in PLUGIN_ROOT.parents:
        claude += f" --plugin-dir {shlex.quote(str(PLUGIN_ROOT))}"
    m = re.fullmatch(r"gh-(\d+)", prog["key"])
    ticket = f"#{m.group(1)}" if m else prog["key"]
    prompt = f"/spec2pr:ship-feature {ticket} --program {p}"
    if prog.get("source") == "local":
        prompt += " --no-jira"
    if r.get("base"):
        prompt += f" --base {r['base']}"
    return f"{claude} {shlex.quote(prompt)}"


def cmd_launch(a):
    p = pdir(a.program)
    prog = load(p)
    if not prog["repos"]:
        die("no repos in this program yet (program.py add-repo)")
    missing = [f"{r['name']}/{f}" for r in prog["repos"] for f in SLICE_FILES if not (p / r["name"] / f).is_file()]
    if not (p / "contract.md").is_file():
        missing.insert(0, "contract.md")
    if missing:
        die("spec is incomplete, missing: " + ", ".join(missing))
    use_cmux = has_cmux() and not a.print
    out = []
    for r in prog["repos"]:
        if a.only and r["name"] != a.only:
            continue
        cmd = child_command(prog, p, r)
        title = workspace_title(prog, r)
        if use_cmux:
            res = subprocess.run(["cmux", "new-workspace", "--name", title, "--cwd", r["path"],
                                  "--description", f"spec2pr program {prog['key']}: {r['name']} slice",
                                  "--command", cmd], capture_output=True, text=True, timeout=30)
            ok = res.returncode == 0
            r["launched"] = ok or r.get("launched", False)
            r["workspace"] = title
            out.append({"repo": r["name"], "workspace": title, "launched": ok,
                        "error": None if ok else (res.stderr or res.stdout).strip()})
        else:
            out.append({"repo": r["name"], "run": f"cd {shlex.quote(r['path'])} && {cmd}"})
    if prog.get("phase") in (None, "context", "spec", "goldfish", "approval"):
        prog["phase"] = "building"
    save(p, prog)
    print(json.dumps({"cmux": use_cmux, "children": out}, indent=2))


def status_of(p, prog):
    rows = []
    for r in prog["repos"]:
        st = child_state(r, prog["key"]) or {}
        rows.append({
            "repo": r["name"], "path": r["path"], "launched": r.get("launched", False),
            "phase": st.get("phase") or ("not started" if r.get("launched") else "not launched"),
            "blocked_at": st.get("blocked_at"), "last": st.get("last"), "verdicts": st.get("verdicts"),
            "branch": st.get("branch"), "worktree": st.get("worktree"), "pr": st.get("pr"),
            "contract_version": (st.get("program") or {}).get("contract_version"),
        })
    reqs = []
    rd = p / "requests"
    if rd.is_dir():
        for f in sorted(rd.glob("*.json")):
            q = read_json(f)
            if isinstance(q, dict) and q.get("status") == "open":
                reqs.append(q)
    return {"program": prog["key"], "title": prog.get("title"), "phase": prog.get("phase"),
            "contract_version": prog.get("contract_version"), "children": rows, "open_requests": reqs}


def cmd_status(a):
    p = pdir(a.program)
    s = status_of(p, load(p))
    if a.json:
        print(json.dumps(s, indent=2))
        return
    print(f"{s['program']} · {s['title']} · program phase: {s['phase']} · contract v{s['contract_version']}")
    for c in s["children"]:
        extra = f" (blocked at {c['blocked_at']})" if c["phase"] == "blocked" else ""
        stale = "" if c["contract_version"] in (None, s["contract_version"]) else f" · on contract v{c['contract_version']}!"
        print(f"  {c['repo']:<12} {c['phase']}{extra}{stale}  {c['pr'] or ''}  {c['last'] or ''}".rstrip())
    for q in s["open_requests"]:
        print(f"  ⚑ contract change request {q['id']} from {q['repo']}: {q['text']}")


def cmd_wait(a):
    """Return as soon as the parent has something to do: a contract request, a blocked child, a child that
    opened its PR, or every child at in-review/done. Run it in the background and act on what it prints."""
    p = pdir(a.program)
    # What earlier waits already reported, so nothing is missed between two waits and nothing is
    # reported twice (an open request waiting on the user would otherwise wake the parent forever).
    seen_file = p / ".agent-state" / "wait-seen.json"
    mem = read_json(seen_file) or {}
    seen, seen_reqs = mem.get("phases") or {}, set(mem.get("requests") or [])
    deadline = time.time() + a.timeout if a.timeout else None
    while True:
        prog = load(p)
        s = status_of(p, prog)
        phases = {c["repo"]: c["phase"] for c in s["children"]}
        reasons = []
        new_reqs = [q["id"] for q in s["open_requests"] if q["id"] not in seen_reqs]
        if new_reqs:
            reasons.append("contract-request")
        if any(ph == "blocked" and seen.get(n) != "blocked" for n, ph in phases.items()):
            reasons.append("child-blocked")
        newly_review = [n for n, ph in phases.items() if ph in DONE_PHASES and seen.get(n) not in DONE_PHASES]
        if newly_review:
            reasons.append("child-pr-opened")
        if s["children"] and all(ph in DONE_PHASES for ph in phases.values()):
            reasons.append("all-children-in-review")
        if reasons:
            write_atomic(seen_file, {"phases": phases, "requests": sorted(seen_reqs | set(new_reqs))})
            print(json.dumps({"reasons": reasons, "new_requests": new_reqs, **s}, indent=2))
            return
        if deadline and time.time() > deadline:
            print(json.dumps({"reasons": ["timeout"], **s}, indent=2))
            return
        time.sleep(10)


def cmd_sync_spec(a):
    p = pdir(a.program)
    prog = load(p)
    r = repo_entry(prog, a.repo)
    dest = Path(a.dest).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for f in SLICE_FILES:
        src = p / r["name"] / f
        if src.is_file():
            shutil.copy2(src, dest / f)
            copied.append(f)
    for f in ("contract.md", "cross-repo.md"):
        if (p / f).is_file():
            shutil.copy2(p / f, dest / f)
            copied.append(f)
    print(json.dumps({"dest": str(dest), "copied": copied, "contract_version": prog.get("contract_version", 1)}))


def cmd_request(a):
    p = pdir(a.program)
    prog = load(p)
    repo_entry(prog, a.repo)
    rid = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
    write_atomic(p / "requests" / f"{rid}.json", {"id": rid, "repo": a.repo, "text": a.text, "status": "open",
                                                  "asked": now(), "contract_version": prog.get("contract_version")})
    cmux_message(prog.get("parent_workspace"),
                 f"spec2pr program {prog['key']}: {a.repo} asks for a contract change (request {rid}): {a.text}",
                 f"{prog['key']} · {a.repo}")
    print(rid)


def cmd_reply(a):
    p = pdir(a.program)
    prog = load(p)
    f = p / "requests" / f"{a.id}.json"
    q = read_json(f)
    if not isinstance(q, dict):
        die(f"no request {a.id}")
    q.update({"status": "answered", "answer": a.text, "answered": now(),
              "contract_version": prog.get("contract_version")})
    write_atomic(f, q)
    r = repo_entry(prog, q["repo"])
    cmux_message(r.get("workspace") or workspace_title(prog, r),
                 f"spec2pr program {prog['key']}: your contract request {a.id} is answered: {a.text}",
                 f"{prog['key']} · program")
    print(json.dumps(q))


def cmd_wait_reply(a):
    p = pdir(a.program)
    f = p / "requests" / f"{a.id}.json"
    deadline = time.time() + a.timeout if a.timeout else None
    while True:
        q = read_json(f)
        if not isinstance(q, dict):
            die(f"no request {a.id}")
        if q.get("status") != "open":
            print(json.dumps(q))
            return
        if deadline and time.time() > deadline:
            print(json.dumps({**q, "status": "timeout"}))
            return
        time.sleep(5)


def cmd_notify(a):
    p = pdir(a.program)
    prog = load(p)
    if a.to == "parent":
        targets = [prog.get("parent_workspace")]
        sender = f"{prog['key']} · {a.sender or 'child'}"
    else:
        rs = prog["repos"] if a.to == "all" else [repo_entry(prog, a.to)]
        targets = [r.get("workspace") or workspace_title(prog, r) for r in rs]
        sender = f"{prog['key']} · program"
    sent = [t for t in targets if cmux_message(t, a.text, sender)]
    print(json.dumps({"sent": len(sent), "of": len(targets), "cmux": has_cmux()}))


def default_base(path):
    """Same order as worktree.sh: origin's default branch, then main/master/develop."""
    def git(*args):
        return subprocess.run(["git", "-C", path, *args], capture_output=True, text=True)
    res = git("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()
    for b in ("main", "master", "develop"):
        if git("rev-parse", "--verify", "--quiet", b).returncode == 0:
            return b
    return "HEAD"


def cmd_diff(a):
    p = pdir(a.program)
    prog = load(p)
    for r in prog["repos"]:
        st = child_state(r, prog["key"]) or {}
        wt = st.get("worktree") or r["path"]
        base = r.get("base") or default_base(r["path"])
        if has_cmux():
            subprocess.run(["cmux", "diff", "--branch", "--cwd", wt, "--base", base, "--no-focus",
                            "--title", workspace_title(prog, r)], capture_output=True, timeout=30)
            print(f"{r['name']}: opened diff of {wt}")
        else:
            res = subprocess.run(["git", "-C", wt, "diff", "--stat", f"{base}...HEAD"], capture_output=True, text=True)
            print(f"== {r['name']} ({wt}) ==\n{res.stdout or res.stderr}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init"); s.add_argument("key"); s.add_argument("--title"); s.add_argument("--source", default="local", choices=["jira", "github", "local"])
    s = sub.add_parser("add-repo"); s.add_argument("program"); s.add_argument("--name", required=True); s.add_argument("--path", required=True); s.add_argument("--base")
    s = sub.add_parser("launch"); s.add_argument("program"); s.add_argument("--print", action="store_true"); s.add_argument("--only")
    s = sub.add_parser("status"); s.add_argument("program"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("wait"); s.add_argument("program"); s.add_argument("--timeout", type=int, default=0)
    s = sub.add_parser("sync-spec"); s.add_argument("program"); s.add_argument("--repo", required=True); s.add_argument("--dest", required=True)
    s = sub.add_parser("request"); s.add_argument("program"); s.add_argument("--repo", required=True); s.add_argument("--text", required=True)
    s = sub.add_parser("reply"); s.add_argument("program"); s.add_argument("id"); s.add_argument("--text", required=True)
    s = sub.add_parser("wait-reply"); s.add_argument("program"); s.add_argument("id"); s.add_argument("--timeout", type=int, default=0)
    s = sub.add_parser("notify"); s.add_argument("program"); s.add_argument("--to", required=True); s.add_argument("--text", required=True); s.add_argument("--from", dest="sender")
    s = sub.add_parser("diff"); s.add_argument("program")
    a = ap.parse_args()
    globals()["cmd_" + a.cmd.replace("-", "_")](a)


if __name__ == "__main__":
    main()
