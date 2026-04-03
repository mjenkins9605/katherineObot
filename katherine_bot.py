"""
Katherine O'Brien Slack Bot - Simplified Edition
=================================================
Two fun behaviors while Katherine's on maternity leave:

  1. #merch-ecomm   — Posts one random snarky Katherine comment per day
  2. #what-time-today — Joins the time-guessing game (8-10am MT) once 3+ people guess

NO Anthropic API key needed — all responses are pre-written in Katherine's voice!

HOW TO SET THIS UP:
-------------------
1. Create the Slack app from the manifest (Michael has it)
2. Get your two Slack tokens:
     - Bot Token  (xoxb-...)  →  Slack App → OAuth & Permissions → Bot User OAuth Token
     - App Token  (xapp-...)  →  Slack App → Basic Information → App-Level Tokens
3. Copy .env.example to .env and fill in those two tokens
4. Run:  pip install -r requirements.txt
5. Run:  python katherine_bot.py
"""

import os
import re
import random
from datetime import datetime
import pytz
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient

load_dotenv()

MOUNTAIN_TZ = pytz.timezone("America/Denver")

# ── Channel IDs ──────────────────────────────────────────────────────────────
MERCH_ECOMM_CHANNEL = "C05QH696L14"   # #merch-ecomm
WHAT_TIME_CHANNEL   = "C0ADAKXNDA4"   # #what-time-today

# ── Whitelist: only #merch-ecomm members ────────────────────────────────────
ALLOWED_USER_IDS = {
    "U0JSHHB24",    # Michael Jenkins
    "U05AM480KSB",  # Rich Nelson
    "U06FDGX31S4",  # Samuel Fink
    "U05RND02J22",  # Madelyn Frost
    "U02JML99TQQ",  # Rob Simpson
    "U06QS0TBRLY",  # Matt Herrick
    "U0684TQN831",  # Charles Day
}

# ── Katherine's pre-written snarky lines for #merch-ecomm ───────────────────
# These rotate randomly each day so it never feels repetitive.
# To add more, just add a new quoted line inside the brackets!
SNARKY_LINES = [
    "I know!",
    "literally why does this always happen haha",
    "WOOOOOW okay then",
    "That's the best news ever! 🎉",
    "Oh I brought my laptop no problem hahaha",
    "Wait what?? haha",
    "Oh geez 😂",
    "Are we sure about this lol",
    "Sam did it again didn't he haha",
    "That's crazy!!",
    "STOP 😂",
    "Literally!! hahaha",
    "Oh that's sick!",
    "Wow you guys are so speedy!!",
    "That's actually wild haha",
    "No way 😂",
    "Okay but same though haha",
    "I'm dying 😂😂",
    "Wait... is this Sam's fault again",
    "WHHHHYYY hahaha",
    "That tracks honestly lol",
    "Oh my gosh YES",
    "Rich to the rescue again 😂",
    "The warehouse is going to love that 😂",
    "That's literally so funny",
    "Okay but good point though!!",
    "baby brain says same hahaha",
    "I mean... fair 😂",
    "not me checking slack while holding a baby lol",
    "100% this",
    "so what you're saying is someone is getting fired? 😂",
    "I'm sick one day and everything falls apart lol",
    "wow that seems a little crazy haha",
    "They're ridiculous 😂",
    "Oh I thought they were in the chat hahaha",
    "nobody tells me anything 😂",
    "adding this to my list of things that are somehow Sam's fault",
    "change of plans... again haha",
    "I'm not even surprised anymore lol",
    "we all saw how that went hahaha",
    # ── Added batch (voice-matched from real Slack messages + new) ──
    "Stop what is that 😂",
    "25 hours later\u2026",
    "Haven't heard a word!",
    "You know me well haha",
    "For what haha",
    "I can't tell if you're joking or not? Haha",
    "HAHAHA that's awesome",
    "Dang it",
    "Can't trust it",
    "That is the winner",
    "He's been cloned",
    "What does Sam think he's doing haha",
    "Haha does that mean it's coming?",
    "Haha yes that's a whole other process",
    "Same haha",
    "Haha deal",
    "I did haha",
    "Amazing thank you!",
    "Yes, on it!",
    "It does!",
    "He's keeping it appropriate",
    "HAPPY WHATEVER EVERYONE",
    "okay who did this 😂",
    "I leave for five minutes hahaha",
    # ── Katherine-voice originals ──
    "oh no who let Sam near the inventory again 😂",
    "why am I not surprised hahaha",
    "literally WHAT haha",
    "okay that's actually kind of iconic though",
    "I'm going to pretend I didn't see that lol",
    "you guys are UNREAL 😂",
    "wait hold on that's actually genius haha",
    "someone please screenshot this before it gets deleted 😂",
    "I feel like I should be concerned haha",
    "okay but why is that so accurate 😂😂",
]

# ── Shuffled deck so every line gets used before any repeats ────────────────
_snarky_deck = []

def _shuffle_deck():
    """Shuffle all lines, then skip a random number so restarts don't repeat."""
    global _snarky_deck
    _snarky_deck = list(SNARKY_LINES)
    random.shuffle(_snarky_deck)
    # Skip a random chunk so each restart begins at a different point
    skip = random.randint(0, len(_snarky_deck) // 2)
    _snarky_deck = _snarky_deck[skip:]

def next_snarky_line():
    """Draw from a shuffled deck of lines. Re-shuffles when exhausted."""
    if not _snarky_deck:
        _shuffle_deck()
    return _snarky_deck.pop()

# ── Slack app ────────────────────────────────────────────────────────────────
app = App(token=os.environ["SLACK_BOT_TOKEN"])

# ── Daily state ─────────────────────────────────────────────────────────────
todays_guesses       = {}    # { user_id: minutes_since_midnight }
what_time_responded  = False
merch_responded      = False
merch_message_count  = 0
merch_trigger_count  = random.choice([3, 4])  # fire on the 3rd or 4th message
last_reset_date      = None


def reset_daily_state():
    """Clear all daily tracking at the start of each new day."""
    global todays_guesses, what_time_responded, merch_responded
    global merch_message_count, merch_trigger_count, last_reset_date
    todays_guesses      = {}
    what_time_responded = False
    merch_responded     = False
    merch_message_count = 0
    merch_trigger_count = random.choice([3, 4])  # re-randomize each day
    last_reset_date     = datetime.now(MOUNTAIN_TZ).date()


# ── Time parsing helpers ─────────────────────────────────────────────────────

def parse_time(text: str):
    """
    Try to pull a time out of a message like '9:30', '10', '2:00 pm', '1', '3', etc.
    Returns total minutes since midnight, or None if no time found.
    """
    text = text.strip().lower()
    pattern = r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b'
    match = re.search(pattern, text)
    if not match:
        return None

    hour   = int(match.group(1))
    minute = int(match.group(2)) if match.group(2) else 0
    ampm   = match.group(3)

    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    elif not ampm:
        # No am/pm given — assume PM for 1-6 (e.g. "2:30" = 2:30pm in a work context)
        # and AM for 7-12 (e.g. "11:30" = 11:30am)
        if 1 <= hour <= 6:
            hour += 12

    # Only accept reasonable work-day times (7am-6pm)
    if not (7 <= hour <= 18):
        return None

    return hour * 60 + minute


def minutes_to_str(total_minutes: int) -> str:
    """Convert 570 -> '9:30' style string."""
    hour   = total_minutes // 60
    minute = total_minutes % 60
    ampm   = "am" if hour < 12 else "pm"
    if hour > 12:
        hour -= 12
    if hour == 0:
        hour = 12
    return f"{hour}:{minute:02d}"


# ── Behavior 1: Daily snarky comment in #merch-ecomm ─────────────────────────

def post_merch_snark(client: WebClient, message_ts: str):
    """Reply to a specific message with a random Katherine snarky line."""
    try:
        response = next_snarky_line()
        client.chat_postMessage(
            channel   = MERCH_ECOMM_CHANNEL,
            thread_ts = message_ts,
            text      = response,
        )
        print(f"[merch-snark] Replied with: {response}")
    except Exception as e:
        print(f"[merch-snark] Error: {e}")


# ── Behavior 2: Join the time-guessing game in #what-time-today ──────────────

def post_katherine_time_guess(client: WebClient):
    """
    Post Katherine's time guess — within 15-30 min of either the
    earliest or latest guess so far, rounded to the nearest quarter hour.
    """
    times     = list(todays_guesses.values())
    taken     = set(times)  # avoid duplicate guesses (Rule III.a)
    base_time = random.choice([min(times), max(times)])  # near first or last posted time
    offset    = random.choice([15, 30])                  # 1 or 2 quarter-hours away
    guess     = base_time + offset
    # Round to nearest quarter hour (:00, :15, :30, :45)
    guess     = round(guess / 15) * 15
    # If that time is already taken, try the other offset
    if guess in taken:
        other_offset = 30 if offset == 15 else 15
        guess = round((base_time + other_offset) / 15) * 15
    time_str  = minutes_to_str(guess)

    client.chat_postMessage(
        channel = WHAT_TIME_CHANNEL,
        text    = f"{time_str} haha",
    )
    print(f"[what-time] Posted Katherine's guess: {time_str}")


# ── Katherine's @mention responses ──────────────────────────────────────────
MENTION_LINES = [
    "what do you need haha",
    "I'm literally holding a baby right now 😂",
    "okay okay I'm here haha what's up",
    "you rang? 👶",
    "I'm on leave but sure haha what happened",
    "oh geez who broke something",
    "I step away for ONE second haha",
    "baby's asleep so I have like 4 minutes haha",
    "present! sort of haha",
    "WOOOOOW you guys can't go one day haha",
]

_mention_deck = []

def next_mention_line():
    """Draw from a shuffled deck combining mention + snarky lines with random start offset."""
    global _mention_deck
    if not _mention_deck:
        _mention_deck = list(MENTION_LINES) + list(SNARKY_LINES)
        random.shuffle(_mention_deck)
        skip = random.randint(0, len(_mention_deck) // 2)
        _mention_deck = _mention_deck[skip:]
    return _mention_deck.pop()

@app.event("app_mention")
def handle_mention(event, say):
    """Respond to @mentions in the same thread, not as a new post."""
    user_id = event.get("user")
    if user_id not in ALLOWED_USER_IDS:
        return
    thread_ts = event.get("thread_ts") or event.get("ts")
    say(text=next_mention_line(), thread_ts=thread_ts)


@app.event("message")
def handle_message(event, client):
    """Watch both channels for their respective triggers."""
    global todays_guesses, what_time_responded, merch_responded
    global merch_message_count, last_reset_date

    # Skip bot messages and edits/deletes
    if event.get("bot_id") or event.get("subtype"):
        return

    channel = event.get("channel")
    user_id = event.get("user")

    if user_id not in ALLOWED_USER_IDS:
        return

    # Reset if it's a new day
    now = datetime.now(MOUNTAIN_TZ)
    if last_reset_date != now.date():
        reset_daily_state()

    # ── Sam tax: react to every Sam message with excited-katherine ──────────
    SAM_USER_ID = "U06FDGX31S4"
    if user_id == SAM_USER_ID:
        try:
            client.reactions_add(
                channel  = channel,
                name     = "excited-katherine",
                timestamp = event.get("ts"),
            )
        except Exception as e:
            print(f"[sam-reaction] Error: {e}")

    # ── #merch-ecomm: fire on the 3rd or 4th message, before 1pm ────────────
    if channel == MERCH_ECOMM_CHANNEL and not merch_responded and now.hour < 13:
        if not event.get("thread_ts"):  # top-level messages only
            merch_message_count += 1
            print(f"[merch-snark] Message count today: {merch_message_count} / trigger at: {merch_trigger_count}")
            if merch_message_count >= merch_trigger_count:
                merch_responded = True
                post_merch_snark(client, event.get("ts"))

    # ── #what-time-today: fire when 3+ people guess between 8-11am ──────────
    if channel == WHAT_TIME_CHANNEL and not what_time_responded:
        if not (8 <= now.hour < 11):
            return
        parsed = parse_time(event.get("text", ""))
        if parsed:
            todays_guesses[user_id] = parsed
            print(f"[what-time] Logged guess from {user_id}: {minutes_to_str(parsed)}")
        if len(todays_guesses) >= 3:
            what_time_responded = True
            post_katherine_time_guess(client)


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Katherine bot is online! 👶 Keeping it classy while she's out.")
    reset_daily_state()
    _shuffle_deck()  # pre-load deck at a random offset on startup

    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
