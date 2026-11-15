---
description: Answer spec2pr's questions from your phone - connect your own Telegram bot (one minute), test it, or disconnect it
argument-hint: [status | test | off]
allowed-tools: Bash
---
# spec2pr: Telegram answers

Arguments: $ARGUMENTS

`TG` = `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/telegram.py"`. If that path was not expanded or does
not exist, find the script with
`find ~/.claude/plugins -path '*spec2pr*/scripts/telegram.py' 2>/dev/null | head -1`.

- `status`, `test` or `off` → run `TG <that>` and report the result in one line.
- No argument → run `TG status`.
  - Connected → say so and offer `/telegram-setup test` and `/telegram-setup off`.
  - Not connected → the bot token is a secret, so **never ask the user to paste it into this chat**.
    Tell them, in short plain steps:
    1. In Telegram, open **@BotFather**, send `/newbot`, pick any name; it replies with a token.
    2. In a terminal (not here), run `<full path of telegram.py> setup` and paste the token when asked
       (it is hidden as you type). Print the real absolute path so they can copy the line.
    3. Open the link it prints and press **Start**. That's it.
    Then: from now on every "Needs you" question also arrives in Telegram with a button per choice;
    tap one, or reply to the message with text (e.g. "Change the spec: Visa only"). Whatever is
    answered first (phone, dashboard or terminal) wins. Loop warnings and "PR opened" also arrive
    there. Claude Code must stay open on the laptop (the laptop can't be asleep) for the feature to
    keep going. `FF_TELEGRAM=off` pauses it without disconnecting.
