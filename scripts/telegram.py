#!/usr/bin/env python3
"""Answer spec2pr's questions from Telegram, so a feature keeps moving while you are away from your desk.

  telegram.py setup [--token T]   connect your own bot (asks for the token, then waits for you to press Start)
  telegram.py status              is it connected?
  telegram.py test                send a test message
  telegram.py notify TEXT         send a short message (used for loop warnings and "PR opened")
  telegram.py off                 disconnect (deletes ~/.spec2pr/telegram.json)
  telegram.py poll [--seconds N]  fetch replies once (the dashboard server and ask.py do this for you)

How it works: ask.py sends every "Needs you" question to your Telegram chat with one button per choice.
Tap a button, or reply to the message with text ("Change the spec: Visa only"). The answer is written
into the same question file the dashboard and the terminal use, so whichever answer comes first wins,
and the Telegram message is updated to show it.

Only the chat that pressed Start during setup can answer; messages from anyone else are ignored.
The bot token is stored in ~/.spec2pr/telegram.json (readable only by you) and never logged.
Standard library only. Set FF_TELEGRAM=off to stop sending without disconnecting.
"""
import argparse
import fcntl
import getpass
import html
import json
import os
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home() / ".spec2pr"
CONFIG = HOME / "telegram.json"
STATE = HOME / "telegram-state.json"     # {"offset": N, "messages": {message_id: {repo, qid, t, closed}}}
POLL_LOCK = HOME / "telegram-poll.lock"  # one getUpdates caller at a time (dashboard server or ask.py wait)
STATE_LOCK = HOME / "telegram-state.lock"
API = os.environ.get("FF_TELEGRAM_API", "https://api.telegram.org") + "/bot{token}/{method}"  # override only for tests
KEEP_DAYS = 30


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- config and state ------------------------------------------------------------------------------
def config():
    if os.environ.get("FF_TELEGRAM", "on") == "off":
        return None
    try:
        c = json.loads(CONFIG.read_text())
        return c if c.get("token") and c.get("chat_id") else None
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write_private(path, data):
    HOME.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".tmp-{path.name}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


@contextmanager
def state_locked():
    """Read-modify-write the state file under a lock; yields the dict, saves it afterwards."""
    HOME.mkdir(parents=True, exist_ok=True)
    with open(STATE_LOCK, "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            st = json.loads(STATE.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            st = {}
        st.setdefault("offset", 0)
        st.setdefault("messages", {})
        yield st
        cutoff = time.time() - KEEP_DAYS * 86400
        st["messages"] = {k: v for k, v in st["messages"].items() if v.get("ts", 0) >= cutoff}
        _write_private(STATE, st)


# ---- Telegram Bot API -----------------------------------------------------------------------------
class TgError(Exception):
    pass


def api(token, method, http_timeout=15, **params):
    req = urllib.request.Request(API.format(token=token, method=method), data=json.dumps(params).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=http_timeout) as r:
            body = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
        except Exception:
            raise TgError(f"HTTP {e.code}") from None
    except Exception as e:  # network down, timeout, DNS: never crash the caller
        raise TgError(type(e).__name__) from None
    if not body.get("ok"):
        raise TgError(body.get("description") or "request failed")
    return body.get("result")


def send(text, buttons=None, force_reply=False, cfg=None):
    cfg = cfg or config()
    if not cfg:
        return None
    params = {"chat_id": cfg["chat_id"], "text": text[:4000], "parse_mode": "HTML",
              "disable_web_page_preview": True}
    if buttons:
        params["reply_markup"] = {"inline_keyboard": buttons}
    elif force_reply:
        params["reply_markup"] = {"force_reply": True, "input_field_placeholder": "Your answer"}
    return api(cfg["token"], "sendMessage", **params)


# ---- questions ------------------------------------------------------------------------------------
def question_path(repo, qid):
    return Path(repo) / ".agent-state" / "questions" / f"{qid}.json"


def read_q(repo, qid):
    try:
        return json.loads(question_path(repo, qid).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def question_text(repo, q, footer=True):
    e = html.escape
    head = f"🟡 <b>{e(Path(repo).name)}</b>" + (f" · {e(q['ticket'])}" if q.get("ticket") else "")
    parts = [head, f"<b>{e(q.get('title') or '')}</b>"]
    ctx = (q.get("context") or "").strip()
    if ctx:
        parts.append(e(ctx if len(ctx) <= 2800 else ctx[:2800] + " …"))
    if footer:
        parts.append("<i>" + ("Tap a choice, or reply to this message to add a note "
                              "(e.g. \"Change the spec: Visa only\")." if q.get("choices") else
                              "Reply to this message with your answer.") + "</i>")
    return "\n\n".join(parts)


def send_question(repo, q):
    """Send one question with a button per choice. Silently does nothing when Telegram isn't set up."""
    cfg = config()
    if not cfg:
        return False
    buttons = [[{"text": ("⭐ " if c.get("recommended") else "") + c["label"],
                 "callback_data": f"a:{q['id']}:{i}"[:64]}] for i, c in enumerate(q.get("choices") or [])]
    try:
        msg = send(question_text(repo, q), buttons=buttons or None, force_reply=not buttons, cfg=cfg)
    except TgError as e:
        print(f"telegram: could not send the question ({e})", file=sys.stderr)
        return False
    with state_locked() as st:
        st["messages"][str(msg["message_id"])] = {"repo": str(repo), "qid": q["id"], "ts": time.time(),
                                                  "closed": False}
    return True


def record_answer(repo, qid, answer, note, by):
    """Write the answer into the question file (same format as the dashboard). Returns (ok, question)."""
    path = question_path(repo, qid)
    q = read_q(repo, qid)
    if not isinstance(q, dict):
        return False, None
    if q.get("status") != "open":
        return False, q
    q.update(status="answered", answer=answer, note=note or None, by=by, answered=now())
    tmp = path.with_name(f".tmp-{path.name}")
    tmp.write_text(json.dumps(q, indent=2))
    os.replace(tmp, path)
    with open(Path(repo) / ".agent-state" / "events.jsonl", "a") as f:
        f.write(json.dumps({"t": q["answered"], "ticket": q.get("ticket"), "ev": "answer", "agent": "you",
                            "id": None}) + "\n")
    return True, q


def parse_reply(q, text):
    """A typed reply: "<choice label>[: note]" picks that choice; anything else is the answer itself."""
    text = text.strip()
    for c in q.get("choices") or []:
        lab = c["label"]
        rest = text[len(lab):]
        if text.lower().startswith(lab.lower()) and (not rest or rest[0] in ":-—,\n"):
            return lab, rest.lstrip(" :-—,\n").strip() or None
    return text, None


def _close_message(cfg, mid, repo, q):
    """Replace the buttons with who answered what."""
    by = {"telegram": "here", "dashboard": "on the dashboard", "terminal": "in the terminal"}.get(q.get("by"),
                                                                                                    q.get("by"))
    done = f"\n\n✅ <b>{html.escape(str(q.get('answer')))}</b>" + (f" — {html.escape(q['note'])}" if q.get("note") else "") \
        + f" <i>(answered {html.escape(str(by))})</i>"
    try:
        api(cfg["token"], "editMessageText", chat_id=cfg["chat_id"], message_id=int(mid),
            text=(question_text(repo, q, footer=False) + done).replace("🟡", "✅", 1)[:4000], parse_mode="HTML",
            disable_web_page_preview=True)
    except TgError:
        pass


def _open_messages(st):
    return [(mid, m) for mid, m in st["messages"].items() if not m.get("closed")]


def handle_update(cfg, st, u):
    chat = str(cfg["chat_id"])
    cb = u.get("callback_query")
    if cb:
        if str((cb.get("message") or {}).get("chat", {}).get("id")) != chat:
            return
        mid = str(cb["message"]["message_id"])
        m = st["messages"].get(mid)
        try:
            _, qid, idx = cb.get("data", "").split(":")
            idx = int(idx)
        except ValueError:
            m = None
        reply = "This question is no longer open."
        if m and m["qid"] == qid:
            q = read_q(m["repo"], qid)
            choices = (q or {}).get("choices") or []
            if q and 0 <= idx < len(choices):
                ok, q = record_answer(m["repo"], qid, choices[idx]["label"], None, "telegram")
                reply = f"Got it: {choices[idx]['label']}" if ok else \
                    f"Already answered ({q.get('by')}): {q.get('answer')}" if q else reply
            if q and q.get("status") != "open":
                _close_message(cfg, mid, m["repo"], q)
                m["closed"] = True
        try:
            api(cfg["token"], "answerCallbackQuery", callback_query_id=cb["id"], text=reply[:190])
        except TgError:
            pass
        return

    msg = u.get("message")
    if not msg or str(msg.get("chat", {}).get("id")) != chat:
        return
    text = (msg.get("text") or "").strip()
    if not text:
        return
    if text.startswith("/"):
        cmd = text.split()[0].split("@")[0].lower()
        if cmd in ("/status", "/open", "/start", "/help"):
            opens = []
            for mid, m in _open_messages(st):
                q = read_q(m["repo"], m["qid"])
                if q and q.get("status") == "open":
                    opens.append(f"• <b>{html.escape(Path(m['repo']).name)}</b>: {html.escape(q.get('title') or '')}")
            body = ("Waiting for you:\n" + "\n".join(opens)) if opens else "Nothing is waiting for you right now."
            if cmd in ("/start", "/help"):
                body = ("spec2pr is connected. Questions from your features arrive here: tap a button, or "
                        "reply to the question with text.\n/status lists what is waiting.\n\n" + body)
            send(body, cfg=cfg)
        return

    target = str((msg.get("reply_to_message") or {}).get("message_id") or "")
    if target not in st["messages"]:
        # Not a reply: fine if exactly one question is waiting, otherwise ask which one.
        waiting = [(mid, m) for mid, m in _open_messages(st)
                   if (read_q(m["repo"], m["qid"]) or {}).get("status") == "open"]
        if len(waiting) == 1:
            target = waiting[0][0]
        else:
            send("More than one question is waiting. Reply to the one you are answering (swipe left on it)."
                 if waiting else "Nothing is waiting for you right now.", cfg=cfg)
            return
    m = st["messages"][target]
    q = read_q(m["repo"], m["qid"])
    if not q:
        return
    answer, note = parse_reply(q, text)
    ok, q = record_answer(m["repo"], m["qid"], answer, note, "telegram")
    if q and q.get("status") != "open":
        _close_message(cfg, target, m["repo"], q)
        m["closed"] = True
    if not ok and q:
        send(f"That was already answered ({html.escape(str(q.get('by')))}): {html.escape(str(q.get('answer')))}",
             cfg=cfg)


def sync_closed(cfg, st):
    """Questions answered on the dashboard or in the terminal: update their Telegram messages."""
    for mid, m in _open_messages(st):
        q = read_q(m["repo"], m["qid"])
        if q is None:
            m["closed"] = True
        elif q.get("status") != "open":
            _close_message(cfg, mid, m["repo"], q)
            m["closed"] = True


def poll_once(seconds=0):
    """Fetch and apply replies. Returns False if Telegram isn't set up or another process is polling."""
    cfg = config()
    if not cfg:
        return False
    HOME.mkdir(parents=True, exist_ok=True)
    with open(POLL_LOCK, "a") as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        with state_locked() as st:
            offset = st["offset"]
        try:
            updates = api(cfg["token"], "getUpdates", http_timeout=seconds + 10, offset=offset, timeout=seconds,
                          allowed_updates=["message", "callback_query"])
        except TgError:
            updates = []
        with state_locked() as st:
            for u in updates or []:
                st["offset"] = max(st["offset"], u["update_id"] + 1)
                try:
                    handle_update(cfg, st, u)
                except Exception as e:  # one bad update must not block the rest
                    print(f"telegram: skipped an update ({type(e).__name__})", file=sys.stderr)
            sync_closed(cfg, st)
    return True


def poll_forever():
    """Background loop for the dashboard server."""
    while True:
        try:
            if not poll_once(25):
                time.sleep(5)
        except Exception:
            time.sleep(10)


# ---- commands --------------------------------------------------------------------------------------
def cmd_setup(a):
    print("Connect spec2pr to Telegram (your own private bot, about one minute):\n"
          "  1. In Telegram, open @BotFather and send /newbot. Pick any name.\n"
          "  2. BotFather replies with a token like 123456:ABC-DEF… Copy it.\n")
    token = (a.token or os.environ.get("SPEC2PR_TELEGRAM_TOKEN") or getpass.getpass("Paste the token (hidden): ")).strip()
    try:
        me = api(token, "getMe")
    except TgError as e:
        sys.exit(f"That token didn't work ({e}).")
    try:
        api(token, "deleteWebhook")
    except TgError:
        pass
    # Skip anything sent to the bot before now.
    old = api(token, "getUpdates", offset=-1)
    offset = old[-1]["update_id"] + 1 if old else 0
    print(f"\n  3. Open https://t.me/{me['username']} and press Start (or send it any message).\n"
          "     Waiting up to 3 minutes…")
    deadline = time.time() + 180
    chat = None
    while time.time() < deadline and not chat:
        for u in api(token, "getUpdates", http_timeout=40, offset=offset, timeout=25) or []:
            offset = u["update_id"] + 1
            c = (u.get("message") or {}).get("chat") or {}
            if c.get("type") == "private":
                chat = c
                break
    if not chat:
        sys.exit("No message arrived. Run setup again and press Start in the bot chat.")
    api(token, "getUpdates", offset=offset)  # confirm what we read
    cfg = {"token": token, "chat_id": chat["id"], "bot": me["username"],
           "name": chat.get("first_name") or chat.get("username") or "", "connected": now()}
    _write_private(CONFIG, cfg)
    with state_locked() as st:
        st["offset"] = offset
    send("✅ <b>spec2pr is connected.</b>\nWhen a feature needs you, the question arrives here. Tap a button "
         "or reply to the message. /status lists what is waiting.", cfg=cfg)
    print(f"\nConnected to @{me['username']}. Questions will now also arrive in Telegram.")


def cmd_status(a):
    c = config()
    if not c:
        off = os.environ.get("FF_TELEGRAM") == "off" and CONFIG.exists()
        print("Telegram: paused (FF_TELEGRAM=off)" if off else "Telegram: not connected. Run: telegram.py setup")
        sys.exit(1)
    print(f"Telegram: connected to @{c.get('bot')} (since {c.get('connected')})")


def cmd_test(a):
    try:
        ok = send("👋 Test from spec2pr: questions will arrive like this.")
    except TgError as e:
        sys.exit(f"Telegram: sending failed ({e})")
    if not ok:
        sys.exit("Telegram: not connected. Run: telegram.py setup")
    print("Sent.")


def cmd_notify(a):
    try:
        send(html.escape(a.text))
    except TgError:
        pass


def cmd_off(a):
    for p in (CONFIG, STATE):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    print("Telegram disconnected. (You can delete the bot in @BotFather with /deletebot.)")


def cmd_poll(a):
    if not poll_once(a.seconds):
        print("Telegram not connected, or another process is already fetching replies.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("setup"); p.add_argument("--token"); p.set_defaults(fn=cmd_setup)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("test").set_defaults(fn=cmd_test)
    p = sub.add_parser("notify"); p.add_argument("text"); p.set_defaults(fn=cmd_notify)
    sub.add_parser("off").set_defaults(fn=cmd_off)
    p = sub.add_parser("poll"); p.add_argument("--seconds", type=int, default=0); p.set_defaults(fn=cmd_poll)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
