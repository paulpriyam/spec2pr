#!/usr/bin/env python3
"""Ask the user a question through the spec2pr dashboard (and a desktop notification).

  ask.py post   --title T [--context C] [--choices JSON] [--ticket KEY] [--repo DIR]  -> prints the id
  ask.py wait   ID [--repo DIR]                -> blocks until answered, prints the answer as JSON
  ask.py answer ID --text TEXT [--repo DIR]    -> record an answer given in the terminal (closes the card)
  ask.py list   [--repo DIR]                   -> open questions as JSON

--choices is a JSON list of {"label": "...", "detail": "...", "recommended": true}. Without choices the
dashboard shows a text box. --repo may be the main checkout or a feature worktree. Questions live in
the main checkout's .agent-state/questions/<id>.json; the dashboard
writes the answer into the same file. Set FF_NOTIFY=off to skip the desktop notification.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def root_of(repo):
    """The main checkout: feature worktrees keep their state there too (see scripts/lib.sh)."""
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


def qdir(repo):
    return root_of(repo) / ".agent-state" / "questions"


def write_atomic(path, data):
    tmp = path.with_name(f".tmp-{path.name}")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def log_event(repo, ev, ticket):
    sd = root_of(repo) / ".agent-state"
    with open(sd / "events.jsonl", "a") as f:
        f.write(json.dumps({"t": now(), "ticket": ticket, "ev": ev, "agent": "you", "id": None}) + "\n")


def current_ticket(repo):
    try:
        return (root_of(repo) / ".agent-state" / "current").read_text().strip() or None
    except FileNotFoundError:
        return None


def notify(repo, title):
    if os.environ.get("FF_NOTIFY", "on") == "off" or sys.platform != "darwin":
        return
    port = os.environ.get("FF_DASHBOARD_PORT", "7788")
    url = f"http://127.0.0.1:{port}/?repo=" + urllib.parse.quote(str(root_of(repo)), safe="")
    name = root_of(repo).name
    try:
        if shutil.which("terminal-notifier"):
            subprocess.run(["terminal-notifier", "-title", f"spec2pr · {name}", "-subtitle", "Needs you",
                            "-message", title, "-open", url, "-sound", "default"], timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            script = ('on run argv\n display notification (item 1 of argv) with title (item 2 of argv) '
                      'subtitle "Needs you · answer on the dashboard or in the terminal" sound name "Glass"\nend run')
            subprocess.run(["osascript", "-e", script, title, f"spec2pr · {name}"], timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def cmd_post(a):
    choices = json.loads(a.choices) if a.choices else []
    if not isinstance(choices, list) or not all(isinstance(c, dict) and c.get("label") for c in choices):
        sys.exit("ask.py: --choices must be a JSON list of {\"label\": ...}")
    d = qdir(a.repo); d.mkdir(parents=True, exist_ok=True)
    qid = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    ticket = a.ticket or current_ticket(a.repo)
    write_atomic(d / f"{qid}.json", {
        "id": qid, "ticket": ticket, "title": a.title, "context": a.context or "",
        "choices": choices, "status": "open", "asked": now(),
    })
    log_event(a.repo, "ask", ticket)
    # Make sure the dashboard is up so the card can be answered there (no new browser tab).
    subprocess.run([str(HERE / "ensure-dashboard.sh"), str(root_of(a.repo)), "--no-open"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    notify(a.repo, a.title)
    print(qid)


def load(a):
    p = qdir(a.repo) / f"{a.id}.json"
    if not p.exists():
        sys.exit(f"ask.py: no question {a.id} in {qdir(a.repo)}")
    return p, json.loads(p.read_text())


def cmd_wait(a):
    p, q = load(a)
    while q.get("status") == "open":
        time.sleep(1)
        try:
            q = json.loads(p.read_text())
        except (json.JSONDecodeError, FileNotFoundError):
            continue
    print(json.dumps({k: q.get(k) for k in ("id", "title", "answer", "note", "by", "answered")}))


def cmd_answer(a):
    p, q = load(a)
    if q.get("status") != "open":
        print(json.dumps({"already_answered": True, "answer": q.get("answer"), "by": q.get("by")}))
        return
    q.update(status="answered", answer=a.text, note=None, by="terminal", answered=now())
    write_atomic(p, q)
    log_event(a.repo, "answer", q.get("ticket"))
    print(json.dumps({"answer": a.text, "by": "terminal"}))


def cmd_list(a):
    d = qdir(a.repo)
    qs = [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))] if d.is_dir() else []
    print(json.dumps([q for q in qs if q.get("status") == "open"], indent=2))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("post"); p.add_argument("--title", required=True); p.add_argument("--context")
    p.add_argument("--choices"); p.add_argument("--ticket"); p.set_defaults(fn=cmd_post)
    p = sub.add_parser("wait"); p.add_argument("id"); p.set_defaults(fn=cmd_wait)
    p = sub.add_parser("answer"); p.add_argument("id"); p.add_argument("--text", required=True)
    p.set_defaults(fn=cmd_answer)
    p = sub.add_parser("list"); p.set_defaults(fn=cmd_list)
    for s in sub.choices.values():
        s.add_argument("--repo", default=".")
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
