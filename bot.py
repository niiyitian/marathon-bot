"""
BYD Marathon 2026 Training Tracker Bot
t.me/YitianMarathonBot
"""

import json
import os
import re
import logging
import base64
import httpx
import pytz
from datetime import datetime, date
from pathlib import Path
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes
)

import food

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GMAPS_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
DATA_DIR = Path("/app/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATA_FILE = DATA_DIR / "data.json"
CHAT_ID_FILE = DATA_DIR / "chat_id.txt"

# ── Conversation states ──────────────────────────────────────────────
EDIT_CHOOSE_SESSION, EDIT_ENTER_TEXT, EDIT_ENTER_NOTES = range(3)
LOG_CHOOSE_SESSION, LOG_UPLOAD = range(10, 12)

# ── Training plan (parsed from .ics) ────────────────────────────────
TRAINING_PLAN = [
  # (uid, date_str YYYY-MM-DD, summary, description, is_editable_thu, is_editable_tue)
  # W01
  ("w01_tue", "2026-06-23", "W01 · [Tempo] Run club tempo — 70min easy @ 7:30/km", "Resist the club pace. Run your own easy effort. HR under 145.", False, True),
  ("w01_thu", "2026-06-25", "W01 · [Track] Track club — 8×1km", "Target 5:40–5:50/km per rep. Full recovery between reps.", True, False),
  ("w01_sat", "2026-06-27", "W01 · [Long run] Long run 12km @ 7:30–7:45/km", "Fix easy pace week. Slow and controlled.", False, False),
  # W02
  ("w02_tue", "2026-06-30", "W02 · [Tempo] Run club tempo — 70min easy @ 7:30/km", "Still easy week — treat club session as an easy run.", False, True),
  ("w02_thu", "2026-07-02", "W02 · [Track] Track club — 12×300m", "Target 1:45–1:50 per rep (5:50/km effort).", True, False),
  ("w02_sat", "2026-07-04", "W02 · [Long run] Long run 14km @ 7:30/km", "Keep conversational effort throughout.", False, False),
  # W03
  ("w03_tue", "2026-07-07", "W03 · [Tempo] Run club — 3×20min tempo @ 6:50/km, 2min jog rest", "Participate fully this week. Good quality session.", False, True),
  ("w03_wed", "2026-07-08", "W03 · [Easy] Easy 4km @ 7:45/km", "Recovery from Tuesday tempo.", False, False),
  ("w03_thu", "2026-07-09", "W03 · [Track] Track club — 2×2km + 6×200m", "2km reps @ 6:00/km. 200m @ 5:30/km.", True, False),
  ("w03_sat", "2026-07-11", "W03 · [Long run] Long run 16km — last 4km @ 7:05/km", "First 12km @ 7:30, then last 4km at marathon pace. Finish strong.", False, False),
  # W04
  ("w04_tue", "2026-07-14", "W04 · [Easy] Easy run 6km @ 7:45/km", "Skip club tempo this week — recovery week.", False, True),
  ("w04_thu", "2026-07-16", "W04 · [Track] Track club — 6×300m only (half volume)", "Recovery week. Tell yourself it's intentional.", True, False),
  ("w04_sat", "2026-07-18", "W04 · [Long run] Easy long run 12km @ 7:30–8:00/km", "Recovery week. No pressure on pace.", False, False),
  # W05
  ("w05_tue", "2026-07-21", "W05 · [Tempo] Run club — 70min easy + 2×10min @ 6:50/km", "Modified — don't do the full 3×20. Big long run Saturday.", False, True),
  ("w05_wed", "2026-07-22", "W05 · [Easy] Easy 6km @ 7:45/km", "", False, False),
  ("w05_thu", "2026-07-23", "W05 · [Track] Track club — 8×1km @ 5:40/km", "", True, False),
  ("w05_sat", "2026-07-25", "W05 · [Long run] Long run 18km @ 7:30/km", "Carry water. Plan a looped route. New distance milestone.", False, False),
  # W06
  ("w06_tue", "2026-07-28", "W06 · [Tempo] Run club — full session: 70min + 3×20min @ 6:50/km", "Good quality week. Recover well after.", False, True),
  ("w06_wed", "2026-07-29", "W06 · [Easy] Easy 6km @ 7:45/km", "", False, False),
  ("w06_thu", "2026-07-30", "W06 · [Track] Track club — 12×300m @ 5:45/km", "", True, False),
  ("w06_sat", "2026-08-01", "W06 · [Long run] Long run 20km @ 7:30–7:45/km", "MILESTONE: first 20km. Bring a gel — take at 60min. Walk 1 min every 5km if needed.", False, False),
  ("w06_sun", "2026-08-02", "W06 · [Easy] Easy recovery 5km @ 8:00–8:15/km", "Extra aerobic volume — building your base. Very easy, skip if legs are trashed from yesterday.", False, False),
  # W07
  ("w07_tue", "2026-08-04", "W07 · [Tempo] Run club — full tempo session", "", False, True),
  ("w07_wed", "2026-08-05", "W07 · [MP run] Marathon pace run: 10km @ 7:00–7:05/km", "Replaces easy mid-week run. Controlled effort — not racing.", False, False),
  ("w07_thu", "2026-08-06", "W07 · [Track] Track club — 2×2km + 6×200m", "2km @ 6:00/km. Feel the pace difference from Wednesday.", True, False),
  ("w07_sat", "2026-08-08", "W07 · [Long run] Long run 22km @ 7:30/km", "Fuel at 45min and 90min. Practice your race-day gel routine.", False, False),
  ("w07_sun", "2026-08-09", "W07 · [Easy] Easy recovery 5km @ 8:00–8:15/km", "Extra aerobic volume. Should feel almost too easy.", False, False),
  # W08
  ("w08_tue", "2026-08-11", "W08 · [Tempo] Run club — 3×20min tempo @ 6:50/km", "", False, True),
  ("w08_wed", "2026-08-12", "W08 · [MP run] Marathon pace run: 12km @ 7:00/km", "", False, False),
  ("w08_thu", "2026-08-13", "W08 · [Track] Track club — 8×1km @ 5:40/km", "", True, False),
  ("w08_sat", "2026-08-15", "W08 · [Long run] Long run 24km @ 7:20–7:30/km", "Last 4km push to 7:00/km if feeling good.", False, False),
  ("w08_sun", "2026-08-16", "W08 · [Easy] Easy recovery 6km @ 8:00–8:15/km", "Extra aerobic volume, very easy pace.", False, False),
  # W09
  ("w09_tue", "2026-08-18", "W09 · [Easy] Easy run with club — 50min @ 7:30/km only", "Skip or shorten the tempo portion.", False, True),
  ("w09_wed", "2026-08-19", "W09 · [Easy] Easy 5km @ 7:45/km", "", False, False),
  ("w09_thu", "2026-08-20", "W09 · [Track] Track club — 6×300m only (half volume)", "Recovery week.", True, False),
  ("w09_sat", "2026-08-22", "W09 · [Long run] Easy 16km @ 7:45/km", "Recovery week long run. No pace pressure.", False, False),
  # W10
  ("w10_tue", "2026-08-25", "W10 · [Tempo] Run club — full tempo: 3×20min @ 6:50/km", "", False, True),
  ("w10_wed", "2026-08-26", "W10 · [MP run] Marathon pace run: 12km @ 7:00/km", "", False, False),
  ("w10_thu", "2026-08-27", "W10 · [Track] Track club — 8×1km @ 5:40/km", "", True, False),
  ("w10_sat", "2026-08-29", "W10 · [Long run] Long run 26km @ 7:30/km", "Gel at 45, 90, 135min. Aim for negative split — 2nd half slightly faster.", False, False),
  ("w10_sun", "2026-08-30", "W10 · [Easy] Easy recovery 6km @ 8:00–8:15/km", "Extra aerobic volume after yesterday's 26km.", False, False),
  # W11
  ("w11_tue", "2026-09-01", "W11 · [Tempo] Run club — 70min + 2×20min @ 6:50/km", "Slightly reduced — big long run coming Saturday.", False, True),
  ("w11_wed", "2026-09-02", "W11 · [MP run] Marathon pace: 10km @ 7:00/km", "", False, False),
  ("w11_thu", "2026-09-03", "W11 · [Track] Track club — 12×300m @ 5:45/km", "", True, False),
  ("w11_sat", "2026-09-05", "W11 · [Long run] Long run 28km @ 7:30/km", "Confidence builder. Run your own pace. Gel every 45min.", False, False),
  ("w11_sun", "2026-09-06", "W11 · [Easy] Easy recovery 6km @ 8:00–8:15/km", "Extra aerobic volume after yesterday's 28km.", False, False),
  # W12
  ("w12_tue", "2026-09-08", "W12 · [Tempo] Run club — 70min + 1×20min tempo @ 6:50/km", "Reduce to 1 tempo rep only.", False, True),
  ("w12_wed", "2026-09-09", "W12 · [Easy] Easy 6km @ 7:30/km", "", False, False),
  ("w12_thu", "2026-09-10", "W12 · [Track] Track club — 6×1km @ 5:40/km (half volume)", "", True, False),
  ("w12_sat", "2026-09-12", "W12 · [Long run] Easy 16km @ 7:30/km", "", False, False),
  # W13
  ("w13_tue", "2026-09-15", "W13 · [Easy] Easy 6km @ 7:30/km — skip club tempo", "", False, True),
  ("w13_thu", "2026-09-17", "W13 · [Track] Track club — 4×300m + 4×200m strides only", "", True, False),
  ("w13_fri", "2026-09-19", "W13 · [Easy] Easy shakeout 4km @ 7:30/km + 4 strides", "", False, False),
  # W14
  ("w14_tue", "2026-09-22", "W14 · [Easy] Easy 4km shakeout @ 7:30/km", "", False, False),
  ("w14_sat", "2026-09-26", "W14 · [🏁 RACE] RACE: Sep 27 Half Marathon", "Target sub-2:15. Start conservative @ 6:25/km. Negative split. This is your fitness test for Dec.", False, False),
  # W15
  ("w15_tue", "2026-09-29", "W15 · [Easy] Easy 5km @ 7:45–8:00/km — skip club tempo", "", False, True),
  ("w15_thu", "2026-10-01", "W15 · [Easy] Easy 5km @ 7:45/km — skip track", "", False, False),
  ("w15_sat", "2026-10-03", "W15 · [Easy] Easy 10km @ 7:45/km", "Full recovery week post HM.", False, False),
  # W16
  ("w16_tue", "2026-10-06", "W16 · [Tempo] Run club — full tempo: 3×20min @ 6:50/km", "", False, True),
  ("w16_wed", "2026-10-07", "W16 · [MP run] Marathon pace: 12km @ 7:00/km", "", False, False),
  ("w16_thu", "2026-10-08", "W16 · [Track] Track club — 8×1km @ 5:40/km", "", True, False),
  ("w16_sat", "2026-10-10", "W16 · [Long run] Long run 29km @ 7:20/km", "Race-day shoes and gels. Simulate Dec 6 exactly.", False, False),
  ("w16_sun", "2026-10-11", "W16 · [Easy] Easy recovery 6km @ 8:00–8:15/km", "Extra aerobic volume — building toward peak weeks.", False, False),
  # W17
  ("w17_tue", "2026-10-13", "W17 · [Tempo] Run club — 3×20min @ 6:50/km", "", False, True),
  ("w17_wed", "2026-10-14", "W17 · [MP run] Marathon pace: 14km @ 7:00/km", "Longest MP run in the plan. Controlled effort.", False, False),
  ("w17_thu", "2026-10-15", "W17 · [Track] Track club — 2×2km + 6×200m", "", True, False),
  ("w17_sat", "2026-10-17", "W17 · [Long run] Long run 32km @ 7:15–7:20/km", "Race-day kit. Gel at 45, 90, 135min. Last 5km @ 7:00/km.", False, False),
  ("w17_sun", "2026-10-18", "W17 · [Easy] Easy recovery 6km @ 8:00–8:15/km", "Extra aerobic volume — nearly at peak week.", False, False),
  # W18
  ("w18_tue", "2026-10-20", "W18 · [Tempo] Run club — 2×20min @ 6:50/km only", "Reduce to 2 reps — big 32km coming Saturday.", False, True),
  ("w18_wed", "2026-10-21", "W18 · [MP run] Marathon pace: 10km @ 7:00/km", "", False, False),
  ("w18_thu", "2026-10-22", "W18 · [Track] Track club — 8×1km @ 5:40/km", "", True, False),
  ("w18_sat", "2026-10-24", "W18 · [Long run] Long run 34km @ 7:20/km", "PEAK RUN. Gel at 45, 90, 135, 165min. Run through fatigue. You earned this.", False, False),
  ("w18_sun", "2026-10-25", "W18 · [Easy] Easy recovery 6km @ 8:00–8:15/km", "Peak week volume. Very easy — this is about durability, not speed.", False, False),
  # W19
  ("w19_tue", "2026-10-27", "W19 · [Easy] Easy 5km @ 7:45/km — skip club", "", False, False),
  ("w19_thu", "2026-10-29", "W19 · [Easy] Easy 5km + strides — skip track", "", False, False),
  ("w19_sat", "2026-10-31", "W19 · [🏁 RACE] RACE: Nov 1 Half Marathon (marathon effort)", "Do NOT race this. Run @ 7:00–7:05/km marathon effort. Target ~2:28. This is a training run with a bib.", False, False),
  # W20
  ("w20_tue", "2026-11-03", "W20 · [Easy] Easy 5km @ 7:45/km — skip club tempo", "", False, False),
  ("w20_wed", "2026-11-04", "W20 · [Easy] Easy 6km @ 7:45/km", "", False, False),
  ("w20_thu", "2026-11-05", "W20 · [Easy] Easy 5km — skip track or do 4×300m easy", "", False, False),
  ("w20_sat", "2026-11-07", "W20 · [Easy] Easy 14km @ 7:45/km", "", False, False),
  # W21
  ("w21_tue", "2026-11-10", "W21 · [Tempo] Run club — 2×20min @ 6:50/km only", "Last quality session with the club.", False, True),
  ("w21_wed", "2026-11-11", "W21 · [MP run] Marathon pace: 10km @ 7:00/km", "Last MP run of the plan. Should feel controlled and comfortable.", False, False),
  ("w21_thu", "2026-11-12", "W21 · [Track] Track club — 6×1km @ 5:40/km", "Last track session. Controlled — not a time trial.", True, False),
  ("w21_sat", "2026-11-14", "W21 · [Long run] Last long run: 22km @ 7:20/km", "", False, False),
  # W22
  ("w22_tue", "2026-11-17", "W22 · [Tempo] Run club — 70min easy + 1×15min @ 6:50/km", "Much reduced. Easy effort overall.", False, True),
  ("w22_wed", "2026-11-18", "W22 · [Easy] Easy 6km @ 7:30/km", "", False, False),
  ("w22_thu", "2026-11-19", "W22 · [Track] Track club — 4×1km @ 5:40/km only", "", True, False),
  ("w22_sat", "2026-11-21", "W22 · [Easy] Easy 16km @ 7:30/km", "", False, False),
  ("w22_thu2", "2026-11-26", "W22 · [🏋️ HYROX] Hyrox Women Doubles", "⚠️ This is 10 days before race day. Treat it as a hard effort — don't leave everything on the floor. Prioritise recovery immediately after: protein, sleep, compression.", False, False),
  # W23
  ("w23_tue", "2026-11-24", "W23 · [Easy] Easy 6km @ 7:30/km — skip club", "", False, False),
  ("w23_wed", "2026-11-25", "W23 · [MP run] 4km @ 7:00/km + 4 strides", "Just to keep legs sharp. Not a workout.", False, False),
  ("w23_thu", "2026-11-26", "W23 · [Track] Track club — 4×200m strides only", "Show face, do very little. Save the legs.", True, False),
  ("w23_sat", "2026-11-28", "W23 · [Easy] Easy 10km @ 7:30/km", "", False, False),
  # W24
  ("w24_tue", "2026-12-01", "W24 · [Easy] Easy 3km shakeout @ 7:30/km", "", False, False),
  ("w24_fri", "2026-12-05", "W24 · [🏁 PACE] PACING DUTY: Dec 5 HM @ 7:50/km", "Walk uphills. Eat 2 gels. Refuel HUGE after finishing. Sleep by 9pm. Big day tomorrow.", False, False),
  ("w24_sun", "2026-12-06", "W24 · [🏁 RACE DAY] BYD Full Marathon — Target sub-4:45", "Start @ 6:55/km for first 10km. Settle into 6:45 from 10–30km. Hold on. You've done the work.", False, False),
]

# ── Data helpers ─────────────────────────────────────────────────────
def load_data() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return {"completed": {}, "edits": {}, "logs": {}}

def save_data(data: dict):
    DATA_FILE.write_text(json.dumps(data, indent=2))

def get_session(uid: str):
    for s in TRAINING_PLAN:
        if s[0] == uid:
            return s
    return None

def get_week_sessions(week: str):
    return [s for s in TRAINING_PLAN if s[0].startswith(week)]

def today_str():
    return date.today().isoformat()

def current_week_num():
    """Return which week we're in (1-24), or None if outside plan."""
    today = date.today()
    for i, s in enumerate(TRAINING_PLAN):
        s_date = date.fromisoformat(s[1])
        if s_date >= today:
            # Extract week number from uid like w01_xxx
            m = re.match(r"w(\d+)_", s[0])
            return int(m.group(1)) if m else 1
    return 24

def session_display(s, data: dict) -> str:
    uid, dt, summary, desc, is_thu, is_tue = s
    overrides = data.get("edits", {})
    raw_summary = overrides.get(uid, {}).get("summary", summary)
    display_desc = overrides.get(uid, {}).get("desc", desc)
    done = uid in data.get("completed", {})
    has_log = uid in data.get("logs", {})

    # Strip [Tag] from display only if not a custom edit
    if uid not in overrides:
        display_summary = re.sub(r'\s*\[.*?\]\s*', ' ', raw_summary).strip()
    else:
        display_summary = raw_summary

    status = "✅" if done else "⬜"
    log_icon = " 📎" if has_log else ""
    edit_icon = " ✏️" if uid in overrides else ""

    text = f"{status} {display_summary}{edit_icon}{log_icon}\n"
    if display_desc:
        text += f"   _{display_desc}_\n"
    text += f"   📅 {dt}\n"
    return text

# ── Main menu keyboard ───────────────────────────────────────────────
def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["📅 This Week", "📋 Full Plan"],
            ["✅ Log Complete", "📷 Upload Activity"],
            ["✏️ Edit Thursday", "✏️ Edit Tuesday"],
            ["📊 My Progress", "🔮 Predictions"],
            ["📋 Week Summary", "👟 Gear Tracker"],
            ["🏁 Race Debrief", "🗓 Race Manager"],
            ["📈 Mileage", "💬 Ask Coach"],
            ["🗺 Route Planner", "🆘 Help"],
            ["🍽️ Log Food", "📊 Nutrition"],
            ["🏋️ Extra Exercise", "⚖️ My Profile"],
        ],
        resize_keyboard=True
    )

# ── /start ───────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    save_chat_id(update.effective_chat.id)
    await update.message.reply_text(
        "👟 *BYD Marathon 2026 Training Bot*\n\n"
        "I'll help you track your 24-week plan to sub-4:45 on Dec 6 🏅\n\n"
        "Use the menu below to navigate. Good luck with the training!",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

# ── This week ────────────────────────────────────────────────────────
async def show_this_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    wnum = current_week_num()
    week_key = f"w{wnum:02d}"
    sessions = get_week_sessions(week_key)

    if not sessions:
        await update.message.reply_text("You're outside the plan window. Race day was Dec 6 🎉")
        return

    text = f"*Week {wnum} — Your Sessions*\n\n"
    for s in sessions:
        text += session_display(s, data) + "\n"

    # Quick-complete buttons
    incomplete = [s for s in sessions if s[0] not in data.get("completed", {})]
    buttons = []
    for s in incomplete:
        uid = s[0]
        label = s[2][:35] + "…" if len(s[2]) > 35 else s[2]
        buttons.append([InlineKeyboardButton(f"✅ {label}", callback_data=f"done|{uid}")])

    kb = InlineKeyboardMarkup(buttons) if buttons else None
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)

# ── Full plan ────────────────────────────────────────────────────────
async def show_full_plan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    total = len(TRAINING_PLAN)
    done_count = len(data.get("completed", {}))

    # Week navigation buttons
    buttons = []
    row = []
    for w in range(1, 25):
        wk = f"W{w:02d}"
        row.append(InlineKeyboardButton(wk, callback_data=f"week|{w:02d}"))
        if len(row) == 6:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    kb = InlineKeyboardMarkup(buttons)
    await update.message.reply_text(
        f"*Full Training Plan*\n"
        f"Progress: {done_count}/{total} sessions ({'%.0f' % (done_count/total*100)}%)\n\n"
        f"Tap a week to view its sessions:",
        parse_mode="Markdown",
        reply_markup=kb
    )

async def cb_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, wnum_str = query.data.split("|")
    week_key = f"w{wnum_str}"
    sessions = get_week_sessions(week_key)
    data = load_data()

    if not sessions:
        await query.edit_message_text("No sessions for that week.")
        return

    text = f"*Week {int(wnum_str)} Sessions*\n\n"
    for s in sessions:
        text += session_display(s, data) + "\n"

    buttons = []
    for s in sessions:
        uid = s[0]
        done = uid in data.get("completed", {})
        label = ("✅ " if done else "⬜ ") + (s[2][:30] + "…" if len(s[2]) > 30 else s[2])
        buttons.append([InlineKeyboardButton(label, callback_data=f"toggle|{uid}")])
    buttons.append([InlineKeyboardButton("« Back to weeks", callback_data="back_weeks")])

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def cb_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, uid = query.data.split("|")
    data = load_data()
    completed = data.setdefault("completed", {})

    if uid in completed:
        del completed[uid]
        msg = "↩️ Unmarked as complete."
    else:
        completed[uid] = today_str()
        msg = "✅ Marked as complete!"

    save_data(data)
    await query.answer(msg, show_alert=False)

    # Refresh the week view
    m = re.match(r"(w\d+)_", uid)
    if m:
        wnum_str = m.group(1)[1:]
        week_key = f"w{wnum_str}"
        sessions = get_week_sessions(week_key)
        text = f"*Week {int(wnum_str)} Sessions*\n\n"
        for s in sessions:
            text += session_display(s, data) + "\n"
        buttons = []
        for s in sessions:
            sid = s[0]
            done = sid in data.get("completed", {})
            label = ("✅ " if done else "⬜ ") + (s[2][:30] + "…" if len(s[2]) > 30 else s[2])
            buttons.append([InlineKeyboardButton(label, callback_data=f"toggle|{sid}")])
        buttons.append([InlineKeyboardButton("« Back to weeks", callback_data="back_weeks")])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def cb_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Quick-complete from this-week view."""
    query = update.callback_query
    await query.answer()
    _, uid = query.data.split("|")
    data = load_data()
    data.setdefault("completed", {})[uid] = today_str()
    save_data(data)
    await query.answer("✅ Marked complete!", show_alert=False)
    # Just refresh this week
    wnum = current_week_num()
    week_key = f"w{wnum:02d}"
    sessions = get_week_sessions(week_key)
    text = f"*Week {wnum} — Your Sessions*\n\n"
    for s in sessions:
        text += session_display(s, data) + "\n"
    incomplete = [s for s in sessions if s[0] not in data.get("completed", {})]
    buttons = []
    for s in incomplete:
        sid = s[0]
        label = s[2][:35] + "…" if len(s[2]) > 35 else s[2]
        buttons.append([InlineKeyboardButton(f"✅ {label}", callback_data=f"done|{sid}")])
    kb = InlineKeyboardMarkup(buttons) if buttons else None
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)

async def cb_back_weeks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = load_data()
    total = len(TRAINING_PLAN)
    done_count = len(data.get("completed", {}))
    buttons = []
    row = []
    for w in range(1, 25):
        wk = f"W{w:02d}"
        row.append(InlineKeyboardButton(wk, callback_data=f"week|{w:02d}"))
        if len(row) == 6:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    kb = InlineKeyboardMarkup(buttons)
    await query.edit_message_text(
        f"*Full Training Plan*\nProgress: {done_count}/{total} sessions\n\nTap a week to view:",
        parse_mode="Markdown", reply_markup=kb
    )

# ── Log complete (with note) ─────────────────────────────────────────
async def log_complete_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    wnum = current_week_num()
    week_key = f"w{wnum:02d}"
    sessions = get_week_sessions(week_key)
    incomplete = [s for s in sessions if s[0] not in data.get("completed", {})]

    if not incomplete:
        await update.message.reply_text("🎉 All sessions this week are done! Check next week with /plan.")
        return

    buttons = []
    for s in incomplete:
        label = s[2][:40] + "…" if len(s[2]) > 40 else s[2]
        buttons.append([InlineKeyboardButton(label, callback_data=f"markdone|{s[0]}")])

    await update.message.reply_text(
        "Which session did you complete?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def cb_markdone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, uid = query.data.split("|")
    data = load_data()
    data.setdefault("completed", {})[uid] = today_str()
    save_data(data)
    s = get_session(uid)
    name = s[2] if s else uid
    await query.edit_message_text(
        f"✅ *{name}*\n\nMarked complete on {today_str()} 🎉\n\nUse *Upload Activity* to attach a Strava screenshot.",
        parse_mode="Markdown"
    )

# ── Upload activity ──────────────────────────────────────────────────
async def upload_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    wnum = current_week_num()
    week_key = f"w{wnum:02d}"
    sessions = get_week_sessions(week_key)

    buttons = []
    for s in sessions:
        uid = s[0]
        done_marker = "✅ " if uid in data.get("completed", {}) else ""
        has_log = "📎 " if uid in data.get("logs", {}) else ""
        display = data.get("edits", {}).get(uid, {}).get("summary", s[2])
        label = done_marker + has_log + (display[:38] + "…" if len(display) > 38 else display)
        buttons.append([InlineKeyboardButton(label, callback_data=f"upload_session|{uid}")])

    # Extra activity option
    buttons.append([InlineKeyboardButton("➕ Extra activity (not in plan)", callback_data="upload_session|extra")])

    await update.message.reply_text(
        "📷 Which session do you want to upload an activity log for?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def cb_upload_session(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, uid = query.data.split("|")
    ctx.user_data["upload_uid"] = uid
    data = load_data()
    if uid == "extra":
        await query.edit_message_text(
            "📷 Send your Strava or Garmin screenshot for the extra activity.\n\nAdd a caption describing what it was (optional) 👇",
            parse_mode="Markdown"
        )
    else:
        s = get_session(uid)
        name = data.get("edits", {}).get(uid, {}).get("summary", s[2] if s else uid)
        await query.edit_message_text(
            f"📷 Send your Strava screenshot or Garmin activity image for:\n\n*{name}*\n\nJust send the photo now 👇",
            parse_mode="Markdown"
        )

async def analyse_with_claude(image_bytes: bytes, session_summary: str, session_desc: str, recovery: str = "") -> str:
    """Send screenshot to Claude and get run analysis."""
    if not ANTHROPIC_KEY:
        return ""
    b64 = base64.standard_b64encode(image_bytes).decode()

    recovery_context = ""
    if recovery == "good":
        recovery_context = "The athlete reported good recovery this morning (sleep score ≥72 or HRV ≥60ms). "
    elif recovery == "avg":
        recovery_context = "The athlete reported average recovery this morning (sleep score 58–71 or HRV 52–59ms). "
    elif recovery == "poor":
        recovery_context = "The athlete reported poor recovery this morning (sleep score <58 or HRV <52ms / unbalanced) — factor this into your assessment. "

    prompt = (
        f"You are an experienced marathon running coach — direct, knowledgeable, and encouraging but honest. "
        f"The athlete trains in Singapore (year-round heat 28–34°C, humidity 70–90%). "
        f"Heart rates in Singapore run 5–10 bpm higher than cool conditions — account for this. "
        f"Athlete max HR is {MAX_HR}bpm. Use these HR zones for RPE:\n"
        f"Z1 <129bpm (RPE 3–4), Z2 129–147 (RPE 5–6), Z3 148–165 (RPE 7), Z4 166–175 (RPE 8–9), Z5 >175 (RPE 10)\n"
        f"{recovery_context}"
        f"Planned session: {session_summary}. Coach notes: {session_desc}\n\n"
        f"Analyse this activity screenshot and respond in exactly this format:\n\n"
        f"Rating: X/10\n\n"
        f"RPE: X/10 (Z[zone] — [e.g. 'avg HR 162 = Z4 threshold effort'])\n\n"
        f"On track: [one sentence comparing actual numbers to the plan target]\n\n"
        f"Well done: [one specific thing done well, with actual numbers]\n\n"
        f"Next time: [one concrete, actionable coaching cue]\n\n"
        f"Use actual numbers from the screenshot. Firm, specific, motivating. No emojis. No filler."
    )
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 400,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                            {"type": "text", "text": prompt}
                        ]
                    }]
                }
            )
        result = resp.json()
        return result["content"][0]["text"]
    except Exception as e:
        logger.error(f"Claude analysis failed: {e}")
        return ""


async def extract_distance_from_image(image_bytes: bytes) -> float:
    """Ask Claude to extract just the distance from the activity screenshot."""
    if not ANTHROPIC_KEY:
        return 0.0
    b64 = base64.standard_b64encode(image_bytes).decode()
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 50,
                    "messages": [{"role": "user", "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                        {"type": "text", "text": "What is the distance of this activity in km? Reply with just the number, e.g. 8.61. If not visible, reply 0."}
                    ]}]
                }
            )
        text = resp.json()["content"][0]["text"].strip()
        return float(re.search(r"[\d.]+", text).group())
    except Exception as e:
        logger.error(f"Distance extract failed: {e}")
        return 0.0


async def extract_activity_data(image_bytes: bytes) -> dict:
    """Extract distance, duration, avg pace, and avg HR from a Strava/Garmin
    screenshot in one call — feeds both mileage logging AND the race
    predictor (auto-PB detection + HR/pace trend). Returns {} on failure or
    if fields aren't visible in the screenshot (fields default to None)."""
    if not ANTHROPIC_KEY:
        return {}
    b64 = base64.standard_b64encode(image_bytes).decode()
    prompt = (
        "Extract activity data from this Strava/Garmin screenshot. Respond with "
        "ONLY a raw JSON object, no markdown fences, no preamble. Schema:\n"
        "{\n"
        '  "distance_km": <float or null if not visible>,\n'
        '  "duration_sec": <integer total seconds or null>,\n'
        '  "avg_pace_sec_per_km": <integer seconds/km or null>,\n'
        '  "avg_hr": <integer bpm or null if not visible>\n'
        "}\n"
        "If duration is shown but pace isn't (or vice versa), derive the missing "
        "one from distance. If a field genuinely isn't visible, use null — don't guess."
    )
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 150,
                    "messages": [{"role": "user", "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                        {"type": "text", "text": prompt}
                    ]}]
                }
            )
        text = resp.json()["content"][0]["text"].strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
        obj = json.loads(text)
        return {
            "distance_km": float(obj["distance_km"]) if obj.get("distance_km") is not None else None,
            "duration_sec": int(obj["duration_sec"]) if obj.get("duration_sec") is not None else None,
            "avg_pace_sec_per_km": int(obj["avg_pace_sec_per_km"]) if obj.get("avg_pace_sec_per_km") is not None else None,
            "avg_hr": int(obj["avg_hr"]) if obj.get("avg_hr") is not None else None,
        }
    except Exception as e:
        logger.error(f"Activity data extract failed: {e}")
        return {}


async def receive_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = ctx.user_data.get("upload_uid")
    if not uid:
        await update.message.reply_text(
            "First choose a session via *Upload Activity*, then send your photo.",
            parse_mode="Markdown"
        )
        return

    data = load_data()
    photo = update.message.photo[-1]
    file_id = photo.file_id
    caption = update.message.caption or ""

    # Generate unique key for extra activities
    if uid == "extra":
        extra_key = f"extra_{today_str()}_{len([k for k in data.get('logs', {}) if k.startswith('extra')])}"
        uid = extra_key
        ctx.user_data["upload_uid"] = uid
        name = caption if caption else "Extra activity"
        desc = ""
        auto_complete_msg = ""
    else:
        s = get_session(uid)
        name = data.get("edits", {}).get(uid, {}).get("summary", s[2] if s else uid)
        desc = data.get("edits", {}).get(uid, {}).get("desc", s[3] if s else "")
        if uid not in data.get("completed", {}):
            data.setdefault("completed", {})[uid] = today_str()
            auto_complete_msg = "\n✅ Also marked as complete!"
        else:
            auto_complete_msg = ""

    data.setdefault("logs", {})
    existing = data["logs"].get(uid, {})
    # Support multiple images — store as list
    file_ids = existing.get("file_ids", [])
    if existing.get("file_id") and existing["file_id"] not in file_ids:
        file_ids.append(existing["file_id"])  # migrate old single image
    file_ids.append(file_id)

    is_additional = uid in data["logs"]
    data["logs"][uid] = {
        "file_ids": file_ids,
        "file_id": file_id,  # keep for backward compat
        "date": today_str(),
        "caption": caption
    }
    save_data(data)
    ctx.user_data.pop("upload_uid", None)

    if is_additional:
        added_msg = f"📎 *Additional image added* ({len(file_ids)} total for this session)\n\n🔍 Analysing..."
    else:
        added_msg = f"📎 Activity log saved for:\n*{name}*{auto_complete_msg}\n\n🔍 Analysing your run..."

    await update.message.reply_text(
        added_msg,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📷 Add another image", callback_data=f"upload_session|{uid}"),
            InlineKeyboardButton("✅ Done", callback_data="upload_done")
        ]])
    )

    # Download photo bytes once — use for both analysis and distance extraction
    tg_file = await update.message.photo[-1].get_file()
    image_bytes = bytes(await tg_file.download_as_bytearray())

    # Run coach analysis
    if ANTHROPIC_KEY:
        try:
            recovery = data.get("sleep_log", {}).get(today_str(), "")
            analysis = await analyse_with_claude(image_bytes, name, desc, recovery)
            if analysis:
                await update.message.reply_text(
                    f"🤖 *Coach Analysis*\n\n{analysis}",
                    parse_mode="Markdown"
                )
        except Exception as e:
            logger.error(f"Photo analysis error: {e}")

        # Extract full activity data (distance/duration/pace/HR) — feeds mileage
        # log, auto-PB detection, and the HR/pace trend used by predictions
        if not is_additional:
            try:
                activity = await extract_activity_data(image_bytes)
                km = activity.get("distance_km") or 0.0
                if km > 0:
                    data = load_data()
                    log_mileage(data, current_week_num(), km)

                    # Store the structured extraction on the log entry for reference
                    data["logs"].setdefault(uid, {})["activity_data"] = activity

                    pb_msg = ""
                    duration_sec = activity.get("duration_sec")
                    if duration_sec:
                        new_pb_key = _maybe_record_pb(data, km, duration_sec)
                        if new_pb_key:
                            new_time = get_best_efforts(data)[new_pb_key]
                            pb_msg = f"\n\n🎉 *New {new_pb_key} PB: {new_time}!* Predictions updated."

                    pace_sec_per_km = activity.get("avg_pace_sec_per_km")
                    avg_hr = activity.get("avg_hr")
                    if pace_sec_per_km and avg_hr:
                        _maybe_record_hr_point(data, name, pace_sec_per_km, avg_hr)

                    save_data(data)

                    ctx.user_data["auto_gear_km"] = km
                    shoes = get_shoes(data)
                    buttons = []
                    for sid, shoe in shoes.items():
                        buttons.append([InlineKeyboardButton(
                            f"{shoe['name']} ({shoe['km']:.0f}km)",
                            callback_data=f"autogear|{sid}|{km}"
                        )])
                    buttons.append([InlineKeyboardButton("⬜ Skip gear tracking", callback_data="autogear|skip|0")])
                    await update.message.reply_text(
                        f"📊 *{km:.1f}km logged to this week's mileage.*{pb_msg}\n\n👟 *Which shoes did you wear?*",
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(buttons)
                    )
            except Exception as e:
                logger.error(f"Gear auto-log error: {e}")
    else:
        await update.message.reply_text(
            "💡 _Tip: Set ANTHROPIC\\_API\\_KEY when starting the bot to get AI run analysis!_",
            parse_mode="Markdown"
        )


async def cb_upload_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✅ All images saved!", reply_markup=None)


async def cb_autogear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("|")
    sid = parts[1]
    km = float(parts[2])

    if sid == "skip":
        await query.edit_message_text("Gear tracking skipped.")
        return

    data = load_data()
    shoes = get_shoes(data)
    if sid in shoes:
        shoes[sid]["km"] = round(shoes[sid]["km"] + km, 1)
        save_data(data)
        shoe = shoes[sid]
        pct = shoe["km"] / shoe["limit"] * 100
        warn = "\n⚠️ Over 80% — consider retiring soon." if pct >= 80 else ""
        await query.edit_message_text(
            f"👟 Added {km:.1f}km to *{shoe['name']}*\nTotal: {shoe['km']:.0f}/{shoe['limit']}km ({pct:.0f}%){warn}",
            parse_mode="Markdown"
        )

# ── Edit Thursday / Tuesday ──────────────────────────────────────────
async def edit_thu_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _edit_start(update, ctx, is_thu=True)

async def edit_tue_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _edit_start(update, ctx, is_thu=False)

async def _edit_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE, is_thu: bool):
    data = load_data()
    day_name = "Thursday Track" if is_thu else "Tuesday Run Club"
    ctx.user_data["edit_is_thu"] = is_thu
    today = date.today()

    # Show only upcoming sessions of the right type (past ones just clutter the list)
    editable = []
    for s in TRAINING_PLAN:
        uid, dt, summary, desc, thu_flag, tue_flag = s
        if date.fromisoformat(dt) < today:
            continue
        if (is_thu and thu_flag) or (not is_thu and tue_flag):
            editable.append(s)

    if not editable:
        await update.message.reply_text(f"No upcoming {day_name} sessions found.")
        return

    buttons = []
    for s in editable:
        uid, dt, summary, _, _, _ = s
        override = data.get("edits", {}).get(uid, {})
        display = override.get("summary", summary)
        label = f"{dt}: {display[:36]}…" if len(display) > 36 else f"{dt}: {display}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"edit_pick|{uid}")])

    await update.message.reply_text(
        f"✏️ *Edit {day_name} Sessions*\n\nUpcoming sessions only — past ones aren't editable here.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def cb_edit_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, uid = query.data.split("|")
    ctx.user_data["edit_uid"] = uid
    data = load_data()
    s = get_session(uid)
    current_title = data.get("edits", {}).get(uid, {}).get("summary", s[2] if s else "")
    current_notes = data.get("edits", {}).get(uid, {}).get("desc", s[3] if s else "")

    await query.edit_message_text(
        f"✏️ Editing: *{s[2] if s else uid}*\n\n"
        f"Current title:\n`{current_title}`\n\n"
        f"Send the new session title (e.g. `7×800m @ 5:50/km`):\n\n"
        f"Or send /cancel to abort.",
        parse_mode="Markdown"
    )
    return EDIT_ENTER_TEXT

async def edit_receive_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = ctx.user_data.get("edit_uid")
    if not uid:
        await update.message.reply_text("Something went wrong. Please try again.")
        return ConversationHandler.END

    ctx.user_data["edit_new_title"] = update.message.text.strip()
    data = load_data()
    s = get_session(uid)
    current_notes = data.get("edits", {}).get(uid, {}).get("desc", s[3] if s else "")

    await update.message.reply_text(
        f"Now send the coach notes / description for this session.\n\n"
        f"Current: _{current_notes if current_notes else 'none'}_\n\n"
        f"Send `-` to keep existing notes, or type new ones.",
        parse_mode="Markdown"
    )
    return EDIT_ENTER_NOTES

async def edit_receive_notes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = ctx.user_data.get("edit_uid")
    new_title = ctx.user_data.get("edit_new_title", "")
    new_notes = update.message.text.strip()

    data = load_data()
    s = get_session(uid)
    data.setdefault("edits", {}).setdefault(uid, {})["summary"] = new_title

    if new_notes != "-":
        data["edits"][uid]["desc"] = new_notes
    else:
        # Keep existing
        existing = data["edits"][uid].get("desc", s[3] if s else "")
        data["edits"][uid]["desc"] = existing

    save_data(data)

    await update.message.reply_text(
        f"✅ Updated!\n\n"
        f"*Title:* {new_title}\n"
        f"*Notes:* _{data['edits'][uid].get('desc', '') or 'none'}_",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )
    ctx.user_data.pop("edit_uid", None)
    ctx.user_data.pop("edit_new_title", None)
    return ConversationHandler.END

async def edit_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Edit cancelled.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END

# ── Race time predictor ───────────────────────────────────────────────
# Seed/fallback best efforts (as of Jun 2026) — auto-updated from logged
# activity photos via _maybe_record_pb() below, stored in data["best_efforts_auto"].
BEST_EFFORTS = {
    "400m": "1:48",
    "1K": "5:20",
    "5K": "30:23",
    "10K": "1:05:48",
    "HM": "2:27:45",
}

MAX_HR = 184  # Yitian's max HR

# Distance bands for auto-detecting which race distance a logged run matches
_PB_DISTANCE_BANDS = {
    "1K": (0.9, 1.1), "5K": (4.7, 5.3), "10K": (9.5, 10.5), "HM": (20.5, 21.5),
}
# Session tags treated as "quality" efforts worth using for HR/pace trend —
# easy/long runs are deliberately excluded since they're not run near threshold
_QUALITY_TAGS = ("TEMPO", "TRACK", "MP RUN")
_HR_POINTS_MAX = 15  # keep only the most recent — old fitness data goes stale


def get_best_efforts(data: dict) -> dict:
    """Static seed values, overridden per-distance by any faster auto-detected
    PB logged from an activity screenshot."""
    auto = data.get("best_efforts_auto", {})
    out = dict(BEST_EFFORTS)
    for k, v in auto.items():
        if k not in out or time_to_sec(v) < time_to_sec(out[k]):
            out[k] = v
    return out


def _maybe_record_pb(data: dict, distance_km: float, duration_sec: float) -> str | None:
    """If this run's distance matches a standard race band and beats the
    current best (seed or auto), record it. Returns the distance key if a
    new PB was set, else None."""
    if not distance_km or not duration_sec:
        return None
    for key, (lo, hi) in _PB_DISTANCE_BANDS.items():
        if lo <= distance_km <= hi:
            # Scale to the exact standard distance so a 10.3km run compares
            # fairly against a 10K PB, rather than penalizing/flattering it.
            standard_km = {"1K": 1.0, "5K": 5.0, "10K": 10.0, "HM": 21.1}[key]
            scaled_sec = duration_sec * (standard_km / distance_km)
            current = get_best_efforts(data).get(key)
            if current is None or scaled_sec < time_to_sec(current):
                data.setdefault("best_efforts_auto", {})[key] = sec_to_hms(scaled_sec)
                return key
    return None


def _classify_session_tag(summary: str) -> str:
    tag_match = re.search(r'\[(.*?)\]', summary)
    if tag_match:
        return tag_match.group(1).upper()
    upper = summary.upper()
    for candidate in _QUALITY_TAGS:
        if candidate in upper:
            return candidate
    return ""


def _maybe_record_hr_point(data: dict, session_summary: str, pace_sec_per_km: float, avg_hr: float):
    """Record an (HR, pace) point from a quality session for threshold-pace
    estimation. Skips easy/long runs — those aren't run near threshold, so
    including them would bias the trend toward slower paces."""
    if not pace_sec_per_km or not avg_hr:
        return
    tag = _classify_session_tag(session_summary)
    if tag not in _QUALITY_TAGS:
        return
    points = data.setdefault("hr_pace_points", [])
    points.append({"date": today_str(), "pace_sec_per_km": pace_sec_per_km, "avg_hr": avg_hr, "tag": tag})
    data["hr_pace_points"] = points[-_HR_POINTS_MAX:]


def estimate_threshold_pace_sec(data: dict) -> float | None:
    """Linear-fit pace (sec/km) vs avg HR from recent quality sessions, then
    extrapolate pace at 88% max HR (a standard lactate-threshold marker).
    Needs at least 4 points and a sane negative slope (pace should drop as
    HR rises) to trust the fit — otherwise returns None and predictions fall
    back to PB-only."""
    points = data.get("hr_pace_points", [])
    if len(points) < 4:
        return None
    hrs = [p["avg_hr"] for p in points]
    paces = [p["pace_sec_per_km"] for p in points]
    n = len(points)
    mean_hr = sum(hrs) / n
    mean_pace = sum(paces) / n
    denom = sum((h - mean_hr) ** 2 for h in hrs)
    if denom == 0:
        return None
    slope = sum((h - mean_hr) * (p - mean_pace) for h, p in zip(hrs, paces)) / denom
    if slope >= 0:  # noisy/insufficient data — pace should fall as HR rises
        return None
    intercept = mean_pace - slope * mean_hr
    threshold_pace = intercept + slope * (0.88 * MAX_HR)
    # Sanity guardrail: discard wildly implausible extrapolations
    if not (180 <= threshold_pace <= 720):  # 3:00/km to 12:00/km
        return None
    return threshold_pace


def time_to_sec(t: str) -> float:
    parts = t.split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])

def sec_to_hms(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(s % 60)
    if h > 0:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"

def predict_race_times(data: dict) -> tuple:
    """
    Estimate HM and FM finish times. Blends two independent signals when
    both are available:
    1. Riegel formula from best 10K PB (auto-updated from logged runs),
       plus a small bonus for weeks trained.
    2. HR/pace trend from recent quality sessions, extrapolated to an
       estimated threshold pace ≈ HM race pace for a trained recreational
       runner — this one updates without needing an all-out PB effort.
    Both adjusted for Singapore heat on race day.
    """
    completed = data.get("completed", {})
    weeks_trained = len(set(re.match(r"(w\d+)_", uid).group(1) for uid in completed if re.match(r"w\d+_", uid))) if completed else 0

    best_efforts = get_best_efforts(data)
    base_10k_sec = time_to_sec(best_efforts["10K"])

    # Training improvement: ~0.75 sec/km per completed week, capped at 20 weeks —
    # a modest supplementary bonus; the auto-updated PB and HR trend below are
    # the primary signals now.
    improvement_per_km = min(weeks_trained, 20) * 0.75
    adjusted_10k_sec = base_10k_sec - (improvement_per_km * 10)

    # Riegel: HM = 10K * (21.1/10)^1.06
    hm_sec_from_pb = adjusted_10k_sec * (21.1 / 10) ** 1.06

    threshold_pace = estimate_threshold_pace_sec(data)
    hr_trend_used = threshold_pace is not None
    if hr_trend_used:
        hm_sec_from_hr = threshold_pace * 21.1
        hm_sec = (hm_sec_from_pb + hm_sec_from_hr) / 2
    else:
        hm_sec = hm_sec_from_pb

    # Singapore heat penalty on race day: +5 sec/km = +105 sec for HM
    hm_sec_race = hm_sec + 105

    # FM via Riegel from adjusted HM
    fm_sec_race = hm_sec_race * (42.2 / 21.1) ** 1.06

    hm_h = int(hm_sec_race // 3600)
    hm_m = int((hm_sec_race % 3600) // 60)
    fm_h = int(fm_sec_race // 3600)
    fm_m = int((fm_sec_race % 3600) // 60)

    return (hm_h, hm_m), (fm_h, fm_m), weeks_trained, hr_trend_used, len(data.get("hr_pace_points", []))


async def show_predictions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    (hm_h, hm_m), (fm_h, fm_m), weeks, hr_trend_used, n_hr_points = predict_race_times(data)
    best_efforts = get_best_efforts(data)
    completed = data.get("completed", {})

    # Conflict warning
    conflict_warning = (
        "\n⚠️ *Schedule conflict detected:*\n"
        "Hyrox (Nov 26) → Pacing HM (Dec 5) → Full Marathon (Dec 6)\n"
        "Consider dropping the pacing duty to protect Dec 6.\n"
    )

    # How close to targets
    hm_target_min = 135  # 2:15 (Sep 27 half marathon target)
    hm_actual_min = hm_h * 60 + hm_m
    hm_gap = hm_actual_min - hm_target_min
    hm_gap_text = f"{abs(hm_gap)} min {'ahead of' if hm_gap < 0 else 'behind'} 2:15 target" if hm_gap != 0 else "exactly on 2:15 target"

    fm_target_min = 285  # 4:45 (Dec 6 full marathon target)
    fm_actual_min = fm_h * 60 + fm_m
    fm_gap = fm_actual_min - fm_target_min
    fm_gap_text = f"{abs(fm_gap)} min {'ahead of' if fm_gap < 0 else 'behind'} sub-4:45 target" if fm_gap != 0 else "exactly on sub-4:45 target"

    hr_note = (
        f"_(blended: 10K PB + HR/pace trend from {n_hr_points} quality sessions)_"
        if hr_trend_used else
        f"_(based on 10K PB only — log {max(0, 4 - n_hr_points)} more tempo/track/MP sessions with HR visible to unlock HR-trend blending)_"
    )

    text = (
        f"🔮 *Race Time Predictions*\n"
        f"_(10K PB {best_efforts['10K']}, {weeks} weeks trained, SG heat adjusted)_\n"
        f"{hr_note}\n\n"
        f"*Current PBs:*\n"
        f"5K: {best_efforts['5K']} · 10K: {best_efforts['10K']} · HM: {best_efforts['HM']}\n\n"
        f"🏃 *Half Marathon (21.1km)*\n"
        f"Predicted: *{hm_h}:{hm_m:02d}*\n"
        f"Sep 27 target: 2:15 → {hm_gap_text}\n\n"
        f"🏅 *Full Marathon (42.2km)*\n"
        f"Predicted: *{fm_h}:{fm_m:02d}*\n"
        f"Dec 6 target: sub-4:45 → {fm_gap_text}\n\n"
        f"*Nov 1 HM:* targeting 2:10 — needs strong Sep race + 5 more weeks\n"
        f"{conflict_warning}\n"
        f"_Predictions improve as more sessions are logged, PBs auto-update, "
        f"and HR data accumulates from tempo/track/MP sessions._"
    )

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_menu_keyboard())
async def show_progress(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    completed = data.get("completed", {})
    logs = data.get("logs", {})
    edits = data.get("edits", {})

    total = len(TRAINING_PLAN)
    done = len(completed)
    pct = done / total * 100

    # Bar chart
    filled = int(pct / 5)
    bar = "█" * filled + "░" * (20 - filled)

    # Count by type
    type_counts = {"Long run": 0, "Track": 0, "Tempo": 0, "Easy": 0, "MP run": 0, "RACE": 0, "Other": 0}
    for uid in completed:
        s = get_session(uid)
        if s:
            summary = s[2]
            matched = False
            for t in type_counts:
                if t.lower() in summary.lower():
                    type_counts[t] += 1
                    matched = True
                    break
            if not matched:
                type_counts["Other"] += 1

    upcoming_text = ""
    today = date.today()
    next_sessions = [s for s in TRAINING_PLAN if date.fromisoformat(s[1]) >= today and s[0] not in completed][:3]
    if next_sessions:
        upcoming_text = "\n*Next up:*\n"
        for s in next_sessions:
            upcoming_text += f"• {s[1]}: {s[2][:50]}\n"

    weeks_to_race = max(0, (date(2026, 12, 6) - date.today()).days // 7)

    text = (
        f"📊 *Your Training Progress*\n\n"
        f"`{bar}` {pct:.0f}%\n"
        f"{done}/{total} sessions complete\n\n"
        f"⏳ *{weeks_to_race} weeks* to race day (Dec 6)\n\n"
        f"*By type completed:*\n"
        f"🏃 Long runs: {type_counts['Long run']}\n"
        f"🏟 Track: {type_counts['Track']}\n"
        f"⚡ Tempo: {type_counts['Tempo']}\n"
        f"🐢 Easy: {type_counts['Easy']}\n"
        f"🎯 Marathon pace: {type_counts['MP run']}\n"
        f"🏁 Races: {type_counts['RACE']}\n\n"
        f"📎 Activity logs uploaded: {len(logs)}\n"
        f"✏️ Custom edits made: {len(edits)}\n"
        f"{upcoming_text}"
    )

    await update.message.reply_text(text, parse_mode="Markdown")


async def show_weekly_summary(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    wnum = current_week_num()
    week_key = f"w{wnum:02d}"
    sessions = get_week_sessions(week_key)
    completed = data.get("completed", {})

    done_this_week = [s for s in sessions if s[0] in completed]
    missed_this_week = [s for s in sessions if s[0] not in completed and date.fromisoformat(s[1]) < date.today()]

    (hm_h, hm_m), (fm_h, fm_m), weeks, hr_trend_used, n_hr_points = predict_race_times(data)

    done_pct = len(done_this_week) / len(sessions) * 100 if sessions else 0

    summary = (
        f"📋 *Week {wnum} Summary*\n\n"
        f"Sessions done: {len(done_this_week)}/{len(sessions)} ({done_pct:.0f}%)\n"
    )

    if missed_this_week:
        summary += f"Missed: {len(missed_this_week)} session(s)\n"
        for s in missed_this_week:
            summary += f"  • {s[2][:45]}\n"

    summary += (
        f"\n🔮 *Updated Race Predictions*\n"
        f"Half Marathon: *{hm_h}:{hm_m:02d}*\n"
        f"Full Marathon: *{fm_h}:{fm_m:02d}*\n\n"
    )

    # Motivational note based on completion
    if done_pct == 100:
        summary += "Perfect week — every session ticked off. Keep that consistency going. 💪"
    elif done_pct >= 75:
        summary += "Solid week. Don't dwell on what you missed — focus on executing next week."
    elif done_pct >= 50:
        summary += "Inconsistent week. Identify what got in the way and plan around it for next week."
    else:
        summary += "Tough week. One bad week doesn't derail a plan — but two in a row does. Reset and commit."

    await update.message.reply_text(summary, parse_mode="Markdown", reply_markup=main_menu_keyboard())

# ── Help ─────────────────────────────────────────────────────────────
async def show_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*BYD Marathon Bot — Help*\n\n"
        "📅 *This Week* — Shows current week's sessions with quick-complete buttons\n"
        "📋 *Full Plan* — Browse all 24 weeks, tap to toggle completion\n"
        "✅ *Log Complete* — Mark a session done\n"
        "📷 *Upload Activity* — Attach a Strava screenshot or Garmin image to a session\n"
        "✏️ *Edit Thursday* — Update your Thursday track sessions (changes monthly)\n"
        "✏️ *Edit Tuesday* — Update your Tuesday run club sessions\n"
        "📊 *My Progress* — Overall stats and upcoming sessions\n\n"
        "*Commands:*\n"
        "/start — Main menu\n"
        "/week — This week\n"
        "/plan — Full plan browser\n"
        "/progress — Progress stats\n\n"
        "Tips:\n"
        "• Photos sent after tapping *Upload Activity* are auto-linked to that session\n"
        "• Edits to Thu/Tue sessions persist across the plan\n"
        "• Session edits made at start of month will update only future sessions",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

# ── Gear tracker ─────────────────────────────────────────────────────
DEFAULT_SHOES = {
    "superblast2": {"name": "ASICS Superblast 2", "km": 293.0, "limit": 500, "type": "Training"},
    "cloudmonster": {"name": "On CloudMonster Hyper", "km": 174.2, "limit": 400, "type": "Race/Tempo"},
    "endorphin": {"name": "Saucony Endorphin Speed", "km": 18.9, "limit": 500, "type": "Race"},
    "evosl": {"name": "Adidas Evo SL", "km": 77.6, "limit": 400, "type": "Race"},
}

def get_shoes(data: dict) -> dict:
    if "shoes" not in data:
        data["shoes"] = DEFAULT_SHOES.copy()
    return data["shoes"]

def shoe_bar(km, limit):
    pct = min(km / limit, 1.0)
    filled = int(pct * 10)
    bar = "█" * filled + "░" * (10 - filled)
    return f"{bar} {km:.0f}/{limit}km"

async def show_gear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    shoes = get_shoes(data)
    save_data(data)

    text = "👟 *Gear Tracker*\n\n"
    buttons = []
    for sid, shoe in shoes.items():
        km = shoe["km"]
        limit = shoe["limit"]
        pct = km / limit * 100
        warn = " ⚠️" if pct >= 80 else ""
        text += f"*{shoe['name']}*{warn}\n"
        text += f"{shoe_bar(km, limit)}\n"
        text += f"_{shoe['type']} · {pct:.0f}% used_\n\n"
        buttons.append([InlineKeyboardButton(f"➕ Log km — {shoe['name'][:25]}", callback_data=f"gear_log|{sid}")])

    buttons.append([InlineKeyboardButton("➕ Add new shoe", callback_data="gear_add")])

    await update.message.reply_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def cb_gear_log(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, sid = query.data.split("|")
    ctx.user_data["gear_log_sid"] = sid
    data = load_data()
    shoes = get_shoes(data)
    shoe = shoes.get(sid, {})
    await query.edit_message_text(
        f"👟 *{shoe.get('name', sid)}*\n\nCurrent: {shoe.get('km', 0):.0f}km\n\nHow many km did you run in these? (e.g. `8.6`)\n\nSend /cancel to abort.",
        parse_mode="Markdown"
    )
    return "GEAR_ENTER_KM"

async def cb_gear_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["gear_adding"] = True
    await query.edit_message_text(
        "👟 *Add new shoe*\n\nSend the shoe name and km limit in this format:\n`Nike Vaporfly 5, 400`\n\n(name, km limit)\n\nSend /cancel to abort.",
        parse_mode="Markdown"
    )
    return "GEAR_ADD_SHOE"

async def gear_enter_km(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sid = ctx.user_data.get("gear_log_sid")
    if not sid:
        return ConversationHandler.END
    try:
        km_added = float(update.message.text.strip().replace(",", "."))
    except ValueError:
        await update.message.reply_text("Please send a number like `8.6`", parse_mode="Markdown")
        return "GEAR_ENTER_KM"

    data = load_data()
    shoes = get_shoes(data)
    shoes[sid]["km"] = round(shoes[sid]["km"] + km_added, 1)
    save_data(data)

    shoe = shoes[sid]
    pct = shoe["km"] / shoe["limit"] * 100
    warn = "\n⚠️ *Over 80% — consider retiring soon.*" if pct >= 80 else ""
    await update.message.reply_text(
        f"✅ Logged {km_added}km on *{shoe['name']}*\n"
        f"Total: {shoe['km']:.0f}/{shoe['limit']}km ({pct:.0f}%){warn}",
        parse_mode="Markdown", reply_markup=main_menu_keyboard()
    )
    ctx.user_data.pop("gear_log_sid", None)
    return ConversationHandler.END

async def gear_add_shoe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        parts = update.message.text.strip().split(",")
        name = parts[0].strip()
        limit = int(parts[1].strip())
    except (IndexError, ValueError):
        await update.message.reply_text("Format: `Nike Vaporfly 5, 400`", parse_mode="Markdown")
        return "GEAR_ADD_SHOE"

    sid = re.sub(r"[^a-z0-9]", "", name.lower())[:12]
    data = load_data()
    shoes = get_shoes(data)
    shoes[sid] = {"name": name, "km": 0, "limit": limit, "type": "Training"}
    save_data(data)

    await update.message.reply_text(
        f"✅ Added *{name}* (limit: {limit}km)",
        parse_mode="Markdown", reply_markup=main_menu_keyboard()
    )
    return ConversationHandler.END

async def gear_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END

# ── Race debrief ──────────────────────────────────────────────────────
RACE_SESSIONS = [
    ("w14_sat", "Sep 27 Half Marathon"),
    ("w19_sat", "Nov 1 Half Marathon"),
    ("w24_sun", "Dec 6 BYD Full Marathon"),
]

DEBRIEF_PICK, DEBRIEF_TIME, DEBRIEF_EFFORT, DEBRIEF_PACING, DEBRIEF_NUTRITION, DEBRIEF_NOTES = range(20, 26)

async def race_debrief_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    buttons = [[InlineKeyboardButton(name, callback_data=f"debrief|{uid}")] for uid, name in RACE_SESSIONS]
    await update.message.reply_text(
        "🏁 *Race Debrief*\n\nWhich race do you want to debrief?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def cb_debrief_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, uid = query.data.split("|")
    name = next((n for u, n in RACE_SESSIONS if u == uid), uid)
    ctx.user_data["debrief_uid"] = uid
    ctx.user_data["debrief_name"] = name
    ctx.user_data["debrief"] = {}
    await query.edit_message_text(
        f"🏁 *{name} Debrief*\n\nWhat was your finish time? (e.g. `2:18:45` or `2:18`)",
        parse_mode="Markdown"
    )
    return DEBRIEF_TIME

async def debrief_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["debrief"]["time"] = update.message.text.strip()
    buttons = [[InlineKeyboardButton(str(i), callback_data=f"def|{i}")] for i in range(1, 11)]
    # arrange in 2 rows of 5
    rows = [buttons[i:i+5] for i in range(0, 10, 5)]
    await update.message.reply_text(
        "How would you rate your overall effort? (1 = easy, 10 = max effort)",
        reply_markup=InlineKeyboardMarkup(rows)
    )
    return DEBRIEF_EFFORT

async def cb_debrief_effort(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, val = query.data.split("|")
    ctx.user_data["debrief"]["effort"] = val
    await query.edit_message_text(
        "How was your pacing?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🐇 Went out too fast", callback_data="pac|fast")],
            [InlineKeyboardButton("✅ Executed perfectly", callback_data="pac|perfect")],
            [InlineKeyboardButton("🐢 Too conservative", callback_data="pac|slow")],
        ])
    )
    return DEBRIEF_PACING

async def cb_debrief_pacing(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, val = query.data.split("|")
    ctx.user_data["debrief"]["pacing"] = {"fast": "Went out too fast", "perfect": "Perfect execution", "slow": "Too conservative"}[val]
    await query.edit_message_text(
        "How was your nutrition/fuelling?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Fuelled well", callback_data="nut|good")],
            [InlineKeyboardButton("😵 Bonked / ran out of energy", callback_data="nut|bonked")],
            [InlineKeyboardButton("🤢 GI issues", callback_data="nut|gi")],
            [InlineKeyboardButton("⬜ Didn't fuel (short race)", callback_data="nut|none")],
        ])
    )
    return DEBRIEF_NUTRITION

async def cb_debrief_nutrition(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, val = query.data.split("|")
    ctx.user_data["debrief"]["nutrition"] = {"good": "Fuelled well", "bonked": "Bonked", "gi": "GI issues", "none": "No fuelling needed"}[val]
    await query.edit_message_text(
        "Any notes? What went well, what to improve? (or send `-` to skip)"
    )
    return DEBRIEF_NOTES

async def debrief_notes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    notes = update.message.text.strip()
    if notes == "-":
        notes = ""
    ctx.user_data["debrief"]["notes"] = notes

    uid = ctx.user_data["debrief_uid"]
    name = ctx.user_data["debrief_name"]
    d = ctx.user_data["debrief"]

    data = load_data()
    data.setdefault("debriefs", {})[uid] = {**d, "date": today_str()}
    save_data(data)

    summary = (
        f"🏁 *{name} — Debrief Saved*\n\n"
        f"⏱ Finish time: *{d.get('time', '—')}*\n"
        f"💪 Effort: {d.get('effort', '—')}/10\n"
        f"📈 Pacing: {d.get('pacing', '—')}\n"
        f"🍌 Nutrition: {d.get('nutrition', '—')}\n"
    )
    if notes:
        summary += f"📝 Notes: _{notes}_\n"

    await update.message.reply_text(summary, parse_mode="Markdown", reply_markup=main_menu_keyboard())
    ctx.user_data.pop("debrief", None)
    ctx.user_data.pop("debrief_uid", None)
    ctx.user_data.pop("debrief_name", None)
    return ConversationHandler.END

async def debrief_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Debrief cancelled.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END

# ── Race manager ─────────────────────────────────────────────────────
RACE_ADD_NAME, RACE_ADD_DATE, RACE_ADD_TARGET = range(30, 33)

async def show_race_manager(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    custom_races = data.get("custom_races", [])

    # Built-in races
    builtin = [
        ("Sep 27", "Half Marathon", "Target 2:15"),
        ("Nov 1", "Half Marathon", "Marathon effort 2:28"),
        ("Nov 26", "Hyrox Women Doubles", "10 days before marathon"),
        ("Dec 5", "Pacing HM @ 2:45", "⚠️ Day before marathon"),
        ("Dec 6", "BYD Full Marathon", "Target sub-5:00 🏅"),
    ]

    text = "🗓 *Race Manager*\n\n*Built-in races:*\n"
    for date_str, name, note in builtin:
        text += f"• {date_str}: {name} — _{note}_\n"

    if custom_races:
        text += "\n*Your custom races:*\n"
        buttons = []
        for i, r in enumerate(custom_races):
            text += f"• {r['date']}: {r['name']} — _{r.get('target', '')}_\n"
            buttons.append([InlineKeyboardButton(f"🗑 Remove: {r['name'][:30]}", callback_data=f"race_remove|{i}")])
    else:
        buttons = []

    buttons.append([InlineKeyboardButton("➕ Add a race", callback_data="race_add")])
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def cb_race_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🗓 *Add a race*\n\nWhat's the race name? (e.g. `Jurong Lake Runs 10K`)\n\nSend /cancel to abort.",
        parse_mode="Markdown"
    )
    return RACE_ADD_NAME

async def race_add_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["new_race_name"] = update.message.text.strip()
    await update.message.reply_text(
        "📅 What's the race date? (e.g. `2026-08-23`)",
        parse_mode="Markdown"
    )
    return RACE_ADD_DATE

async def race_add_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    date_str = update.message.text.strip()
    try:
        date.fromisoformat(date_str)
    except ValueError:
        await update.message.reply_text("Please use format `YYYY-MM-DD`, e.g. `2026-08-23`", parse_mode="Markdown")
        return RACE_ADD_DATE
    ctx.user_data["new_race_date"] = date_str
    await update.message.reply_text("🎯 What's your target time? (e.g. `55:00` for 10K, or `-` to skip)")
    return RACE_ADD_TARGET

async def race_add_target(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    target = update.message.text.strip()
    if target == "-":
        target = ""
    data = load_data()
    race = {
        "name": ctx.user_data.get("new_race_name", ""),
        "date": ctx.user_data.get("new_race_date", ""),
        "target": target
    }
    data.setdefault("custom_races", []).append(race)
    save_data(data)
    await update.message.reply_text(
        f"✅ Added *{race['name']}* on {race['date']}" + (f" — target {target}" if target else ""),
        parse_mode="Markdown", reply_markup=main_menu_keyboard()
    )
    ctx.user_data.pop("new_race_name", None)
    ctx.user_data.pop("new_race_date", None)
    return ConversationHandler.END

async def cb_race_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, idx = query.data.split("|")
    data = load_data()
    races = data.get("custom_races", [])
    if int(idx) < len(races):
        removed = races.pop(int(idx))
        save_data(data)
        await query.edit_message_text(f"🗑 Removed *{removed['name']}*", parse_mode="Markdown")
    else:
        await query.edit_message_text("Race not found.")

async def race_manager_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END

# ── Mileage tracker ──────────────────────────────────────────────────
# Planned km per week (approximate based on plan)
WEEKLY_KM_TARGETS = {
    1: 20, 2: 24, 3: 28, 4: 20, 5: 32, 6: 36, 7: 38, 8: 42,
    9: 28, 10: 44, 11: 46, 12: 32, 13: 20, 14: 22, 15: 18,
    16: 44, 17: 48, 18: 46, 19: 18, 20: 30, 21: 42, 22: 36,
    23: 24, 24: 10
}

def get_mileage(data: dict) -> dict:
    """Return mileage dict {week_num: km}"""
    return data.get("mileage", {})

def log_mileage(data: dict, week_num: int, km: float):
    data.setdefault("mileage", {})
    wk = str(week_num)
    data["mileage"][wk] = round(data["mileage"].get(wk, 0.0) + km, 1)

async def show_mileage(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    mileage = get_mileage(data)
    wnum = current_week_num()

    # Current week
    this_week_km = mileage.get(str(wnum), 0.0)
    this_week_target = WEEKLY_KM_TARGETS.get(wnum, 30)
    pct = min(this_week_km / this_week_target, 1.0)
    filled = int(pct * 10)
    bar = "█" * filled + "░" * (10 - filled)

    # Total
    total_km = sum(mileage.values())
    total_runs = len([k for k in data.get("logs", {}) if not k.startswith("extra") or True])

    # Last 4 weeks trend
    trend = ""
    for w in range(max(1, wnum - 3), wnum + 1):
        wkm = mileage.get(str(w), 0.0)
        wtgt = WEEKLY_KM_TARGETS.get(w, 30)
        wpct = min(wkm / wtgt, 1.0)
        wfilled = int(wpct * 6)
        wbar = "█" * wfilled + "░" * (6 - wfilled)
        trend += f"W{w:02d}: {wbar} {wkm:.1f}/{wtgt}km\n"

    text = (
        f"📊 *Mileage Tracker*\n\n"
        f"*This week (W{wnum:02d}):*\n"
        f"{bar} {this_week_km:.1f}/{this_week_target}km ({pct*100:.0f}%)\n\n"
        f"*Last 4 weeks:*\n{trend}\n"
        f"*Total logged:* {total_km:.1f}km across all runs\n\n"
        f"_Mileage is auto-logged when you upload activity screenshots._"
    )

    # Manual log button
    buttons = [[InlineKeyboardButton("➕ Log km manually", callback_data="mileage_manual")]]
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def cb_mileage_manual(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    wnum = current_week_num()

    # Show week picker — current week + last 3 weeks
    buttons = []
    for w in range(max(1, wnum - 3), wnum + 1):
        buttons.append([InlineKeyboardButton(f"Week {w}", callback_data=f"mileage_week|{w}")])

    await query.edit_message_text(
        "📊 *Log km manually*\n\nWhich week?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def cb_mileage_week_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, wnum = query.data.split("|")
    ctx.user_data["mileage_week"] = int(wnum)
    await query.edit_message_text(
        f"📊 Log km for *Week {wnum}*\n\nHow many km did you run? (e.g. `8.5`)\n\nSend /cancel to abort.",
        parse_mode="Markdown"
    )
    return "MILEAGE_ENTER"

async def mileage_enter(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        km = float(update.message.text.strip().replace(",", "."))
    except ValueError:
        await update.message.reply_text("Please send a number like `8.5`", parse_mode="Markdown")
        return "MILEAGE_ENTER"
    wnum = ctx.user_data.get("mileage_week", current_week_num())
    data = load_data()
    log_mileage(data, wnum, km)
    save_data(data)
    total = data["mileage"].get(str(wnum), 0.0)
    target = WEEKLY_KM_TARGETS.get(wnum, 30)
    await update.message.reply_text(
        f"✅ Logged {km}km for W{wnum:02d}\nWeek total: {total:.1f}/{target}km",
        reply_markup=main_menu_keyboard()
    )
    return ConversationHandler.END

async def mileage_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


SGT = pytz.timezone("Asia/Singapore")

def save_chat_id(chat_id: int):
    CHAT_ID_FILE.write_text(str(chat_id))

def load_chat_id():
    if CHAT_ID_FILE.exists():
        try:
            return int(CHAT_ID_FILE.read_text().strip())
        except:
            return None
    return None

# ── Monday morning briefing ───────────────────────────────────────────
async def send_monday_briefing(app):
    chat_id = load_chat_id()
    if not chat_id:
        return

    data = load_data()
    wnum = current_week_num()
    week_key = f"w{wnum:02d}"
    sessions = get_week_sessions(week_key)
    completed = data.get("completed", {})

    session_lines = ""
    for s in sessions:
        done = "✅" if s[0] in completed else "⬜"
        # Use edited title if available, strip [Tag] prefix for cleaner display
        display = data.get("edits", {}).get(s[0], {}).get("summary", s[2])
        display = re.sub(r'\[.*?\]\s*', '', display).strip()
        session_lines += f"{done} {s[1]}: {display[:55]}\n"

    km_target = WEEKLY_KM_TARGETS.get(wnum, 30)
    last_week_km = data.get("mileage", {}).get(str(wnum - 1), 0.0)
    weeks_left = max(0, (date(2026, 12, 6) - date.today()).days // 7)

    race_alert = ""
    for s in sessions:
        if "RACE" in s[2] or "🏁" in s[2]:
            race_alert = f"\n🏁 *Race this week!* {s[2]}\n"

    injury_warn = ""
    last_wk_km = data.get("mileage", {}).get(str(wnum - 1), 0.0)
    two_wk_km = data.get("mileage", {}).get(str(wnum - 2), 0.0)
    if last_wk_km > 0 and two_wk_km > 0:
        jump = (last_wk_km - two_wk_km) / two_wk_km * 100
        if jump > 15:
            injury_warn = f"\n⚠️ *Mileage jumped {jump:.0f}% last week* — ease into this week.\n"

    if wnum <= 4:
        focus = "Base building. Keep every easy run actually easy — HR under 145."
    elif wnum <= 8:
        focus = "Building phase. Hit your track targets but don't chase the club pace on Tuesdays."
    elif wnum <= 14:
        focus = "Peak phase. Prioritise sleep and nutrition — the long runs matter most now."
    elif wnum <= 20:
        focus = "Race prep. Trust the taper. Less is more this week."
    else:
        focus = "Final stretch. Stay healthy, stay consistent. Dec 6 is close."

    msg = (
        f"🌅 *Good morning, Yitian! Week {wnum} starts today.*\n\n"
        f"*{weeks_left} weeks to BYD Marathon (Dec 6)*\n"
        f"{race_alert}{injury_warn}\n"
        f"*This week's sessions:*\n{session_lines}\n"
        f"*Weekly km target:* {km_target}km\n"
        f"*Last week logged:* {last_week_km:.1f}km\n\n"
        f"*Focus this week:*\n_{focus}_"
    )
    await app.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")

# ── Pre-race checklist ────────────────────────────────────────────────
RACE_CHECKLISTS = {
    "w14_sat": {
        "name": "Sep 27 Half Marathon",
        "checklist": [
            "Kit laid out: singlet, shorts, socks, race shoes",
            "Bib picked up and pinned",
            "2 gels packed (take at 45min)",
            "Race plan: start @ 6:25/km, negative split second half",
            "Dinner tonight: carbs, familiar food, nothing new",
            "Sleep by 10pm — race start is early",
            "Garmin charged and synced",
        ]
    },
    "w19_sat": {
        "name": "Nov 1 Half Marathon",
        "checklist": [
            "Kit laid out: singlet, shorts, socks, race shoes",
            "Bib picked up and pinned",
            "3 gels packed (marathon effort — fuel more)",
            "Race plan: run @ 7:00–7:05/km marathon effort, NOT racing",
            "Dinner tonight: carbs, familiar food",
            "Sleep by 10pm",
            "Remember: this is a training run with a bib, not a race",
        ]
    },
    "w24_sun": {
        "name": "Dec 6 BYD Full Marathon",
        "checklist": [
            "Race kit laid out the night before",
            "Bib pinned",
            "5 gels packed (every 45min from 45min mark)",
            "Race plan: 7:15/km first 10km, settle 7:05 from 10–30km",
            "Breakfast: 2–3hrs before start, familiar food",
            "Garmin fully charged",
            "Body Glide / anti-chafe applied",
            "Sleep early Dec 5 — pacing duty first but rest after",
            "You've done the work. Trust the training.",
        ]
    }
}

async def send_morning_sleep_prompt(app):
    """Sent every morning at 7am SGT."""
    chat_id = load_chat_id()
    if not chat_id:
        return
    await app.bot.send_message(
        chat_id=chat_id,
        text=(
            "🌅 *Good morning, Yitian!*\n\n"
            "What's your Garmin sleep score this morning?\n\n"
            "_Check Garmin Connect → Sleep_"
        ),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("😴 Good — Score ≥72 or HRV ≥60ms", callback_data="sleep|good")],
            [InlineKeyboardButton("😐 Average — Score 58–71 or HRV 52–59ms", callback_data="sleep|avg")],
            [InlineKeyboardButton("😫 Poor — Score <58 or HRV <52ms / 🟠", callback_data="sleep|poor")],
            [InlineKeyboardButton("⬜ Skip today", callback_data="sleep|skip")],
        ])
    )

async def cb_sleep_log(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, rating = query.data.split("|")

    if rating == "skip":
        await query.edit_message_text("Skipped — have a good training day! 👟")
        return

    data = load_data()
    data.setdefault("sleep_log", {})[today_str()] = rating
    save_data(data)

    responses = {
        "good": "Noted ✅ — green light for today's session. Hit your targets.",
        "avg": "Noted — average recovery. Train as planned but don't force it if something feels off.",
        "poor": "Noted ⚠️ — below baseline. Complete the session but reduce intensity if HR climbs too high early."
    }
    await query.edit_message_text(responses[rating])

async def check_and_send_race_checklists(app):
    import datetime as dt
    target = (date.today() + dt.timedelta(days=3)).isoformat()
    for uid, info in RACE_CHECKLISTS.items():
        s = get_session(uid)
        if s and s[1] == target:
            chat_id = load_chat_id()
            if not chat_id:
                return
            items = "\n".join(f"☐ {item}" for item in info["checklist"])
            msg = (
                f"🏁 *3 days to {info['name']}!*\n\n"
                f"*Pre-race checklist:*\n{items}\n\n"
                f"_Get everything ready today. You've got this._"
            )
            await app.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")

# ── Sleep / recovery logging ──────────────────────────────────────────
async def cb_recovery(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("|")
    rating = parts[1]
    uid = parts[2] if len(parts) > 2 else ""

    if rating == "skip":
        await query.edit_message_text("Skipped.")
        return

    data = load_data()
    data.setdefault("recovery", {})[uid] = {"rating": rating, "date": today_str()}
    save_data(data)

    msgs = {
        "good": "Fresh legs logged ✅ — coach analysis will factor this in.",
        "avg": "Average recovery logged — take the warm-up easy.",
        "poor": "Fatigued noted ⚠️ — if the session feels wrong, cut it short."
    }
    await query.edit_message_text(msgs.get(rating, "Logged."))


async def text_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📅 This Week":
        await show_this_week(update, ctx)
    elif text == "📋 Full Plan":
        await show_full_plan(update, ctx)
    elif text == "✅ Log Complete":
        await log_complete_start(update, ctx)
    elif text == "📷 Upload Activity":
        await upload_start(update, ctx)
    elif text == "✏️ Edit Thursday":
        await edit_thu_start(update, ctx)
    elif text == "✏️ Edit Tuesday":
        await edit_tue_start(update, ctx)
    elif text == "📊 My Progress":
        await show_progress(update, ctx)
    elif text == "🔮 Predictions":
        await show_predictions(update, ctx)
    elif text == "📋 Week Summary":
        await show_weekly_summary(update, ctx)
    elif text == "👟 Gear Tracker":
        await show_gear(update, ctx)
    elif text == "🏁 Race Debrief":
        await race_debrief_start(update, ctx)
    elif text == "🗓 Race Manager":
        await show_race_manager(update, ctx)
    elif text == "📈 Mileage":
        await show_mileage(update, ctx)
    elif text == "💬 Ask Coach":
        return await ask_coach_start(update, ctx)
    elif text == "🗺 Route Planner":
        return await route_planner_start(update, ctx)
    elif text == "🆘 Help":
        await show_help(update, ctx)
    elif text == "🍽️ Log Food":
        return await food.food_menu(update, ctx)
    elif text == "📊 Nutrition":
        return await food.show_today(update, ctx)
    elif text == "🏋️ Extra Exercise":
        return await food.exercise_menu(update, ctx)
    elif text == "⚖️ My Profile":
        return await food.profile_menu(update, ctx)
    else:
        await update.message.reply_text(
            "Use the menu buttons below, or /help for commands.",
            reply_markup=main_menu_keyboard()
        )

ASK_COACH_STATE = 40

async def ask_coach_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💬 *Ask Coach*\n\n"
        "Ask anything about your training — pacing, nutrition, race strategy, recovery, gear.\n\n"
        "Type your question:",
        parse_mode="Markdown"
    )
    return ASK_COACH_STATE

async def ask_coach_answer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    question = update.message.text.strip()
    if not ANTHROPIC_KEY:
        await update.message.reply_text("Coach unavailable — ANTHROPIC_API_KEY not set.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    await update.message.reply_text("🤔 Thinking...")

    try:
        data = load_data()
        wnum = current_week_num()
        completed_count = len(data.get("completed", {}))
        (hm_h, hm_m), (fm_h, fm_m), weeks, hr_trend_used, n_hr_points = predict_race_times(data)
        best_efforts = get_best_efforts(data)

        context = (
            f"You are an experienced marathon running coach advising Yitian, a female runner based in Singapore.\n"
            f"Current training: Week {wnum}/24 of BYD Marathon plan (Dec 6 2026, sub-4:45 target).\n"
            f"Sessions completed: {completed_count}. Predicted HM: {hm_h}:{hm_m:02d}, FM: {fm_h}:{fm_m:02d} "
            f"({'blended from PB + HR trend' if hr_trend_used else 'from 10K PB only'}).\n"
            f"Current PBs — 5K {best_efforts['5K']}, 10K {best_efforts['10K']}, HM {best_efforts['HM']} "
            f"(auto-updates from logged runs). Max HR: 184bpm. Avg sleep score: 68.\n"
            f"Races: Sep 27 HM (target 2:15), Nov 1 HM (marathon effort), Nov 26 Hyrox, Dec 6 Full Marathon.\n"
            f"Trains in Singapore heat (28-34°C, 70-90% humidity) — HR runs 5-10bpm higher than cool conditions.\n"
            f"Shoes: ASICS Superblast 2 (training), On CloudMonster Hyper (race/tempo), Saucony Endorphin Speed (race), Adidas Evo SL (race).\n\n"
            f"Answer directly and concisely. Be specific with numbers. "
            f"Sound like a coach — firm, practical, no fluff. 3-5 sentences max unless a detailed breakdown is needed."
        )

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 400,
                    "system": context,
                    "messages": [{"role": "user", "content": question}]
                }
            )
        answer = resp.json()["content"][0]["text"]
        await update.message.reply_text(f"💬 *Coach:*\n\n{answer}", parse_mode="Markdown", reply_markup=main_menu_keyboard())
    except Exception as e:
        logger.error(f"Ask coach error: {e}")
        await update.message.reply_text("Something went wrong. Try again.", reply_markup=main_menu_keyboard())

    return ConversationHandler.END

async def ask_coach_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


ROUTE_START, ROUTE_END, ROUTE_DISTANCE = range(50, 53)

def build_gpx(route_name: str, points: list) -> str:
    """Build a GPX file from a list of (lat, lon) points."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="YT Running Bot" xmlns="http://www.topografix.com/GPX/1/1">',
        f'  <trk><name>{route_name}</name><trkseg>',
    ]
    for lat, lon in points:
        lines.append(f'    <trkpt lat="{lat}" lon="{lon}"></trkpt>')
    lines += ['  </trkseg></trk>', '</gpx>']
    return "\n".join(lines)

async def get_route(origin: str, destination: str, target_km: float) -> dict:
    """Call Google Maps Directions API and return route info."""
    if not GMAPS_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://maps.googleapis.com/maps/api/directions/json",
                params={
                    "origin": origin,
                    "destination": destination if destination else origin,
                    "mode": "walking",
                    "key": GMAPS_KEY,
                    "region": "sg",
                }
            )
        data = resp.json()
        if data["status"] != "OK":
            return {"error": data.get("status", "Unknown error")}

        route = data["routes"][0]
        leg = route["legs"][0]
        distance_m = leg["distance"]["value"]
        duration_s = leg["duration"]["value"]

        # Extract polyline points
        import base64 as b64
        def decode_polyline(encoded):
            points = []
            index = 0
            lat = 0
            lng = 0
            while index < len(encoded):
                result = 0
                shift = 0
                while True:
                    b = ord(encoded[index]) - 63
                    index += 1
                    result |= (b & 0x1f) << shift
                    shift += 5
                    if b < 0x20:
                        break
                dlat = ~(result >> 1) if result & 1 else result >> 1
                lat += dlat
                result = 0
                shift = 0
                while True:
                    b = ord(encoded[index]) - 63
                    index += 1
                    result |= (b & 0x1f) << shift
                    shift += 5
                    if b < 0x20:
                        break
                dlng = ~(result >> 1) if result & 1 else result >> 1
                lng += dlng
                points.append((lat / 1e5, lng / 1e5))
            return points

        points = decode_polyline(route["overview_polyline"]["points"])

        # Build step-by-step directions
        steps = []
        for step in leg["steps"]:
            instruction = re.sub(r'<[^>]+>', '', step["html_instructions"])
            dist = step["distance"]["text"]
            steps.append(f"• {instruction} ({dist})")

        return {
            "distance_km": distance_m / 1000,
            "duration_min": duration_s // 60,
            "points": points,
            "steps": steps[:10],  # first 10 steps
            "start_address": leg["start_address"],
            "end_address": leg["end_address"],
        }
    except Exception as e:
        logger.error(f"Google Maps error: {e}")
        return {"error": str(e)}

async def route_planner_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🗺 *Route Planner*\n\n"
        "I'll plan a running route and give you a GPX file to import into Garmin.\n\n"
        "📍 What's your *starting point*?\n"
        "_(e.g. `160 Tampines Street 12` or `Bedok MRT`)_",
        parse_mode="Markdown"
    )
    return ROUTE_START

async def route_get_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["route_start"] = update.message.text.strip() + ", Singapore"
    await update.message.reply_text(
        "🏁 What's your *end point*?\n\n"
        "_(Send `-` to make it a loop back to start)_",
        parse_mode="Markdown"
    )
    return ROUTE_END

async def route_get_end(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    end = update.message.text.strip()
    if end == "-":
        ctx.user_data["route_end"] = ctx.user_data["route_start"]
    else:
        ctx.user_data["route_end"] = end + ", Singapore"
    await update.message.reply_text(
        "📏 How many *km* do you want to run?\n_(e.g. `14`)_",
        parse_mode="Markdown"
    )
    return ROUTE_DISTANCE

async def route_get_distance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        km = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Please send a number like `14`", parse_mode="Markdown")
        return ROUTE_DISTANCE

    start = ctx.user_data.get("route_start", "")
    end = ctx.user_data.get("route_end", "")

    await update.message.reply_text("🗺 Planning your route...")

    if not GMAPS_KEY:
        await update.message.reply_text(
            "Google Maps API key not set. Add `GOOGLE_MAPS_API_KEY` to Railway variables.",
            reply_markup=main_menu_keyboard()
        )
        return ConversationHandler.END

    result = await get_route(start, end, km)

    if not result or "error" in result:
        # Fallback to AI suggestion
        await update.message.reply_text(
            f"⚠️ Couldn't get a live route (error: {result.get('error', 'unknown')}).\n\n"
            f"Make sure the Directions API is enabled in Google Cloud Console → APIs & Services → Library → search 'Directions API' → Enable.",
            reply_markup=main_menu_keyboard()
        )
        return ConversationHandler.END

    actual_km = result["distance_km"]
    duration = result["duration_min"]
    steps = "\n".join(result["steps"])

    # Build GPX
    gpx_content = build_gpx(f"Running route {km}km", result["points"])
    gpx_path = DATA_DIR / "route.gpx"
    gpx_path.write_text(gpx_content)

    # Send summary
    await update.message.reply_text(
        f"🗺 *Your {km}km Running Route*\n\n"
        f"📍 From: {result['start_address'][:50]}\n"
        f"🏁 To: {result['end_address'][:50]}\n"
        f"📏 Distance: {actual_km:.1f}km\n"
        f"⏱ Est. time @ 7:30/km: ~{int(km * 7.5)}min\n\n"
        f"*Directions:*\n{steps}\n\n"
        f"_GPX file coming next — import into Garmin Connect_",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

    # Send GPX file
    await update.message.reply_document(
        document=open(gpx_path, "rb"),
        filename=f"run_{int(km)}km.gpx",
        caption="📎 Import this into Garmin Connect → Courses → Import"
    )

    ctx.user_data.pop("route_start", None)
    ctx.user_data.pop("route_end", None)
    return ConversationHandler.END

async def route_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Route planning cancelled.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


# ── Build & run ──────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TOKEN).build()

    # Edit conversation handler
    edit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_edit_pick, pattern=r"^edit_pick\|")],
        states={
            EDIT_ENTER_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_receive_text),
                CommandHandler("cancel", edit_cancel),
            ],
            EDIT_ENTER_NOTES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_receive_notes),
                CommandHandler("cancel", edit_cancel),
            ],
        },
        fallbacks=[CommandHandler("cancel", edit_cancel)],
        per_message=False,
    )

    # Gear tracker conversation
    gear_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_gear_log, pattern=r"^gear_log\|"),
            CallbackQueryHandler(cb_gear_add, pattern=r"^gear_add$"),
        ],
        states={
            "GEAR_ENTER_KM": [MessageHandler(filters.TEXT & ~filters.COMMAND, gear_enter_km)],
            "GEAR_ADD_SHOE": [MessageHandler(filters.TEXT & ~filters.COMMAND, gear_add_shoe)],
        },
        fallbacks=[CommandHandler("cancel", gear_cancel)],
        per_message=False,
    )

    # Race debrief conversation
    debrief_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_debrief_pick, pattern=r"^debrief\|")],
        states={
            DEBRIEF_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, debrief_time)],
            DEBRIEF_EFFORT: [CallbackQueryHandler(cb_debrief_effort, pattern=r"^def\|")],
            DEBRIEF_PACING: [CallbackQueryHandler(cb_debrief_pacing, pattern=r"^pac\|")],
            DEBRIEF_NUTRITION: [CallbackQueryHandler(cb_debrief_nutrition, pattern=r"^nut\|")],
            DEBRIEF_NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, debrief_notes)],
        },
        fallbacks=[CommandHandler("cancel", debrief_cancel)],
        per_message=False,
    )

    # Race manager conversation
    race_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_race_add, pattern=r"^race_add$")],
        states={
            RACE_ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, race_add_name)],
            RACE_ADD_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, race_add_date)],
            RACE_ADD_TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, race_add_target)],
        },
        fallbacks=[CommandHandler("cancel", race_manager_cancel)],
        per_message=False,
    )

    # Mileage manual entry conversation
    mileage_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_mileage_manual, pattern=r"^mileage_manual$"),
            CallbackQueryHandler(cb_mileage_week_pick, pattern=r"^mileage_week\|"),
        ],
        states={
            "MILEAGE_ENTER": [MessageHandler(filters.TEXT & ~filters.COMMAND, mileage_enter)],
        },
        fallbacks=[CommandHandler("cancel", mileage_cancel)],
        per_message=False,
    )

    ask_coach_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^💬 Ask Coach$"), ask_coach_start)],
        states={
            ASK_COACH_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_coach_answer)],
        },
        fallbacks=[CommandHandler("cancel", ask_coach_cancel)],
        per_message=False,
    )

    route_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🗺 Route Planner$"), route_planner_start)],
        states={
            ROUTE_START: [MessageHandler(filters.TEXT & ~filters.COMMAND, route_get_start)],
            ROUTE_END: [MessageHandler(filters.TEXT & ~filters.COMMAND, route_get_end)],
            ROUTE_DISTANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, route_get_distance)],
        },
        fallbacks=[CommandHandler("cancel", route_cancel)],
        per_message=False,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("week", show_this_week))
    app.add_handler(CommandHandler("plan", show_full_plan))
    app.add_handler(CommandHandler("progress", show_progress))
    app.add_handler(CommandHandler("help", show_help))

    app.add_handler(edit_conv)
    app.add_handler(gear_conv)
    app.add_handler(debrief_conv)
    app.add_handler(race_conv)
    app.add_handler(mileage_conv)
    app.add_handler(ask_coach_conv)
    app.add_handler(route_conv)
    app.add_handler(CallbackQueryHandler(cb_autogear, pattern=r"^autogear\|"))
    app.add_handler(CallbackQueryHandler(cb_upload_done, pattern=r"^upload_done$"))
    app.add_handler(CallbackQueryHandler(cb_race_remove, pattern=r"^race_remove\|"))
    app.add_handler(CallbackQueryHandler(cb_recovery, pattern=r"^recovery\|"))
    app.add_handler(CallbackQueryHandler(cb_sleep_log, pattern=r"^sleep\|"))

    app.add_handler(CallbackQueryHandler(cb_week, pattern=r"^week\|"))
    app.add_handler(CallbackQueryHandler(cb_toggle, pattern=r"^toggle\|"))
    app.add_handler(CallbackQueryHandler(cb_done, pattern=r"^done\|"))
    app.add_handler(CallbackQueryHandler(cb_markdone, pattern=r"^markdone\|"))
    app.add_handler(CallbackQueryHandler(cb_upload_session, pattern=r"^upload_session\|"))
    app.add_handler(CallbackQueryHandler(cb_back_weeks, pattern=r"^back_weeks$"))

    food.register(app)

    app.add_handler(MessageHandler(filters.PHOTO, receive_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    # Scheduler for Monday briefing + race checklists
    scheduler = AsyncIOScheduler(timezone=SGT)
    scheduler.add_job(send_monday_briefing, "cron", day_of_week="mon", hour=7, minute=0, args=[app])
    scheduler.add_job(send_morning_sleep_prompt, "cron", hour=7, minute=0, args=[app])
    scheduler.add_job(check_and_send_race_checklists, "cron", hour=8, minute=0, args=[app])
    scheduler.start()

    logger.info("Bot starting…")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
