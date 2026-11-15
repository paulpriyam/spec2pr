#!/usr/bin/env python3
"""spec2pr learns from finished features: records what went wrong, turns repeated problems into short
lessons, and hands the right lessons to the agents at the start of the next feature.

Phase 1, records:
  learn.py sendback --repo R --ticket K --stage S --tag T --why "…" [--ac AC-3]
      log one piece of work sent back for fixes (called by the orchestrator each time)
  learn.py retro --repo R --ticket K --outcome pr|done|blocked|stopped
      write the feature's record (rounds, send-backs, loops, cost, your corrections), score the
      lessons it used, and print lesson candidates as JSON for the orchestrator to write up

Phase 2, lessons (Markdown files you can edit by hand):
  learn.py pick --repo R --ticket K --text FILE [FILE…] [--platforms web,android]
      choose the lessons that fit this feature, write STATE/K-lessons.md (one section per agent)
  learn.py add --scope general|repo --repo R --title … --when … --do … --for a,b [--match kw,kw]
               [--platforms any|web,…] [--signature stage/tag] [--evidence …] [--status on|proposed]
  learn.py set ID on|off|archived|proposed [--repo R]      learn.py edit ID [--when …] [--do …] …
  learn.py list [--repo R]

Where things live:
  ~/.spec2pr/history.jsonl            one record per feature, every project (Phase 1)
  ~/.spec2pr/lessons.md               general lessons, true in any repo (ids G-…)
  <repo>/.spec2pr/lessons.md          lessons about this codebase, can be committed (ids R-…)
  <repo>/.agent-state/K-sendbacks.jsonl, K-retro.json, K-lessons.md
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path.home() / ".spec2pr"
HISTORY = HOME / "history.jsonl"
GENERAL = HOME / "lessons.md"

STAGES = ["goldfish", "guardian", "tester", "verify", "reviewer", "pr-comments"]
STAGE_NAMES = {"goldfish": "goldfish test", "guardian": "spec check", "tester": "tests", "verify": "app check",
               "reviewer": "code review", "pr-comments": "PR comments"}
TAGS = {
    "missed-ui-state": "error, empty, loading or declined states not handled",
    "missed-ac": "an acceptance criterion was not built",
    "wrong-behaviour": "built, but does the wrong thing",
    "unclear-spec": "the spec was vague or missing a decision",
    "weak-tests": "tests missing, shallow or not testing the AC",
    "broken-build": "compile, type, lint or startup errors",
    "regression": "broke something that worked before",
    "repo-convention": "ignored how this codebase does things",
    "env-setup": "app or tools could not run",
    "security": "a security or privacy problem",
    "other": "anything else",
}
AGENTS = ["elephant", "implementer", "spec-guardian", "tester", "app-verifier", "pr-reviewer"]
PER_AGENT = 8
FLAG_MIN_USED, FLAG_RATIO = 3, 0.5
STALE_DAYS = 120


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today():
    return datetime.now().strftime("%Y-%m-%d")


def root_of(repo):
    """The main checkout (feature worktrees keep their state there, see lib.sh)."""
    import subprocess
    repo = Path(repo).resolve()
    try:
        common = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-common-dir"], capture_output=True,
                                text=True, timeout=5).stdout.strip()
    except Exception:
        common = ""
    if common:
        c = (repo / common).resolve()
        if c.name == ".git":
            return c.parent
    return repo


def read_json(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except (FileNotFoundError, json.JSONDecodeError, NotADirectoryError):
        return default


def write_atomic(p, text):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".tmp-{p.name}")
    tmp.write_text(text)
    os.replace(tmp, p)


def read_jsonl(p):
    out = []
    try:
        for line in Path(p).read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    except FileNotFoundError:
        pass
    return out


# ---- lessons files ---------------------------------------------------------------------------------
FIELDS = ["when", "do", "for", "match", "platforms", "signature", "evidence", "status", "added", "used",
          "repeats", "last-used"]
HEADERS = {
    "general": "# spec2pr lessons: general\n\nLessons that hold in any repo. spec2pr adds them after the same problem\n"
               "shows up in two or more features, and asks you first. Edit freely: keep the `### ID · title`\n"
               "heading and the `- field: value` lines. status is on, off, proposed or archived.\n",
    "repo": "# spec2pr lessons: this repo\n\nLessons about this codebase, learned from earlier features and your corrections.\n"
            "spec2pr gives the relevant ones to its agents at the start of every feature. Edit freely:\n"
            "keep the `### ID · title` heading and the `- field: value` lines. Commit this file to share it.\n",
}


def lessons_path(scope, repo=None):
    return GENERAL if scope == "general" else Path(repo) / ".spec2pr" / "lessons.md"


def parse_lessons(text, scope):
    lessons, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^###\s+([A-Za-z]+-\d+)\s*(?:·|-|:)\s*(.+?)\s*$", line)
        if m:
            cur = {"id": m.group(1), "title": m.group(2), "scope": scope, "notes": []}
            lessons.append(cur)
            continue
        if cur is None:
            continue
        f = re.match(r"^-\s+([a-z][a-z-]*):\s?(.*)$", line)
        if f and f.group(1) in FIELDS:
            cur[f.group(1)] = f.group(2).strip()
        elif line.strip():
            cur["notes"].append(line.rstrip())
    for l in lessons:
        l["for"] = [a.strip() for a in l.get("for", "").split(",") if a.strip()]
        l["match"] = [k.strip() for k in l.get("match", "").split(",") if k.strip()]
        l["platforms"] = [p.strip() for p in (l.get("platforms") or "any").split(",") if p.strip()] or ["any"]
        l["status"] = l.get("status") or "on"
        for k in ("used", "repeats"):
            try:
                l[k] = int(l.get(k) or 0)
            except ValueError:
                l[k] = 0
        l["flagged"] = l["used"] >= FLAG_MIN_USED and l["repeats"] / max(l["used"], 1) >= FLAG_RATIO
    return lessons


def load_lessons(scope, repo=None):
    p = lessons_path(scope, repo)
    try:
        return parse_lessons(p.read_text(), scope)
    except FileNotFoundError:
        return []


def save_lessons(scope, lessons, repo=None):
    out = [HEADERS[scope].rstrip("\n")]
    for l in lessons:
        out.append(f"\n### {l['id']} · {l['title']}")
        for k in FIELDS:
            v = l.get(k)
            if isinstance(v, list):
                v = ", ".join(v)
            if v not in (None, ""):
                out.append(f"- {k}: {v}")
        out.extend(l.get("notes") or [])
    write_atomic(lessons_path(scope, repo), "\n".join(out) + "\n")


def next_id(scope, lessons):
    pre = "G" if scope == "general" else "R"
    n = max([int(l["id"].split("-")[1]) for l in lessons if l["id"].startswith(pre + "-")] or [0])
    return f"{pre}-{n + 1:03d}"


def scope_of(lesson_id):
    return "general" if lesson_id.upper().startswith("G-") else "repo"


def set_status(lesson_id, status, repo=None):
    """Used by the CLI and the dashboard. Returns (ok, message)."""
    if status not in ("on", "off", "archived", "proposed"):
        return False, "status must be on, off, archived or proposed"
    scope = scope_of(lesson_id)
    if scope == "repo" and not repo:
        return False, "repo lessons need --repo"
    lessons = load_lessons(scope, repo)
    for l in lessons:
        if l["id"].upper() == lesson_id.upper():
            l["status"] = status
            save_lessons(scope, lessons, repo)
            return True, "ok"
    return False, f"no lesson {lesson_id}"


# ---- history ---------------------------------------------------------------------------------------
def load_history():
    return read_jsonl(HISTORY)


def upsert_history(rec):
    rows = [r for r in load_history() if not (r.get("repo") == rec["repo"] and r.get("key") == rec["key"])]
    rows.append(rec)
    write_atomic(HISTORY, "".join(json.dumps(r) + "\n" for r in rows))


# ---- Phase 1: send-backs and the retro -------------------------------------------------------------
def cmd_sendback(a):
    if a.stage not in STAGES:
        sys.exit(f"learn.py: --stage must be one of {', '.join(STAGES)}")
    if a.tag not in TAGS:
        sys.exit(f"learn.py: --tag must be one of {', '.join(TAGS)}")
    sd = root_of(a.repo) / ".agent-state"
    sd.mkdir(parents=True, exist_ok=True)
    with open(sd / f"{a.ticket}-sendbacks.jsonl", "a") as f:
        f.write(json.dumps({"t": now(), "stage": a.stage, "tag": a.tag, "ac": a.ac, "why": a.why[:300]}) + "\n")
    print(f"{a.stage}/{a.tag}")


def flow_numbers(root, key):
    """Loops, cost and tokens from the dashboard's live-flow builder (same numbers the cards show)."""
    try:
        sys.path.insert(0, str(HERE.parent / "dashboard"))
        import server  # noqa: E402
        flow = server._build_flow(root, key)
        tot = flow.get("totals") or {}
        tok = tot.get("tokens") or {}
        return {"loops": len(flow.get("loops") or []), "cost_usd": tot.get("cost_usd"),
                "tokens": sum(tok.values()) if tok else None, "agents": tot.get("agents"),
                "tool_calls": tot.get("tool_calls")}
    except Exception as e:
        print(f"learn.py: cost/loop numbers unavailable ({type(e).__name__})", file=sys.stderr)
        return {"loops": None, "cost_usd": None, "tokens": None, "agents": None, "tool_calls": None}


def corrections(root, key):
    """Your answers that changed course: notes, typed answers, or "change the spec"."""
    out = []
    qd = root / ".agent-state" / "questions"
    for p in sorted(qd.glob("*.json")) if qd.is_dir() else []:
        q = read_json(p, {})
        if q.get("ticket") != key or q.get("status") != "answered":
            continue
        labels = [c.get("label") for c in q.get("choices") or []]
        ans = q.get("answer") or ""
        if q.get("note") or (labels and ans not in labels) or re.search(r"change", ans, re.I) or not labels:
            out.append({"question": q.get("title"), "answer": ans, "note": q.get("note"), "by": q.get("by")})
    return out


def minutes_between(a, b):
    try:
        fa = datetime.fromisoformat(a.replace("Z", "+00:00"))
        fb = datetime.fromisoformat(b.replace("Z", "+00:00"))
        return round((fb - fa).total_seconds() / 60)
    except Exception:
        return None


def cmd_retro(a):
    root = root_of(a.repo)
    sd = root / ".agent-state"
    st = read_json(sd / f"{a.ticket}.json", {}) or {}
    sendbacks = read_jsonl(sd / f"{a.ticket}-sendbacks.jsonl")
    events = [e for e in read_jsonl(sd / "events.jsonl") if e.get("ticket") == a.ticket]
    report = read_json(sd / f"{a.ticket}-verify" / "report.json", {}) or {}
    platforms = report.get("platforms") or ([report["platform"]] if report.get("platform") else [])
    sigs = sorted({f"{s['stage']}/{s['tag']}" for s in sendbacks})
    fl = flow_numbers(root, a.ticket)
    started = events[0]["t"] if events else st.get("updated")
    rec = {
        "repo": str(root), "key": a.ticket, "title": st.get("title") or a.ticket, "source": st.get("source"),
        "outcome": a.outcome, "platforms": platforms, "started": started, "ended": now(),
        "minutes": minutes_between(started, now()) if started else None,
        "rounds": st.get("attempts") or {}, "verdicts": st.get("verdicts") or {},
        "sendbacks": sendbacks, "signatures": sigs,
        "asks": sum(1 for e in events if e.get("ev") == "ask"),
        "corrections": corrections(root, a.ticket),
        "lessons_used": st.get("lessons_used") or [], "pr": st.get("pr"), **fl,
    }

    # Score the lessons this feature was given: did the problem they target happen anyway?
    scored, flagged = [], []
    used = set(rec["lessons_used"])
    prev = next((r for r in load_history() if r.get("repo") == rec["repo"] and r.get("key") == rec["key"]), None)
    already = set((prev or {}).get("scored") or [])   # a re-run of the retro must not count twice
    for scope in ("general", "repo"):
        lessons = load_lessons(scope, root)
        changed = False
        for l in lessons:
            if l["id"] in used and l["id"] not in already:
                l["used"] += 1
                l["last-used"] = today()
                if l.get("signature") and l["signature"] in sigs:
                    l["repeats"] += 1
                scored.append(l["id"]); changed = True
            if l["status"] == "on" and l["used"] >= FLAG_MIN_USED and l["repeats"] / l["used"] >= FLAG_RATIO:
                flagged.append({"id": l["id"], "title": l["title"], "used": l["used"], "repeats": l["repeats"],
                                "when": l.get("when"), "do": l.get("do")})
            last = l.get("last-used") or l.get("added")
            try:
                if l["status"] == "on" and last and \
                        datetime.strptime(last, "%Y-%m-%d") < datetime.now() - timedelta(days=STALE_DAYS):
                    l["status"] = "archived"; changed = True
            except ValueError:
                pass
        if changed:
            save_lessons(scope, lessons, root)
    rec["scored"] = sorted(already | set(scored))
    upsert_history(rec)
    write_atomic(sd / f"{a.ticket}-retro.json", json.dumps(rec, indent=2))

    # Lesson candidates: a problem from THIS feature that also happened in another feature.
    by_sig = defaultdict(list)
    for r in load_history():
        for s in r.get("signatures") or []:
            by_sig[s].append(r)
    have = {"general": {l.get("signature") for l in load_lessons("general")},
            "repo": {l.get("signature") for l in load_lessons("repo", root)}}
    candidates = []
    for s in sigs:
        feats = by_sig.get(s, [])
        if len({(r["repo"], r["key"]) for r in feats}) < 2:
            continue
        here = [r for r in feats if r["repo"] == str(root)]
        scope = "general" if len({r["repo"] for r in feats}) >= 2 else "repo"
        if s in have[scope] or (scope == "general" and len(here) >= 2 and s in have["repo"]):
            continue
        stage, tag = s.split("/", 1)
        examples = [{"repo": Path(r["repo"]).name, "feature": r["key"], "ac": sb.get("ac"), "why": sb.get("why")}
                    for r in feats for sb in r.get("sendbacks") or [] if f"{sb['stage']}/{sb['tag']}" == s][:6]
        candidates.append({"signature": s, "scope": scope, "problem": f"{STAGE_NAMES[stage]}: {TAGS[tag]}",
                           "features": sorted({f"{Path(r['repo']).name}/{r['key']}" for r in feats}),
                           "examples": examples})
    print(json.dumps({
        "record": {k: rec[k] for k in ("key", "outcome", "rounds", "signatures", "loops", "cost_usd", "tokens",
                                       "asks", "minutes")},
        "candidates": candidates, "corrections": rec["corrections"], "flagged": flagged,
        "how_to_add": "learn.py add --scope <scope> --repo ROOT --title … --when … --do … --for <agents> "
                      "--match <keywords> --signature <signature> --evidence <features>",
    }, indent=2))


# ---- Phase 2: picking lessons for a feature --------------------------------------------------------
def relevant(l, text, platforms):
    if l["status"] != "on":
        return False
    if platforms and "any" not in l["platforms"] and not set(l["platforms"]) & set(platforms):
        return False
    if l["scope"] == "repo" or not l["match"]:
        return True
    return any(k.lower() in text for k in l["match"])


def rank(l):
    return (l["repeats"] * 3 - l["used"], 0 if l["scope"] == "repo" else 1, l.get("added") or "")


def cmd_pick(a):
    root = root_of(a.repo)
    sd = root / ".agent-state"
    text = ""
    for f in a.text or []:
        p = Path(f)
        for q in ([p] if p.is_file() else sorted(p.glob("*.md")) if p.is_dir() else []):
            try:
                text += "\n" + q.read_text(errors="ignore")
            except OSError:
                pass
    text = text.lower()
    platforms = [p.strip() for p in (a.platforms or "").split(",") if p.strip()]
    pool = [l for l in load_lessons("repo", root) + load_lessons("general") if relevant(l, text, platforms)]
    per = {}
    for ag in AGENTS:
        mine = sorted([l for l in pool if ag in l["for"]], key=rank)[:PER_AGENT]
        if mine:
            per[ag] = mine
    picked = sorted({l["id"] for ls in per.values() for l in ls})
    st_path = sd / f"{a.ticket}.json"
    st = read_json(st_path, None)
    if isinstance(st, dict):
        st["lessons_used"] = sorted(set(st.get("lessons_used") or []) | set(picked))
        write_atomic(st_path, json.dumps(st, indent=2))
    out = [f"# Lessons for {a.ticket}",
           "Known pitfalls from earlier features. Follow them unless the spec says otherwise; the spec wins."]
    for ag, ls in per.items():
        out.append(f"\n## {ag}")
        for l in ls:
            line = f"- **{l['id']} {l['title']}**"
            if l.get("when"):
                line += f" When: {l['when']}."
            if l.get("do"):
                line += f" Do: {l['do']}"
            out.append(line)
    write_atomic(sd / f"{a.ticket}-lessons.md", "\n".join(out) + "\n")
    print(json.dumps({"file": str(sd / f"{a.ticket}-lessons.md"), "lessons": picked,
                      "per_agent": {ag: [l["id"] for l in ls] for ag, ls in per.items()}}))


# ---- Phase 2: managing lessons ---------------------------------------------------------------------
def cmd_add(a):
    root = root_of(a.repo) if a.repo else None
    if a.scope == "repo" and not root:
        sys.exit("learn.py: repo lessons need --repo")
    bad = [x for x in a.for_.split(",") if x.strip() and x.strip() not in AGENTS]
    if bad:
        sys.exit(f"learn.py: --for takes {', '.join(AGENTS)} (got {', '.join(bad)})")
    lessons = load_lessons(a.scope, root)
    status = a.status or ("proposed" if a.scope == "general" else "on")
    l = {"id": next_id(a.scope, lessons), "title": a.title.strip(), "scope": a.scope, "when": a.when, "do": a.do,
         "for": [x.strip() for x in a.for_.split(",") if x.strip()],
         "match": [x.strip() for x in (a.match or "").split(",") if x.strip()],
         "platforms": [x.strip() for x in (a.platforms or "any").split(",") if x.strip()],
         "signature": a.signature, "evidence": a.evidence, "status": status, "added": today(),
         "used": 0, "repeats": 0, "notes": []}
    lessons.append(l)
    save_lessons(a.scope, lessons, root)
    print(json.dumps({"id": l["id"], "status": status, "file": str(lessons_path(a.scope, root))}))


def cmd_set(a):
    ok, msg = set_status(a.id, a.status, root_of(a.repo) if a.repo else None)
    print(msg) if ok else sys.exit(f"learn.py: {msg}")


def cmd_edit(a):
    scope = scope_of(a.id)
    root = root_of(a.repo) if a.repo else None
    lessons = load_lessons(scope, root)
    for l in lessons:
        if l["id"].upper() == a.id.upper():
            for k in ("title", "when", "do", "match", "platforms", "signature"):
                v = getattr(a, k)
                if v is not None:
                    l[k] = [x.strip() for x in v.split(",")] if k in ("match", "platforms") else v
            if a.for_:
                l["for"] = [x.strip() for x in a.for_.split(",") if x.strip()]
            # A rewritten lesson starts a fresh scorecard.
            l["notes"] = (l.get("notes") or []) + [f"Rewritten {today()} (was used {l['used']}×, repeated {l['repeats']}×)."]
            l["used"], l["repeats"] = 0, 0
            save_lessons(scope, lessons, root)
            print("ok")
            return
    sys.exit(f"learn.py: no lesson {a.id}")


def learning_view(repo):
    """Everything the dashboard's Learning tab shows for one repo."""
    root = Path(repo)
    hist = [r for r in load_history() if r.get("repo") == str(root)]
    hist.sort(key=lambda r: r.get("ended") or "")
    counts = defaultdict(set)
    for r in hist:
        for s in r.get("signatures") or []:
            counts[s].add(r["key"])
    sigs = sorted(({"signature": s, "problem": f"{STAGE_NAMES.get(s.split('/')[0], s)}: {TAGS.get(s.split('/', 1)[-1], '')}",
                    "features": sorted(k)} for s, k in counts.items()), key=lambda x: -len(x["features"]))
    slim = [{k: r.get(k) for k in ("key", "title", "outcome", "ended", "minutes", "rounds", "signatures", "loops",
                                   "cost_usd", "tokens", "asks", "lessons_used", "pr")}
            | {"sendbacks": len(r.get("sendbacks") or []), "corrections": len(r.get("corrections") or [])}
            for r in hist[-30:]]
    strip = lambda ls: [{k: v for k, v in l.items() if k != "notes"} for l in ls]
    return {"history": slim, "signatures": sigs[:12],
            "lessons": {"repo": strip(load_lessons("repo", root)), "general": strip(load_lessons("general"))},
            "files": {"repo": str(lessons_path("repo", root)), "general": str(GENERAL)}}


def cmd_list(a):
    root = root_of(a.repo) if a.repo else None
    for scope in ("repo", "general"):
        if scope == "repo" and not root:
            continue
        for l in load_lessons(scope, root):
            flag = "  ⚠ not working" if l["flagged"] else ""
            print(f"{l['id']:6} {l['status']:9} used {l['used']:>2}× repeats {l['repeats']:>2}×  {l['title']}{flag}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sendback"); p.add_argument("--repo", default="."); p.add_argument("--ticket", required=True)
    p.add_argument("--stage", required=True); p.add_argument("--tag", required=True)
    p.add_argument("--why", required=True); p.add_argument("--ac"); p.set_defaults(fn=cmd_sendback)
    p = sub.add_parser("retro"); p.add_argument("--repo", default="."); p.add_argument("--ticket", required=True)
    p.add_argument("--outcome", required=True, choices=["pr", "done", "blocked", "stopped"]); p.set_defaults(fn=cmd_retro)
    p = sub.add_parser("pick"); p.add_argument("--repo", default="."); p.add_argument("--ticket", required=True)
    p.add_argument("--text", nargs="*"); p.add_argument("--platforms"); p.set_defaults(fn=cmd_pick)
    p = sub.add_parser("add"); p.add_argument("--scope", required=True, choices=["general", "repo"])
    p.add_argument("--repo"); p.add_argument("--title", required=True); p.add_argument("--when", required=True)
    p.add_argument("--do", required=True); p.add_argument("--for", dest="for_", required=True)
    p.add_argument("--match"); p.add_argument("--platforms"); p.add_argument("--signature")
    p.add_argument("--evidence"); p.add_argument("--status", choices=["on", "off", "proposed"]); p.set_defaults(fn=cmd_add)
    p = sub.add_parser("set"); p.add_argument("id"); p.add_argument("status"); p.add_argument("--repo")
    p.set_defaults(fn=cmd_set)
    p = sub.add_parser("edit"); p.add_argument("id"); p.add_argument("--repo")
    for k in ("title", "when", "do", "match", "platforms", "signature"):
        p.add_argument(f"--{k}")
    p.add_argument("--for", dest="for_"); p.set_defaults(fn=cmd_edit)
    p = sub.add_parser("list"); p.add_argument("--repo"); p.set_defaults(fn=cmd_list)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
