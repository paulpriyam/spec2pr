"""Local trial telemetry for spec2pr programs (multi-repo features). Used by program.py.

Everything stays on this machine, in <program dir>/telemetry/:
  env.json            versions (Claude Code, cmux, git, python, spec2pr) and whether cmux was reachable
  events.jsonl        one line per program.py action: init, add-repo, launch (with cmux errors), wait
                      results, contract requests/replies, notify results, sync-spec, diff, captures
  captures/<n>-<label>/
      status.json     every child's phase, verdicts, PR, contract version
      children.json   per child: did its session start (prompt hook bound), which spec2pr copy and
                      version it runs, open questions, agent/tool activity, failures, diff stat
      screen-<repo>.txt   the last lines of that repo's cmux terminal (secrets blanked)
      window.png      screenshot of the cmux window (whatever workspace is in front)
  REPORT.md           summary written by `program.py report`, plus telemetry-<KEY>.zip next to it

Nothing is uploaded. Turn it off with SPEC2PR_TELEMETRY=off. Screenshots and terminal text can show
code and ticket details: look at them before sharing the zip.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SECRET_RES = [
    re.compile(r"(?i)((?:token|secret|password|passwd|api[_-]?key|authorization)[\"']?\s*[:=]\s*(?:bearer\s+)?)[^\s\"'&]+"),
    re.compile(r"(?i)(--(?:token|password|secret|api-key)[= ])\S+"),
]
SECRET_BARE = re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|xox[abp]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}")


def enabled():
    return os.environ.get("SPEC2PR_TELEMETRY", "on").lower() not in ("off", "0", "false", "no")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tdir(p):
    d = Path(p) / "telemetry"
    d.mkdir(parents=True, exist_ok=True)
    return d


def redact(text):
    for r in SECRET_RES:
        text = r.sub(lambda m: m.group(1) + "***", text)
    return SECRET_BARE.sub("***", text)


def run(cmd, timeout=20, cwd=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return -1, "", str(e)


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return None


def plugin_version(root=PLUGIN_ROOT):
    d = read_json(Path(root) / ".claude-plugin" / "plugin.json") or {}
    return d.get("version")


def log(p, event, **data):
    if not enabled():
        return
    try:
        with open(tdir(p) / "events.jsonl", "a") as f:
            f.write(json.dumps({"t": now(), "event": event, **data}) + "\n")
    except Exception:
        pass


def cmux_ok():
    return shutil.which("cmux") is not None and bool(os.environ.get("CMUX_SOCKET_PATH") or os.environ.get("CMUX_SOCKET"))


def record_env(p):
    if not enabled():
        return
    def ver(cmd):
        code, out, err = run(cmd, timeout=10)
        return (out or err).splitlines()[0] if (out or err) else None
    installed = Path.home() / ".claude" / "plugins"
    env = {
        "t": now(),
        "spec2pr_version": plugin_version(),
        "spec2pr_root": str(PLUGIN_ROOT),
        "spec2pr_is_installed_copy": installed in PLUGIN_ROOT.parents,
        "claude": ver(["claude", "--version"]),
        "cmux": ver(["cmux", "--version"]) if shutil.which("cmux") else None,
        "cmux_reachable": cmux_ok(),
        "git": ver(["git", "--version"]),
        "python": sys.version.split()[0],
        "os": " ".join(os.uname()[i] for i in (0, 2, 4)),
        "parent_workspace": os.environ.get("CMUX_WORKSPACE_ID"),
    }
    (tdir(p) / "env.json").write_text(json.dumps(env, indent=2) + "\n")


def cmux_workspaces():
    if not cmux_ok():
        return {}
    code, out, _ = run(["cmux", "workspace", "list", "--json"], timeout=10)
    try:
        return {w.get("custom_title") or w.get("title"): w for w in json.loads(out).get("workspaces", [])}
    except Exception:
        return {}


def child_details(prog, r, launched_at):
    """What happened in one repo's session, read from its own .agent-state (written by spec2pr's hooks)."""
    key = prog["key"]
    sd = Path(r["path"]) / ".agent-state"
    st = read_json(sd / f"{key}.json") or {}
    # Did the child's prompt hook run? on-prompt.sh writes sessions/<sid> = KEY for /ship-feature.
    bound = []
    for f in (sd / "sessions").glob("*") if (sd / "sessions").is_dir() else []:
        try:
            if f.read_text().strip() == key:
                t = datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                if not launched_at or t >= launched_at:
                    bound.append(t)
        except Exception:
            pass
    def lines(name):
        out = []
        try:
            for l in (sd / name).read_text().splitlines()[-5000:]:
                try:
                    e = json.loads(l)
                except Exception:
                    continue
                if e.get("ticket") == key:
                    out.append(e)
        except Exception:
            pass
        return out
    events, tools = lines("events.jsonl"), lines("tools.jsonl")
    fails = [t for t in tools if t.get("ev") == "post" and t.get("ok") is False]
    agents = {}
    for e in events:
        if e.get("ev") == "start":
            a = (e.get("agent") or "?").split(":")[-1]
            agents[a] = agents.get(a, 0) + 1
    questions = []
    qd = sd / "questions"
    if qd.is_dir():
        for f in qd.glob("*.json"):
            q = read_json(f) or {}
            if q.get("ticket") in (key, None):
                questions.append({"title": q.get("title"), "status": q.get("status"), "asked": q.get("asked"),
                                  "answer": q.get("answer"), "by": q.get("by")})
    wt = st.get("worktree") or r["path"]
    base = r.get("base")
    if not base:
        code, out, _ = run(["git", "-C", r["path"], "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], timeout=10)
        base = out if code == 0 and out else next(
            (b for b in ("main", "master", "develop") if run(["git", "-C", r["path"], "rev-parse", "--verify", "--quiet", b], timeout=10)[0] == 0), "HEAD")
    _, stat, _ = run(["git", "-C", wt, "diff", "--shortstat", f"{base}...HEAD"], timeout=15)
    _, uncommitted, _ = run(["git", "-C", wt, "status", "--porcelain"], timeout=15)
    prg = st.get("program") or {}
    return {
        "repo": r["name"], "phase": st.get("phase"), "title": st.get("title"),
        "session_started": bool(bound), "session_bound_at": sorted(bound),
        "plugin_version": prg.get("plugin_version"), "plugin_root": prg.get("plugin_root"),
        "contract_version": prg.get("contract_version"), "spec_dir": st.get("spec_dir"),
        "verdicts": st.get("verdicts"), "attempts": st.get("attempts"), "last": st.get("last"),
        "blocked_at": st.get("blocked_at"), "pr": st.get("pr"), "branch": st.get("branch"), "worktree": st.get("worktree"),
        "subagent_runs": agents, "tool_calls": sum(1 for t in tools if t.get("ev") == "pre"),
        "failed_tool_calls": len(fails),
        "recent_failures": [{"t": t.get("t"), "agent": t.get("agent_type") or "elephant", "tool": t.get("tool"),
                             "what": redact(t.get("summary") or "")} for t in fails[-5:]],
        "questions": questions, "diff": stat or "", "uncommitted_files": len(uncommitted.splitlines()) if uncommitted else 0,
    }


def capture(p, prog, status, label, shot=True):
    """One snapshot of the whole program. Returns the capture dir (or None when telemetry is off)."""
    if not enabled():
        return None
    td = tdir(p) / "captures"
    td.mkdir(exist_ok=True)
    n = len(list(td.iterdir())) + 1
    safe = re.sub(r"[^a-z0-9-]+", "-", label.lower()).strip("-") or "capture"
    d = td / f"{n:02d}-{safe}"
    d.mkdir()
    (d / "status.json").write_text(json.dumps(status, indent=2) + "\n")
    kids = [child_details(prog, r, prog.get("launched_at")) for r in prog["repos"]]
    (d / "children.json").write_text(json.dumps(kids, indent=2) + "\n")
    got = {"screens": [], "window": None}
    if cmux_ok():
        ws = cmux_workspaces()
        for r in prog["repos"]:
            w = ws.get(r.get("workspace") or f"{prog['key']} · {r['name']}")
            if not w:
                continue
            code, out, _ = run(["cmux", "read-screen", "--workspace", w.get("ref") or w.get("id"), "--lines", "200"], timeout=15)
            if code == 0 and out:
                (d / f"screen-{r['name']}.txt").write_text(redact(out) + "\n")
                got["screens"].append(r["name"])
        if shot:
            code, out, _ = run(["cmux", "shot", "--out", str(d / "window.png"), "--max-width", "1800",
                                "--caption", f"spec2pr {prog['key']} · {label}"], timeout=30)
            if code == 0 and (d / "window.png").exists():
                got["window"] = "window.png"
    log(p, "capture", label=label, dir=d.name, **got,
        children={k["repo"]: {"phase": k["phase"], "session_started": k["session_started"]} for k in kids})
    return d


def _events(p):
    out = []
    try:
        for l in (tdir(p) / "events.jsonl").read_text().splitlines():
            try:
                out.append(json.loads(l))
            except Exception:
                pass
    except FileNotFoundError:
        pass
    return out


def _dur(a, b):
    try:
        t0 = datetime.fromisoformat(a.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(b.replace("Z", "+00:00"))
        s = int((t1 - t0).total_seconds())
        return f"{s // 3600}h{(s % 3600) // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"
    except Exception:
        return "?"


def report(p, prog, status):
    """Write REPORT.md and telemetry-<KEY>.zip. Takes a final capture first."""
    last = capture(p, prog, status, "report")
    td = tdir(p)
    env = read_json(td / "env.json") or {}
    evs = _events(p)
    kids = read_json(last / "children.json") if last else [child_details(prog, r, prog.get("launched_at")) for r in prog["repos"]]
    problems = []
    for e in evs:
        if e["event"] == "launch":
            for c in e.get("children", []):
                if c.get("launched") is False:
                    problems.append(f"Launch of {c['repo']} failed: {c.get('error')}")
        if e["event"] == "notify" and e.get("of") and e.get("sent", 0) < e["of"]:
            problems.append(f"{e['t']} notify to {e.get('to')}: delivered {e.get('sent')}/{e.get('of')} (cmux {'up' if e.get('cmux') else 'not available'})")
        if e["event"] == "wait" and "timeout" in e.get("reasons", []):
            problems.append(f"{e['t']} wait timed out")
    for k in kids:
        if prog.get("launched_at") and not k["session_started"]:
            problems.append(f"{k['repo']}: no sign its session started (prompt hook never bound). Check its cmux workspace.")
        if k.get("plugin_version") and k["plugin_version"] != env.get("spec2pr_version"):
            problems.append(f"{k['repo']}: runs spec2pr {k['plugin_version']}, parent runs {env.get('spec2pr_version')}")
        if k.get("contract_version") not in (None, prog.get("contract_version")):
            problems.append(f"{k['repo']}: on contract v{k['contract_version']}, program is v{prog.get('contract_version')}")
        if k.get("phase") == "blocked":
            problems.append(f"{k['repo']}: blocked at {k.get('blocked_at')}: {k.get('last')}")
        if k.get("failed_tool_calls", 0) >= 10:
            problems.append(f"{k['repo']}: {k['failed_tool_calls']} failed tool calls")
    reqs = [read_json(f) or {} for f in sorted((p / "requests").glob("*.json"))] if (p / "requests").is_dir() else []
    caps = sorted(x.name for x in (td / "captures").iterdir()) if (td / "captures").is_dir() else []
    first = evs[0]["t"] if evs else prog.get("created")
    L = [f"# spec2pr program trial: {prog['key']}", "",
         f"**{prog.get('title') or prog['key']}** · program phase `{prog.get('phase')}` · contract v{prog.get('contract_version')} · "
         f"{len(prog['repos'])} repos · started {first} · report {now()} ({_dur(first, now()) if first else '?'})", "",
         "## Environment", ""]
    L += [f"- {k}: `{v}`" for k, v in env.items() if k != "t"]
    L += ["", "## Repos", "", "| repo | phase | session started | spec2pr | contract | verdicts | PR | diff |", "|---|---|---|---|---|---|---|---|"]
    for k in kids:
        v = " ".join(f"{a}:{b}" for a, b in (k.get("verdicts") or {}).items() if b) or "–"
        L.append(f"| {k['repo']} | {k.get('phase') or '–'} | {'yes ' + k['session_bound_at'][0] if k['session_started'] else 'NO'} | "
                 f"{k.get('plugin_version') or '?'} | v{k.get('contract_version') or '?'} | {v} | {k.get('pr') or '–'} | {k.get('diff') or '–'} |")
    L += ["", "## Problems spotted", ""] + ([f"- {x}" for x in problems] or ["- none"])
    L += ["", "## Contract change requests", ""]
    L += [f"- {q.get('id')} from {q.get('repo')}: {q.get('text')} → {q.get('status')}"
          f"{' after ' + _dur(q['asked'], q['answered']) if q.get('answered') else ''}{': ' + q['answer'] if q.get('answer') else ''}" for q in reqs] or ["- none"]
    for k in kids:
        L += ["", f"## {k['repo']} activity", "",
              f"- subagent runs: {', '.join(f'{a}×{n}' for a, n in k['subagent_runs'].items()) or 'none'}",
              f"- tool calls: {k['tool_calls']} ({k['failed_tool_calls']} failed), attempts: {k.get('attempts')}",
              f"- questions asked: {len(k['questions'])}" + "".join(f"\n  - {q['title']} → {q.get('answer') or q.get('status')}" for q in k["questions"])]
        if k["recent_failures"]:
            L += ["- recent failures:"] + [f"  - {f['t']} {f['agent']} {f['tool']}: `{f['what']}`" for f in k["recent_failures"]]
    L += ["", "## Timeline", ""]
    for e in evs:
        extra = {k: v for k, v in e.items() if k not in ("t", "event")}
        L.append(f"- `{e['t']}` **{e['event']}** {json.dumps(extra, ensure_ascii=False)[:300]}")
    L += ["", "## Captures", "", "Each has status.json, children.json, terminal text per repo and window.png (if cmux was up).", ""]
    L += [f"- captures/{c}" for c in caps] or ["- none"]
    (td / "REPORT.md").write_text("\n".join(L) + "\n")
    z = td / f"telemetry-{prog['key']}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in td.rglob("*"):
            if f.is_file() and f != z:
                zf.write(f, f.relative_to(td))
        for f in ("program.json", "contract.md", "cross-repo.md"):
            if (p / f).is_file():
                zf.write(p / f, f"program/{f}")
    log(p, "report", problems=len(problems))
    return td / "REPORT.md", z, problems
