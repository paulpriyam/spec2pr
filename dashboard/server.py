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
           /api/learning?repo=<path>  (Learning tab: feature records, repeated problems, lessons)
           /api/lesson (POST {repo, id, status}: turn a lesson on/off, keep or discard a proposed one)
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
sys.path.insert(0, str(HERE.parent / "scripts"))
import learn  # noqa: E402  (scripts/learn.py: feature records and lessons)


def code_version():
    """Hash of server.py + index.html + scripts/telegram.py + scripts/learn.py. ensure-dashboard.sh computes the same hash to spot a stale server."""
    h = hashlib.sha256()
    for f in (HERE / "server.py", HERE / "index.html", HERE.parent / "scripts" / "telegram.py",
              HERE.parent / "scripts" / "learn.py"):
        h.update(f.read_bytes())
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
                    try:
                        fl = build_flow_for(repo, data["ticket"])
                        data["_cost"] = fl["totals"]["cost_usd"]
                        data["_tokens"] = fl["totals"]["tokens"]
                        data["_loops"] = [l for l in fl["loops"] if l["running"]]
                    except Exception:
                        pass
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


def set_lesson(fixed, body):
    """Turn a lesson on/off (or keep/discard a proposed one) from the Learning tab."""
    repo, lid, status = body.get("repo"), body.get("id"), body.get("status")
    if not (isinstance(repo, str) and isinstance(lid, str) and isinstance(status, str)):
        return 400, "repo, id and status are required"
    if not all(c.isalnum() or c == "-" for c in lid):
        return 400, "bad id"
    match = [r for r in all_repos(fixed) if str(r) == repo]
    if not match:
        return 404, "unknown repo"
    ok, msg = learn.set_status(lid, status, match[0])
    return (200, "ok") if ok else (400, msg)


MAX_TOOLS_PER_NODE = 60

# ---- Cost estimates -------------------------------------------------------------------------------
# API list prices in USD per million tokens: input, output, cache write (5 min), cache write (1 h),
# cache read. Override or add models in ~/.spec2pr/prices.json as {"model-id": [in, out, w5m, w1h, read]}.
# These are estimates of what the run would cost on the Claude API; on a Claude subscription you are
# not billed per token.
PRICES = {
    "claude-fable-5-2": (8, 40, 10, 16, 0.20), "claude-mythos-5-2": (8, 40, 10, 16, 0.20),
    "claude-fable-5-1": (10, 50, 12.5, 20, 0.25), "claude-mythos-5-1": (10, 50, 12.5, 20, 0.25),
    "claude-fable-5": (10, 50, 12.5, 20, 1.0), "claude-mythos-5": (10, 50, 12.5, 20, 1.0),
    "claude-opus-5-5": (4, 20, 5, 8, 0.20),
    "claude-opus-5": (5, 25, 6.25, 10, 0.50), "claude-opus-4-8": (5, 25, 6.25, 10, 0.50),
    "claude-opus-4-7": (5, 25, 6.25, 10, 0.50), "claude-opus-4-6": (5, 25, 6.25, 10, 0.50),
    "claude-sonnet-5-5": (2, 10, 2.5, 4, 0.10), "claude-sonnet-5": (2, 10, 2.5, 4, 0.20),
    "claude-sonnet-4-6": (3, 15, 3.75, 6, 0.30), "claude-sonnet-4-5": (3, 15, 3.75, 6, 0.30),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.20, 0.01), "claude-haiku-4-5": (1, 5, 1.25, 2, 0.10),
}


def prices():
    p = dict(PRICES)
    extra = read_json(Path.home() / ".spec2pr" / "prices.json")
    if isinstance(extra, dict):
        p.update({k: tuple(v) for k, v in extra.items() if isinstance(v, list) and len(v) == 5})
    return p


def price_for(model, table):
    m = (model or "").split("[")[0]
    best = max((k for k in table if m.startswith(k)), key=len, default=None)
    return table.get(best) if best else None


_tx_cache = {}


def transcript_entries(path):
    """Assistant messages of a Claude Code transcript: [(timestamp, model, usage, sidechain)], one per message id."""
    try:
        st = path.stat()
    except OSError:
        return []
    hit = _tx_cache.get(str(path))
    if hit and hit[0] == (st.st_mtime, st.st_size):
        return hit[1]
    by_id = {}
    try:
        with open(path) as f:
            for line in f:
                if '"usage"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                m = e.get("message") or {}
                if e.get("type") != "assistant" or not isinstance(m.get("usage"), dict):
                    continue
                by_id[m.get("id") or e.get("uuid")] = (e.get("timestamp") or "", m.get("model"), m["usage"], bool(e.get("isSidechain")))
    except OSError:
        return []
    entries = sorted(by_id.values(), key=lambda x: x[0])
    _tx_cache[str(path)] = ((st.st_mtime, st.st_size), entries)
    return entries


def cost_of(entries, table):
    usd, unpriced = 0.0, False
    tok = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
    for _, model, u, _ in entries:
        cw = u.get("cache_creation") or {}
        w5 = cw.get("ephemeral_5m_input_tokens")
        w1 = cw.get("ephemeral_1h_input_tokens") or 0
        if w5 is None:
            w5 = (u.get("cache_creation_input_tokens") or 0) - w1
        inp, out, rd = u.get("input_tokens") or 0, u.get("output_tokens") or 0, u.get("cache_read_input_tokens") or 0
        tok["input"] += inp; tok["output"] += out; tok["cache_write"] += w5 + w1; tok["cache_read"] += rd
        pr = price_for(model, table)
        if not pr:
            unpriced = unpriced or bool(inp or out)
            continue
        mult = 2.0 if u.get("speed") == "fast" else 1.0
        if (model or "").startswith("claude-haiku-5-5") and inp + rd + w5 + w1 > 100_000:
            mult *= 5.0  # long prompts are billed at the higher tier
        usd += mult * (inp * pr[0] + out * pr[1] + w5 * pr[2] + w1 * pr[3] + rd * pr[4]) / 1e6
    return {"usd": round(usd, 4), "tokens": tok, "unpriced": unpriced}


# ---- Loop detection ---------------------------------------------------------------------------------
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
EDITING_AGENTS = {"implementer", "tester"}
REPEAT_FAILS, STALL_CALLS = 3, 25


def detect_loop(calls, agent_type):
    """Flag an agent that keeps hitting the same failure, or a builder busy without changing any file."""
    fails = {}
    for c in calls:
        if c["status"] == "fail":
            k = (c["tool"], c["summary"])
            fails[k] = fails.get(k, 0) + 1
    if fails:
        (tool, summ), n = max(fails.items(), key=lambda kv: kv[1])
        if n >= REPEAT_FAILS:
            return {"kind": "repeat", "count": n, "text": f"{tool} `{summ[:70]}` failed {n} times"}
    short = (agent_type or "").split(":")[-1]
    if short in EDITING_AGENTS:
        since = 0
        for c in reversed(calls):
            if c["tool"] in EDIT_TOOLS:
                break
            since += 1
        if since >= STALL_CALLS:
            return {"kind": "stall", "count": since, "text": f"{since} tool calls without changing a file"}
    return None


def build_flow(fixed, query):
    from urllib.parse import parse_qs
    q = {k: v[0] for k, v in parse_qs(query).items()}
    match = [r for r in all_repos(fixed) if str(r) == q.get("repo")]
    if not match or not q.get("key"):
        return None
    return build_flow_for(match[0], q["key"])


_flow_cache = {}


def build_flow_for(repo, key):
    """Memoized on the state files; transcripts have their own cache."""
    sd = repo / ".agent-state"
    sig = tuple((p.stat().st_mtime, p.stat().st_size) if p.exists() else None
                for p in (sd / "events.jsonl", sd / "tools.jsonl", sd / "questions"))
    hit = _flow_cache.get((str(repo), key))
    if hit and hit[0] == sig and (datetime.now(timezone.utc) - hit[2]).total_seconds() < 2:
        return hit[1]
    flow = _build_flow(repo, key)
    _flow_cache[(str(repo), key)] = (sig, flow, datetime.now(timezone.utc))
    return flow


def _build_flow(repo, key):
    """Chronological flow for one feature: elephant segments, (parallel) subagents with their tool
    calls, and the user's questions/answers, with estimated costs and loop warnings.
    Built from events.jsonl + tools.jsonl (+ Claude Code transcripts for token usage)."""
    sd = repo / ".agent-state"
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
            agents[aid] = {"id": aid, "type": typ, "start": e.get("t"), "end": None, "tools": [],
                           "_tx": e.get("transcript"), "_session": e.get("session"), "_real_id": e.get("id")}
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

    # Estimated cost per agent (its own transcript) and for the elephant (main session, during this feature).
    table = prices()
    total_usd, unpriced = 0.0, False
    total_tok = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
    add_tok = lambda c: [total_tok.__setitem__(k, total_tok[k] + c["tokens"][k]) for k in total_tok]
    for a in agents.values():
        a["loop"] = detect_loop(a["tools"], a["type"]) if a["status"] == "running" or a["tools"] else None
        a["cost"] = None
        if a.get("_tx") and a.get("_session") and a.get("_real_id"):
            f = Path(a["_tx"]).parent / a["_session"] / "subagents" / f"agent-{a['_real_id']}.jsonl"
            c = cost_of(transcript_entries(f), table)
            if c["tokens"]["output"] or c["usd"]:
                a["cost"] = c; total_usd += c["usd"]; unpriced = unpriced or c["unpriced"]; add_tok(c)
        for k in ("_tx", "_session", "_real_id"):
            a.pop(k, None)
    times = [r.get("t") for r in events + tools_raw if r.get("t")]
    elephant_cost = None
    main_tx = {r.get("transcript") for r in events + tools_raw if r.get("transcript") and not r.get("agent_id")}
    if times and main_tx:
        t0 = min(times)
        t1 = max(times) if not any(a["status"] == "running" for a in agents.values()) else "9999"
        lo, hi = t0[:19], t1[:19]   # compare to the second: hook times have none, transcripts have ms
        entries = [e for f in main_tx for e in transcript_entries(Path(f)) if not e[3] and lo <= e[0][:19] <= hi]
        if entries:
            elephant_cost = cost_of(entries, table)
            total_usd += elephant_cost["usd"]; unpriced = unpriced or elephant_cost["unpriced"]; add_tok(elephant_cost)
    elephant_loop = detect_loop(elephant, "elephant")

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
    loops = [{"agent": a["type"].split(":")[-1], "agent_id": a["id"], "running": a["status"] == "running", **a["loop"]}
             for a in agents.values() if a.get("loop")]
    if elephant_loop:
        last = parse_t(elephant[-1]["t"] or "") if elephant else None
        recent = bool(last) and (now - last).total_seconds() < 900
        loops.append({"agent": "elephant", "agent_id": None, "running": recent, **elephant_loop})
    return {"key": key, "generated": now.isoformat(), "nodes": nodes, "loops": loops,
            "elephant_cost": elephant_cost,
            "totals": {"tool_calls": len(order), "agents": len(agents),
                       "cost_usd": round(total_usd, 2) if (total_usd or elephant_cost) else None, "cost_partial": unpriced,
                       "tokens": total_tok if any(total_tok.values()) else None}}


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
            elif path == "/api/learning":
                from urllib.parse import parse_qs
                repo = (parse_qs(self.path.partition("?")[2]).get("repo") or [""])[0]
                match = [r for r in all_repos(repos) if str(r) == repo]
                if not match:
                    self._send(404, b"not found", "text/plain")
                else:
                    self._send(200, json.dumps(learn.learning_view(match[0])).encode(), "application/json")
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
            if path not in ("/api/answer", "/api/lesson"):
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
            body = body if isinstance(body, dict) else {}
            code, msg = answer_question(repos, body) if path == "/api/answer" else set_lesson(repos, body)
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
    # Answers from Telegram (if connected): one background thread fetches replies for every project.
    sys.path.insert(0, str(HERE.parent / "scripts"))
    try:
        import telegram
        import threading
        threading.Thread(target=telegram.poll_forever, daemon=True).start()
    except Exception as e:
        print(f"spec2pr dashboard: Telegram replies disabled ({e})", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
