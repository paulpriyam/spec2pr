#!/usr/bin/env python3
"""spec2pr dashboard: a tiny local server (stdlib only) that shows pipeline state live.

Usage:
  python3 server.py [--port 7788] [--repo PATH ...]

Watches <repo>/.agent-state/ for each --repo, plus every repo listed in ~/.spec2pr/repos.txt
(registered automatically when /ship-feature runs). Binds to 127.0.0.1 only.
Endpoints: /  (dashboard page; /?repo=<path> opens it on one project)
           /api/state  (JSON snapshot of every watched repo, polled by the page every 2s)
           /api/answer (POST {repo, id, answer, note}: answers a question posted by scripts/ask.py)
           /api/evidence?repo=<path>&key=<KEY>&file=<name>  (app-check screenshots/notes of a feature)
           /api/flow?repo=<path>&key=<KEY>  (live flow: elephant, subagents, their tool calls, your answers)
           /api/info   (server root, pid and code version; used by ensure-dashboard.sh to restart a stale server)
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAX_EVENTS = 300


def code_version():
    """Hash of server.py + index.html. ensure-dashboard.sh computes the same hash to spot a stale server."""
    h = hashlib.sha256()
    for name in ("server.py", "index.html"):
        h.update((HERE / name).read_bytes())
    return h.hexdigest()


VERSION = code_version()


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def read_events(path):
    events = []
    try:
        with open(path) as f:
            lines = f.readlines()[-2000:]
    except FileNotFoundError:
        return events
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def parse_t(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def running_agents(events):
    """Pair start/stop events. Match on agent id when present, otherwise on agent type (LIFO)."""
    open_by_id, open_by_type, durations = {}, {}, []
    for e in events:
        agent, aid, ev = e.get("agent", "unknown"), e.get("id"), e.get("ev")
        key = (e.get("ticket"), agent)
        if ev == "start":
            if aid:
                open_by_id[aid] = e
            else:
                open_by_type.setdefault(key, []).append(e)
        elif ev == "stop":
            start = open_by_id.pop(aid, None) if aid else None
            if start is None and open_by_type.get(key):
                start = open_by_type[key].pop()
            if start:
                t0, t1 = parse_t(start.get("t", "")), parse_t(e.get("t", ""))
                if t0 and t1:
                    durations.append({"ticket": e.get("ticket"), "agent": agent,
                                      "seconds": (t1 - t0).total_seconds()})
    running = list(open_by_id.values()) + [s for lst in open_by_type.values() for s in lst]
    return running, durations


REGISTRY = Path.home() / ".spec2pr" / "repos.txt"


def all_repos(fixed):
    """Repos given on the command line, plus repos registered by /ship-feature (re-read every request)."""
    seen, out = set(), []
    extra = []
    try:
        extra = [Path(l.strip()) for l in REGISTRY.read_text().splitlines() if l.strip()]
    except FileNotFoundError:
        pass
    for r in list(fixed) + extra:
        r = r.expanduser()
        if str(r) in seen or not r.is_dir():
            continue
        if r not in fixed and not (r / ".agent-state").is_dir():
            continue
        seen.add(str(r))
        out.append(r)
    return out


def snapshot(fixed):
    now = datetime.now(timezone.utc)
    out = {"generated": now.isoformat(), "repos": []}
    for repo in all_repos(fixed):
        sd = repo / ".agent-state"
        current = None
        try:
            current = (sd / "current").read_text().strip() or None
        except Exception:
            pass
        tickets = []
        if sd.is_dir():
            for p in sorted(sd.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
                if p.name.startswith("tmp"):
                    continue
                data = read_json(p)
                if isinstance(data, dict) and data.get("ticket"):
                    data["_mtime"] = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()
                    wt = data.get("worktree")
                    data["_worktree_exists"] = bool(wt) and Path(wt).is_dir()
                    report = read_json(sd / f"{data['ticket']}-verify" / "report.json")
                    if isinstance(report, dict):
                        data["_verify"] = report
                    tickets.append(data)
        # Active features: marked "active": true in their state, plus the repo's most recent one (current).
        # Several can run at once, each in its own worktree. "active": false or phase "done" ends one.
        active = [t["ticket"] for t in tickets
                  if t.get("phase") != "done" and (t.get("active") is True or
                                                   (t["ticket"] == current and t.get("active") is not False))]
        events = read_events(sd / "events.jsonl")
        mtimes = [p.stat().st_mtime for p in sd.iterdir()] if sd.is_dir() else []
        questions = []
        qd = sd / "questions"
        if qd.is_dir():
            for p in sorted(qd.glob("*.json")):
                q = read_json(p)
                if isinstance(q, dict) and q.get("status") == "open":
                    questions.append(q)
        running, durations = running_agents(events)
        for r in running:
            t0 = parse_t(r.get("t", ""))
            r["elapsed"] = max(0.0, (now - t0).total_seconds()) if t0 else None
        out["repos"].append({
            "path": str(repo),
            "name": repo.name,
            "current": current,
            "active": active,
            "tickets": tickets,
            "events": events[-MAX_EVENTS:],
            "running": running,
            "durations": durations,
            "questions": questions,
            "last_activity": datetime.fromtimestamp(max(mtimes), timezone.utc).isoformat() if mtimes else None,
        })
    # Most recently active project first.
    out["repos"].sort(key=lambda r: r["last_activity"] or "", reverse=True)
    return out


def answer_question(fixed, body):
    """Record an answer from the dashboard. Returns (http_code, message)."""
    repo, qid, answer = body.get("repo"), body.get("id"), body.get("answer")
    if not (isinstance(repo, str) and isinstance(qid, str) and isinstance(answer, str) and answer.strip()):
        return 400, "repo, id and answer are required"
    if not all(c.isalnum() or c == "-" for c in qid):
        return 400, "bad id"
    match = [r for r in all_repos(fixed) if str(r) == repo]
    if not match:
        return 404, "unknown repo"
    path = match[0] / ".agent-state" / "questions" / f"{qid}.json"
    q = read_json(path)
    if not isinstance(q, dict):
        return 404, "unknown question"
    if q.get("status") != "open":
        return 409, f"already answered ({q.get('by')}): {q.get('answer')}"
    labels = [c.get("label") for c in q.get("choices") or []]
    if labels and answer not in labels:
        return 400, "answer must be one of the choices"
    note = body.get("note")
    q.update(status="answered", answer=answer.strip(), note=note.strip() if isinstance(note, str) and note.strip() else None,
             by="dashboard", answered=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    tmp = path.with_name(f".tmp-{path.name}")
    tmp.write_text(json.dumps(q, indent=2))
    os.replace(tmp, path)
    with open(match[0] / ".agent-state" / "events.jsonl", "a") as f:
        f.write(json.dumps({"t": q["answered"], "ticket": q.get("ticket"), "ev": "answer", "agent": "you", "id": None}) + "\n")
    return 200, "ok"


MAX_TOOLS_PER_NODE = 60


def build_flow(fixed, query):
    """Chronological flow for one feature: elephant segments, (parallel) subagents with their tool
    calls, and the user's questions/answers. Built from events.jsonl + tools.jsonl."""
    from urllib.parse import parse_qs
    q = {k: v[0] for k, v in parse_qs(query).items()}
    match = [r for r in all_repos(fixed) if str(r) == q.get("repo")]
    key = q.get("key")
    if not match or not key:
        return None
    sd = match[0] / ".agent-state"
    now = datetime.now(timezone.utc)
    events = [e for e in read_events(sd / "events.jsonl") if e.get("ticket") == key]
    tools_raw = [e for e in read_events(sd / "tools.jsonl")[-6000:] if e.get("ticket") == key] \
        if (sd / "tools.jsonl").exists() else []

    # Tool calls: pair pre/post by tool_use_id.
    calls, order = {}, []
    for e in tools_raw:
        tid = e.get("id") or f"anon-{len(order)}"
        if e.get("ev") == "pre":
            calls[tid] = {"t": e.get("t"), "tool": e.get("tool"), "summary": e.get("summary") or "",
                          "agent_id": e.get("agent_id"), "agent_type": e.get("agent_type"), "status": "running"}
            order.append(tid)
        elif e.get("ev") == "post" and tid in calls:
            c = calls[tid]
            c["status"] = "ok" if e.get("ok", True) else "fail"
            t0, t1 = parse_t(c["t"] or ""), parse_t(e.get("t") or "")
            c["seconds"] = (t1 - t0).total_seconds() if t0 and t1 else None

    # Subagents from start/stop events.
    agents, open_by_type = {}, {}
    for e in events:
        if e.get("ev") not in ("start", "stop"):
            continue
        aid, typ = e.get("id"), e.get("agent", "agent")
        if e["ev"] == "start":
            aid = aid or f"{typ}@{e.get('t')}"
            agents[aid] = {"id": aid, "type": typ, "start": e.get("t"), "end": None, "tools": []}
            open_by_type.setdefault(typ, []).append(aid)
        else:
            if not aid or aid not in agents:
                aid = open_by_type.get(typ, [None]).pop() if open_by_type.get(typ) else None
            if aid and aid in agents:
                agents[aid]["end"] = e.get("t")
    for a in agents.values():
        t0 = parse_t(a["start"] or "")
        t1 = parse_t(a["end"] or "") if a["end"] else now
        a["seconds"] = (t1 - t0).total_seconds() if t0 and t1 else None
        a["status"] = "done" if a["end"] else "running"

    elephant = []
    for tid in order:
        c = calls[tid]
        started = parse_t(c["t"] or "")
        if c["status"] == "running" and started and (now - started).total_seconds() > 900:
            c["status"] = "unknown"  # no end event after 15 min
        a = agents.get(c["agent_id"]) if c["agent_id"] else None
        if c["agent_id"] and a is None:  # tool call from a subagent we have no start event for
            a = agents.setdefault(c["agent_id"], {"id": c["agent_id"], "type": c["agent_type"] or "agent",
                                                  "start": c["t"], "end": None, "tools": [], "seconds": None,
                                                  "status": "running"})
        (a["tools"] if a else elephant).append(c)

    # Questions you were asked (titles from the question files) and your answers.
    qtitles = {}
    qd = sd / "questions"
    if qd.is_dir():
        for f in qd.glob("*.json"):
            x = read_json(f)
            if isinstance(x, dict) and x.get("ticket") in (key, None):
                qtitles[("ask", x.get("asked"))] = x.get("title")
                if x.get("answered"):
                    qtitles[("answer", x.get("answered"))] = f'{x.get("answer")}' + (f' ({x["note"]})' if x.get("note") else "")
    you = [{"kind": "you", "ev": e["ev"], "t": e.get("t"), "text": qtitles.get((e["ev"], e.get("t")), "")}
           for e in events if e.get("ev") in ("ask", "answer")]

    # Group overlapping subagents into parallel steps; put elephant tool calls and your events between.
    steps, cur = [], None
    for a in sorted(agents.values(), key=lambda a: a["start"] or ""):
        end = a["end"] or "9999"
        if cur and (a["start"] or "") < cur["until"]:
            cur["agents"].append(a); cur["until"] = max(cur["until"], end)
        else:
            cur = {"kind": "agents", "t": a["start"], "until": end, "agents": [a]}
            steps.append(cur)
    timeline = sorted(steps + you, key=lambda x: x["t"] or "")
    nodes, ei = [], 0
    elephant.sort(key=lambda c: c["t"] or "")
    def take_elephant(until):
        nonlocal ei
        seg = []
        while ei < len(elephant) and (until is None or (elephant[ei]["t"] or "") < until):
            seg.append(elephant[ei]); ei += 1
        if seg:
            nodes.append({"kind": "elephant", "t": seg[0]["t"], "tools": seg[-MAX_TOOLS_PER_NODE:], "count": len(seg),
                          "running": any(c["status"] == "running" for c in seg)})
    for n in timeline:
        take_elephant(n["t"])
        if n["kind"] == "agents":
            for a in n["agents"]:
                a["count"] = len(a["tools"]); a["tools"] = a["tools"][-MAX_TOOLS_PER_NODE:]
            n.pop("until", None)
        nodes.append(n)
    take_elephant(None)
    return {"key": key, "generated": now.isoformat(), "nodes": nodes,
            "totals": {"tool_calls": len(order), "agents": len(agents)}}


EVIDENCE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
                  ".gif": "image/gif", ".txt": "text/plain; charset=utf-8", ".log": "text/plain; charset=utf-8",
                  ".json": "application/json", ".yaml": "text/plain; charset=utf-8"}


def evidence_file(fixed, query):
    """Path of an app-check evidence file, only inside <repo>/.agent-state/<KEY>-verify/, else None."""
    from urllib.parse import parse_qs
    q = {k: v[0] for k, v in parse_qs(query).items()}
    repo, key, name = q.get("repo"), q.get("key", ""), q.get("file", "")
    match = [r for r in all_repos(fixed) if str(r) == repo]
    if not match or not key or not name or "/" in key or ".." in key:
        return None
    base = (match[0] / ".agent-state" / f"{key}-verify").resolve()
    path = (base / name).resolve()
    if base not in path.parents or not path.is_file() or path.suffix.lower() not in EVIDENCE_TYPES:
        return None
    return path


def make_handler(repos, port):
    origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                # Read on every request so an edited page shows up without a restart.
                self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/flow":
                flow = build_flow(repos, self.path.partition("?")[2])
                if flow is None:
                    self._send(404, b"not found", "text/plain")
                else:
                    self._send(200, json.dumps(flow).encode(), "application/json")
            elif path == "/api/evidence":
                f = evidence_file(repos, self.path.partition("?")[2])
                if f is None:
                    self._send(404, b"not found", "text/plain")
                else:
                    self._send(200, f.read_bytes(), EVIDENCE_TYPES[f.suffix.lower()])
            elif path == "/api/info":
                body = json.dumps({"root": str(HERE.parent), "pid": os.getpid(), "version": VERSION}).encode()
                self._send(200, body, "application/json")
            elif path == "/api/state":
                body = json.dumps(snapshot(repos)).encode()
                self._send(200, body, "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            path = self.path.split("?")[0]
            if path != "/api/answer":
                return self._send(404, b"not found", "text/plain")
            # Only the dashboard page itself may answer: same-origin JSON requests from this server.
            if self.headers.get("Origin") not in origins or \
                    not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._send(403, b"forbidden", "text/plain")
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 65536)) or b"{}")
            except Exception:
                return self._send(400, b"bad json", "text/plain")
            code, msg = answer_question(repos, body if isinstance(body, dict) else {})
            self._send(code, json.dumps({"ok": code == 200, "message": msg}).encode(), "application/json")

        def log_message(self, *args):
            pass

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=int(os.environ.get("FF_DASHBOARD_PORT", 7788)))
    ap.add_argument("--repo", action="append", help="repo to watch (repeatable); default: cwd")
    args = ap.parse_args()
    repos = [Path(r).expanduser().resolve() for r in (args.repo or [])]
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(repos, args.port))
    except OSError as e:
        print(f"spec2pr dashboard: port {args.port} is busy ({e}). "
              f"Is it already running? Try http://127.0.0.1:{args.port} or pass --port.", file=sys.stderr)
        sys.exit(1)
    print(f"spec2pr dashboard: http://127.0.0.1:{args.port}  watching: "
          + (", ".join(str(r) for r in repos) or "(none fixed)") + f" + repos in {REGISTRY}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
