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
from datetime import datetime, date
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DATA_FILE = Path("data.json")

# ── Conversation states ──────────────────────────────────────────────
EDIT_CHOOSE_SESSION, EDIT_ENTER_TEXT = range(2)
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
  ("w05_wed", "2026-07-22", "W05 · [Easy] Easy 5km @ 7:45/km", "", False, False),
  ("w05_thu", "2026-07-23", "W05 · [Track] Track club — 8×1km @ 5:40/km", "", True, False),
  ("w05_sat", "2026-07-25", "W05 · [Long run] Long run 18km @ 7:30/km", "Carry water. Plan a looped route. New distance milestone.", False, False),
  # W06
  ("w06_tue", "2026-07-28", "W06 · [Tempo] Run club — full session: 70min + 3×20min @ 6:50/km", "Good quality week. Recover well after.", False, True),
  ("w06_wed", "2026-07-29", "W06 · [Easy] Easy 5km @ 7:45/km", "", False, False),
  ("w06_thu", "2026-07-30", "W06 · [Track] Track club — 12×300m @ 5:45/km", "", True, False),
  ("w06_sat", "2026-08-01", "W06 · [Long run] Long run 20km @ 7:30–7:45/km", "MILESTONE: first 20km. Bring a gel — take at 60min. Walk 1 min every 5km if needed.", False, False),
  # W07
  ("w07_tue", "2026-08-04", "W07 · [Tempo] Run club — full tempo session", "", False, True),
  ("w07_wed", "2026-08-05", "W07 · [MP run] Marathon pace run: 10km @ 7:00–7:05/km", "Replaces easy mid-week run. Controlled effort — not racing.", False, False),
  ("w07_thu", "2026-08-06", "W07 · [Track] Track club — 2×2km + 6×200m", "2km @ 6:00/km. Feel the pace difference from Wednesday.", True, False),
  ("w07_sat", "2026-08-08", "W07 · [Long run] Long run 22km @ 7:30/km", "Fuel at 45min and 90min. Practice your race-day gel routine.", False, False),
  # W08
  ("w08_tue", "2026-08-11", "W08 · [Tempo] Run club — 3×20min tempo @ 6:50/km", "", False, True),
  ("w08_wed", "2026-08-12", "W08 · [MP run] Marathon pace run: 12km @ 7:00/km", "", False, False),
  ("w08_thu", "2026-08-13", "W08 · [Track] Track club — 8×1km @ 5:40/km", "", True, False),
  ("w08_sat", "2026-08-15", "W08 · [Long run] Long run 24km @ 7:20–7:30/km", "Last 4km push to 7:00/km if feeling good.", False, False),
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
  # W11
  ("w11_tue", "2026-09-01", "W11 · [Tempo] Run club — 70min + 2×20min @ 6:50/km", "Slightly reduced — big long run coming Saturday.", False, True),
  ("w11_wed", "2026-09-02", "W11 · [MP run] Marathon pace: 10km @ 7:00/km", "", False, False),
  ("w11_thu", "2026-09-03", "W11 · [Track] Track club — 12×300m @ 5:45/km", "", True, False),
  ("w11_sat", "2026-09-05", "W11 · [Long run] Long run 28km @ 7:30/km", "Confidence builder. Run your own pace. Gel every 45min.", False, False),
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
  ("w16_sat", "2026-10-10", "W16 · [Long run] Long run 28km @ 7:20/km", "Race-day shoes and gels. Simulate Dec 6 exactly.", False, False),
  # W17
  ("w17_tue", "2026-10-13", "W17 · [Tempo] Run club — 3×20min @ 6:50/km", "", False, True),
  ("w17_wed", "2026-10-14", "W17 · [MP run] Marathon pace: 14km @ 7:00/km", "Longest MP run in the plan. Controlled effort.", False, False),
  ("w17_thu", "2026-10-15", "W17 · [Track] Track club — 2×2km + 6×200m", "", True, False),
  ("w17_sat", "2026-10-17", "W17 · [Long run] Long run 30km @ 7:15–7:20/km", "Race-day kit. Gel at 45, 90, 135min. Last 5km @ 7:00/km.", False, False),
  # W18
  ("w18_tue", "2026-10-20", "W18 · [Tempo] Run club — 2×20min @ 6:50/km only", "Reduce to 2 reps — big 32km coming Saturday.", False, True),
  ("w18_wed", "2026-10-21", "W18 · [MP run] Marathon pace: 10km @ 7:00/km", "", False, False),
  ("w18_thu", "2026-10-22", "W18 · [Track] Track club — 8×1km @ 5:40/km", "", True, False),
  ("w18_sat", "2026-10-24", "W18 · [Long run] Long run 32km @ 7:20/km", "PEAK RUN. Gel at 45, 90, 135, 165min. Run through fatigue. You earned this.", False, False),
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
  ("w24_sun", "2026-12-06", "W24 · [🏁 RACE DAY] BYD Full Marathon — Target sub-5hr", "Start @ 7:15/km for first 10km. Settle into 7:05 from 10–30km. Hold on. You've done the work.", False, False),
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
    display_summary = overrides.get(uid, {}).get("summary", summary)
    display_desc = overrides.get(uid, {}).get("desc", desc)
    done = uid in data.get("completed", {})
    has_log = uid in data.get("logs", {})

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
            ["📈 Mileage", "🆘 Help"],
        ],
        resize_keyboard=True
    )

# ── /start ───────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👟 *BYD Marathon 2026 Training Bot*\n\n"
        "I'll help you track your 24-week plan to sub-5hr on Dec 6 🏅\n\n"
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
    await query.edit_message_text(f"✅ *{name}*\n\nMarked complete on {today_str()} 🎉\n\nUse *Upload Activity* to attach a Strava screenshot.", parse_mode="Markdown")

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

async def analyse_with_claude(image_bytes: bytes, session_summary: str, session_desc: str) -> str:
    """Send screenshot to Claude and get run analysis."""
    if not ANTHROPIC_KEY:
        return ""
    b64 = base64.standard_b64encode(image_bytes).decode()
    prompt = (
        f"You are an experienced marathon running coach — direct, knowledgeable, and encouraging but honest. "
        f"The athlete trains in Singapore (year-round heat 28–34°C, humidity 70–90%). "
        f"Heart rates in Singapore will naturally run 5–10 bpm higher than in cool conditions — factor this in when assessing effort vs pace. "
        f"The planned session was: {session_summary}. Coach's notes: {session_desc}\n\n"
        f"Analyse this activity screenshot and respond in exactly this format:\n\n"
        f"Rating: X/10\n\n"
        f"On track: [one sentence comparing actual numbers to the plan target]\n\n"
        f"Well done: [one specific thing the athlete executed well, citing actual numbers]\n\n"
        f"Next time: [one concrete, actionable coaching cue for the next session]\n\n"
        f"Use actual numbers from the screenshot. Sound like a coach, not a chatbot — "
        f"firm, specific, and motivating. No emojis. No filler phrases like 'great job' or 'awesome'."
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

    data.setdefault("logs", {})[uid] = {
        "file_id": file_id,
        "date": today_str(),
        "caption": caption
    }
    save_data(data)
    ctx.user_data.pop("upload_uid", None)

    await update.message.reply_text(
        f"📎 Activity log saved for:\n*{name}*{auto_complete_msg}\n\n🔍 Analysing your run...",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

    # Download photo bytes once — use for both analysis and distance extraction
    tg_file = await update.message.photo[-1].get_file()
    image_bytes = bytes(await tg_file.download_as_bytearray())

    # Run coach analysis
    if ANTHROPIC_KEY:
        try:
            analysis = await analyse_with_claude(image_bytes, name, desc)
            if analysis:
                await update.message.reply_text(
                    f"🤖 *Coach Analysis*\n\n{analysis}",
                    parse_mode="Markdown"
                )
        except Exception as e:
            logger.error(f"Photo analysis error: {e}")

        # Extract distance and ask which shoe + auto-log mileage
        try:
            km = await extract_distance_from_image(image_bytes)
            if km > 0:
                # Auto-log mileage for current week
                log_mileage(data, current_week_num(), km)
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
                    f"📊 *{km:.1f}km logged to this week's mileage.*\n\n👟 *Which shoes did you wear?*",
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

    # Show all sessions of the right type (past and future)
    editable = []
    for s in TRAINING_PLAN:
        uid, dt, summary, desc, thu_flag, tue_flag = s
        if (is_thu and thu_flag) or (not is_thu and tue_flag):
            editable.append(s)

    if not editable:
        await update.message.reply_text(f"No {day_name} sessions found.")
        return

    today = date.today()
    buttons = []
    for s in editable:
        uid, dt, summary, _, _, _ = s
        override = data.get("edits", {}).get(uid, {})
        display = override.get("summary", summary)
        s_date = date.fromisoformat(dt)
        past_marker = "· " if s_date < today else ""
        label = f"{past_marker}{dt}: {display[:32]}…" if len(display) > 32 else f"{past_marker}{dt}: {display}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"edit_pick|{uid}")])

    await update.message.reply_text(
        f"✏️ *Edit {day_name} Sessions*\n\nAll sessions shown — past ones marked with ·",
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
    current = data.get("edits", {}).get(uid, {}).get("summary", s[2] if s else "")

    await query.edit_message_text(
        f"✏️ Editing: *{s[2] if s else uid}*\n\n"
        f"Current description:\n`{current}`\n\n"
        f"Send the new session description (e.g. `8×400m @ 5:30/km`):\n\n"
        f"Or send /cancel to abort.",
        parse_mode="Markdown"
    )
    return EDIT_ENTER_TEXT

async def edit_receive_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = ctx.user_data.get("edit_uid")
    if not uid:
        await update.message.reply_text("Something went wrong. Please try again.")
        return ConversationHandler.END

    new_text = update.message.text.strip()
    data = load_data()
    data.setdefault("edits", {}).setdefault(uid, {})["summary"] = new_text
    save_data(data)

    s = get_session(uid)
    await update.message.reply_text(
        f"✅ Updated!\n\n*{s[1] if s else uid}* is now:\n_{new_text}_",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )
    ctx.user_data.pop("edit_uid", None)
    return ConversationHandler.END

async def edit_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Edit cancelled.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END

# ── Race time predictor ───────────────────────────────────────────────
# Actual Strava best efforts (as of Jun 2026)
BEST_EFFORTS = {
    "400m": "1:48",
    "1K": "5:20",
    "5K": "30:23",
    "10K": "1:05:48",
    "HM": "2:27:45",
}

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
    Estimate HM and FM finish times using Riegel formula from best 10K,
    adjusted for training progression and Singapore heat.
    """
    completed = data.get("completed", {})
    weeks_trained = len(set(re.match(r"(w\d+)_", uid).group(1) for uid in completed if re.match(r"w\d+_", uid))) if completed else 0

    # Base from actual 10K PB: 1:05:48
    base_10k_sec = time_to_sec("1:05:48")

    # Training improvement: ~3 sec/km per 4 weeks consistent training
    # Each completed week contributes a small improvement
    improvement_per_km = min(weeks_trained, 20) * 0.75  # seconds per km
    adjusted_10k_sec = base_10k_sec - (improvement_per_km * 10)

    # Riegel: HM = 10K * (21.1/10)^1.06
    hm_sec = adjusted_10k_sec * (21.1 / 10) ** 1.06

    # Singapore heat penalty on race day: +5 sec/km = +105 sec for HM
    hm_sec_race = hm_sec + 105

    # FM via Riegel from adjusted HM
    fm_sec_race = hm_sec_race * (42.2 / 21.1) ** 1.06

    hm_h = int(hm_sec_race // 3600)
    hm_m = int((hm_sec_race % 3600) // 60)
    fm_h = int(fm_sec_race // 3600)
    fm_m = int((fm_sec_race % 3600) // 60)

    return (hm_h, hm_m), (fm_h, fm_m), weeks_trained


async def show_predictions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    (hm_h, hm_m), (fm_h, fm_m), weeks = predict_race_times(data)
    completed = data.get("completed", {})

    # Conflict warning
    conflict_warning = (
        "\n⚠️ *Schedule conflict detected:*\n"
        "Hyrox (Nov 26) → Pacing HM (Dec 5) → Full Marathon (Dec 6)\n"
        "Consider dropping the pacing duty to protect Dec 6.\n"
    )

    # How close to targets
    hm_target_min = 135  # 2:15
    hm_actual_min = hm_h * 60 + hm_m
    hm_gap = hm_actual_min - hm_target_min
    hm_gap_text = f"{abs(hm_gap)} min {'ahead of' if hm_gap < 0 else 'behind'} 2:15 target" if hm_gap != 0 else "exactly on 2:15 target"

    fm_target_min = 300  # 5:00
    fm_actual_min = fm_h * 60 + fm_m
    fm_gap = fm_actual_min - fm_target_min
    fm_gap_text = f"{abs(fm_gap)} min {'ahead of' if fm_gap < 0 else 'behind'} sub-5hr target" if fm_gap != 0 else "exactly on sub-5hr target"

    text = (
        f"🔮 *Race Time Predictions*\n"
        f"_(based on 10K PB {BEST_EFFORTS['10K']}, {weeks} weeks trained, SG heat adjusted)_\n\n"
        f"*Current PBs:*\n"
        f"5K: {BEST_EFFORTS['5K']} · 10K: {BEST_EFFORTS['10K']} · HM: {BEST_EFFORTS['HM']}\n\n"
        f"🏃 *Half Marathon (21.1km)*\n"
        f"Predicted: *{hm_h}:{hm_m:02d}*\n"
        f"Sep 27 target: 2:15 → {hm_gap_text}\n\n"
        f"🏅 *Full Marathon (42.2km)*\n"
        f"Predicted: *{fm_h}:{fm_m:02d}*\n"
        f"Dec 6 target: sub-5:00 → {fm_gap_text}\n\n"
        f"*Nov 1 HM:* targeting 2:10 — needs strong Sep race + 5 more weeks\n"
        f"{conflict_warning}\n"
        f"_Predictions improve as more sessions are logged and PBs update._"
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

    (hm_h, hm_m), (fm_h, fm_m), weeks = predict_race_times(data)

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
    elif text == "🆘 Help":
        await show_help(update, ctx)
    else:
        await update.message.reply_text(
            "Use the menu buttons below, or /help for commands.",
            reply_markup=main_menu_keyboard()
        )

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
            ]
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
    app.add_handler(CallbackQueryHandler(cb_autogear, pattern=r"^autogear\|"))
    app.add_handler(CallbackQueryHandler(cb_race_remove, pattern=r"^race_remove\|"))

    app.add_handler(CallbackQueryHandler(cb_week, pattern=r"^week\|"))
    app.add_handler(CallbackQueryHandler(cb_toggle, pattern=r"^toggle\|"))
    app.add_handler(CallbackQueryHandler(cb_done, pattern=r"^done\|"))
    app.add_handler(CallbackQueryHandler(cb_markdone, pattern=r"^markdone\|"))
    app.add_handler(CallbackQueryHandler(cb_upload_session, pattern=r"^upload_session\|"))
    app.add_handler(CallbackQueryHandler(cb_back_weeks, pattern=r"^back_weeks$"))

    app.add_handler(MessageHandler(filters.PHOTO, receive_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    logger.info("Bot starting…")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
