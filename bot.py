import os, json, logging
from datetime import date, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

TOKEN = "8727762251:AAG4HRx7CT-8G132mfxdZQRR3cyUxit1_xM"
DATA_FILE = "progress.json"

logging.basicConfig(level=logging.INFO)

# ── PLAN DATA ─────────────────────────────────────────────────────────────────
# Sessions per week: list of {day, type, title, note}
PLAN = {
1:  {"date":"Jun 24","label":"Base build","vol":22,"lr":12,"target":"Fix easy pace. Slow to 7:30+/km — HR under 145 on all easy runs.","sessions":[
    {"day":"Tue","type":"Tempo","title":"Run club tempo — 70min easy @ 7:30/km","note":"Resist the club pace. Run your own easy effort.","editable":True},
    {"day":"Thu","type":"Track","title":"Track club — 8×1km","note":"Target 5:40–5:50/km per rep. Full recovery between reps.","editable":True},
    {"day":"Sat","type":"Long run","title":"Long run 12km @ 7:30–7:45/km","note":"Fix easy pace week.","editable":False}]},
2:  {"date":"Jul 01","label":"Base build","vol":24,"lr":14,"target":"Build long run to 14km. Keep all easy runs truly easy.","sessions":[
    {"day":"Tue","type":"Tempo","title":"Run club tempo — 70min easy @ 7:30/km","note":"Still easy week.","editable":True},
    {"day":"Wed","type":"Easy","title":"Easy 5km @ 7:30/km","note":"","editable":False},
    {"day":"Thu","type":"Track","title":"Track club — 12×300m","note":"Target 1:45–1:50 per rep.","editable":True},
    {"day":"Sat","type":"Long run","title":"Long run 14km @ 7:30/km","note":"Conversational effort throughout.","editable":False}]},
3:  {"date":"Jul 08","label":"Base build","vol":26,"lr":16,"target":"Introduce marathon pace. First taste of 7:00/km mid-week.","sessions":[
    {"day":"Tue","type":"Tempo","title":"Run club — 3×20min tempo @ 6:50/km, 2min jog rest","note":"Participate fully this week.","editable":True},
    {"day":"Wed","type":"Easy","title":"Easy 4km @ 7:45/km","note":"Recovery from Tuesday tempo.","editable":False},
    {"day":"Thu","type":"Track","title":"Track club — 2×2km + 6×200m","note":"2km @ 6:00/km. 200m @ 5:30/km.","editable":True},
    {"day":"Sat","type":"Long run","title":"Long run 16km — last 4km @ 7:05/km","note":"First marathon-pace segment.","editable":False}]},
4:  {"date":"Jul 15","label":"Recovery","vol":20,"lr":12,"target":"Recovery week. Drop volume 20%. Legs adapt here — do not skip the rest.","sessions":[
    {"day":"Tue","type":"Easy","title":"Easy run 6km @ 7:45/km","note":"Skip club tempo this week.","editable":False},
    {"day":"Thu","type":"Track","title":"Track club — 6×300m only (half volume)","note":"Recovery week.","editable":True},
    {"day":"Sat","type":"Long run","title":"Easy long run 12km @ 7:30–8:00/km","note":"No pressure on pace.","editable":False}]},
5:  {"date":"Jul 22","label":"Base build","vol":30,"lr":18,"target":"Long run 18km — new distance milestone. Finish feeling you had more left.","sessions":[
    {"day":"Tue","type":"Tempo","title":"Run club — 70min easy + 2×10min @ 6:50/km","note":"Modified — don't do full 3×20. Big long run Sat.","editable":True},
    {"day":"Wed","type":"Easy","title":"Easy 5km @ 7:45/km","note":"","editable":False},
    {"day":"Thu","type":"Track","title":"Track club — 8×1km @ 5:40/km","note":"","editable":True},
    {"day":"Sat","type":"Long run","title":"Long run 18km @ 7:30/km","note":"Carry water. Plan a looped route.","editable":False}]},
6:  {"date":"Jul 29","label":"Base build","vol":33,"lr":20,"target":"First 20km long run. This is a milestone — complete it at easy effort.","sessions":[
    {"day":"Tue","type":"Tempo","title":"Run club — full session: 70min + 3×20min @ 6:50/km","note":"Good quality week.","editable":True},
    {"day":"Wed","type":"Easy","title":"Easy 5km @ 7:45/km","note":"","editable":False},
    {"day":"Thu","type":"Track","title":"Track club — 12×300m @ 5:45/km","note":"","editable":True},
    {"day":"Sat","type":"Long run","title":"Long run 20km @ 7:30–7:45/km","note":"Bring a gel. Take it at 60min.","editable":False}]},
7:  {"date":"Aug 05","label":"Marathon build","vol":35,"lr":22,"target":"Replace one track with marathon-pace work. Volume creeps up.","sessions":[
    {"day":"Tue","type":"Tempo","title":"Run club — full tempo session","note":"","editable":True},
    {"day":"Wed","type":"MP run","title":"Marathon pace run: 10km @ 7:00–7:05/km","note":"Replaces easy mid-week run.","editable":False},
    {"day":"Thu","type":"Track","title":"Track club — 2×2km + 6×200m","note":"2km @ 6:00/km.","editable":True},
    {"day":"Sat","type":"Long run","title":"Long run 22km @ 7:30/km","note":"Fuel at 45min and 90min.","editable":False}]},
8:  {"date":"Aug 12","label":"Marathon build","vol":38,"lr":24,"target":"Long run 24km. Biggest week yet.","sessions":[
    {"day":"Tue","type":"Tempo","title":"Run club — 3×20min tempo @ 6:50/km","note":"","editable":True},
    {"day":"Wed","type":"MP run","title":"Marathon pace run: 12km @ 7:00/km","note":"","editable":False},
    {"day":"Thu","type":"Track","title":"Track club — 8×1km @ 5:40/km","note":"","editable":True},
    {"day":"Sat","type":"Long run","title":"Long run 24km @ 7:20–7:30/km","note":"Last 4km push to 7:00/km if feeling good.","editable":False}]},
9:  {"date":"Aug 19","label":"Recovery","vol":28,"lr":16,"target":"Recovery week. Sleep 8hrs. Protect your legs.","sessions":[
    {"day":"Tue","type":"Easy","title":"Easy run with club — 50min @ 7:30/km only","note":"Skip or shorten the tempo portion.","editable":False},
    {"day":"Wed","type":"Easy","title":"Easy 5km @ 7:45/km","note":"","editable":False},
    {"day":"Thu","type":"Track","title":"Track club — 6×300m only (half volume)","note":"","editable":True},
    {"day":"Sat","type":"Long run","title":"Easy 16km @ 7:45/km","note":"No pace pressure.","editable":False}]},
10: {"date":"Aug 26","label":"Marathon build","vol":40,"lr":26,"target":"Long run 26km — must carry gels and water.","sessions":[
    {"day":"Tue","type":"Tempo","title":"Run club — full tempo: 3×20min @ 6:50/km","note":"","editable":True},
    {"day":"Wed","type":"MP run","title":"Marathon pace run: 12km @ 7:00/km","note":"","editable":False},
    {"day":"Thu","type":"Track","title":"Track club — 8×1km @ 5:40/km","note":"","editable":True},
    {"day":"Sat","type":"Long run","title":"Long run 26km @ 7:30/km","note":"Gel at 45, 90, 135min. Negative split.","editable":False}]},
11: {"date":"Sep 02","label":"Marathon build","vol":42,"lr":28,"target":"28km long run. Race-day fuelling rehearsal.","sessions":[
    {"day":"Tue","type":"Tempo","title":"Run club — 70min + 2×20min @ 6:50/km","note":"Slightly reduced.","editable":True},
    {"day":"Wed","type":"MP run","title":"Marathon pace: 10km @ 7:00/km","note":"","editable":False},
    {"day":"Thu","type":"Track","title":"Track club — 12×300m @ 5:45/km","note":"","editable":True},
    {"day":"Sat","type":"Long run","title":"Long run 28km @ 7:30/km","note":"Gel every 45min.","editable":False}]},
12: {"date":"Sep 09","label":"Pre-race taper","vol":30,"lr":16,"target":"Taper for Sep 27 HM. Cut easy volume, keep sharpness.","sessions":[
    {"day":"Tue","type":"Tempo","title":"Run club — 70min + 1×20min tempo @ 6:50/km","note":"Reduce to 1 tempo rep only.","editable":True},
    {"day":"Wed","type":"Easy","title":"Easy 6km @ 7:30/km","note":"","editable":False},
    {"day":"Thu","type":"Track","title":"Track club — 6×1km @ 5:40/km (half volume)","note":"","editable":True},
    {"day":"Sat","type":"Long run","title":"Easy 16km @ 7:30/km","note":"","editable":False}]},
13: {"date":"Sep 16","label":"Pre-race taper","vol":22,"lr":12,"target":"Final taper for Sep HM. Fresh legs by Friday.","sessions":[
    {"day":"Tue","type":"Easy","title":"Easy 6km @ 7:30/km — skip club tempo","note":"","editable":False},
    {"day":"Thu","type":"Track","title":"Track club — 4×300m + 4×200m strides only","note":"","editable":True},
    {"day":"Sat","type":"Easy","title":"Easy shakeout 4km + 4 strides","note":"","editable":False}]},
14: {"date":"Sep 23","label":"RACE WEEK","vol":15,"lr":None,"target":"Race Sep 27 HM — target sub-2:15. Start @ 6:25/km.","sessions":[
    {"day":"Tue","type":"Easy","title":"Easy 4km shakeout @ 7:30/km","note":"","editable":False},
    {"day":"Sat","type":"🏁 RACE","title":"RACE: Sep 27 Half Marathon","note":"Target sub-2:15. Negative split.","editable":False}]},
15: {"date":"Sep 30","label":"Recovery","vol":20,"lr":10,"target":"Full recovery week post HM. Easy jogs only.","sessions":[
    {"day":"Tue","type":"Easy","title":"Easy 5km @ 7:45–8:00/km","note":"Skip club tempo.","editable":False},
    {"day":"Thu","type":"Easy","title":"Easy 5km @ 7:45/km — skip track","note":"","editable":False},
    {"day":"Sat","type":"Easy","title":"Easy 10km @ 7:45/km","note":"","editable":False}]},
16: {"date":"Oct 07","label":"Peak build","vol":42,"lr":28,"target":"Back to full training. Long run 28km.","sessions":[
    {"day":"Tue","type":"Tempo","title":"Run club — full tempo: 3×20min @ 6:50/km","note":"","editable":True},
    {"day":"Wed","type":"MP run","title":"Marathon pace: 12km @ 7:00/km","note":"","editable":False},
    {"day":"Thu","type":"Track","title":"Track club — 8×1km @ 5:40/km","note":"","editable":True},
    {"day":"Sat","type":"Long run","title":"Long run 28km @ 7:20/km","note":"Race-day shoes and gels.","editable":False}]},
17: {"date":"Oct 14","label":"Peak build","vol":48,"lr":30,"target":"30km marathon simulation. Treat it like the real thing.","sessions":[
    {"day":"Tue","type":"Tempo","title":"Run club — 3×20min @ 6:50/km","note":"","editable":True},
    {"day":"Wed","type":"MP run","title":"Marathon pace: 14km @ 7:00/km","note":"Longest MP run in plan.","editable":False},
    {"day":"Thu","type":"Track","title":"Track club — 2×2km + 6×200m","note":"","editable":True},
    {"day":"Sat","type":"Long run","title":"Long run 30km @ 7:15–7:20/km","note":"Race-day kit. Gel at 45, 90, 135min.","editable":False}]},
18: {"date":"Oct 21","label":"Peak build","vol":50,"lr":32,"target":"Peak week. 32km long run. Hardest week of the plan.","sessions":[
    {"day":"Tue","type":"Tempo","title":"Run club — 2×20min @ 6:50/km only","note":"Reduce — big 32km Saturday.","editable":True},
    {"day":"Wed","type":"MP run","title":"Marathon pace: 10km @ 7:00/km","note":"","editable":False},
    {"day":"Thu","type":"Track","title":"Track club — 8×1km @ 5:40/km","note":"","editable":True},
    {"day":"Sat","type":"Long run","title":"Long run 32km @ 7:20/km","note":"PEAK RUN. Gel at 45, 90, 135, 165min.","editable":False}]},
19: {"date":"Oct 28","label":"RACE WEEK","vol":18,"lr":None,"target":"Nov 1 HM — run at marathon effort 7:00/km. Do NOT race.","sessions":[
    {"day":"Tue","type":"Easy","title":"Easy 5km @ 7:45/km — skip club","note":"","editable":False},
    {"day":"Thu","type":"Easy","title":"Easy 5km + strides — skip track","note":"","editable":False},
    {"day":"Sat","type":"🏁 RACE","title":"RACE: Nov 1 Half Marathon (marathon effort)","note":"Target ~2:28. This is a training run with a bib.","editable":False}]},
20: {"date":"Nov 04","label":"Recovery","vol":25,"lr":14,"target":"Easy week post Nov HM. Your peak work is done.","sessions":[
    {"day":"Tue","type":"Easy","title":"Easy 5km @ 7:45/km — skip club","note":"","editable":False},
    {"day":"Wed","type":"Easy","title":"Easy 6km @ 7:45/km","note":"","editable":False},
    {"day":"Thu","type":"Easy","title":"Easy 5km or 4×300m easy","note":"","editable":False},
    {"day":"Sat","type":"Easy","title":"Easy 14km @ 7:45/km","note":"","editable":False}]},
21: {"date":"Nov 11","label":"Taper begins","vol":42,"lr":26,"target":"Bring forward peak long run to 26km — must be done BEFORE Hyrox Nov 26. Last real quality week.","sessions":[
    {"day":"Tue","type":"Tempo","title":"Run club — 2×20min @ 6:50/km only","note":"Last quality club session.","editable":True},
    {"day":"Wed","type":"MP run","title":"Marathon pace: 10km @ 7:00/km","note":"Last MP run of the plan. Should feel controlled and comfortable.","editable":False},
    {"day":"Thu","type":"Track","title":"Track club — 6×1km @ 5:40/km","note":"Last track session. Controlled — not a time trial.","editable":True},
    {"day":"Sat","type":"Long run","title":"Long run 26km @ 7:20/km","note":"IMPORTANT: peak long run moved here before Hyrox. Race-day shoes, gel every 45min.","editable":False}]},
22: {"date":"Nov 18","label":"Pre-Hyrox taper","vol":22,"lr":12,"target":"Hard taper — arrive at Hyrox Nov 26 FRESH. Cut volume aggressively this week.","sessions":[
    {"day":"Tue","type":"Tempo","title":"Run club — 70min easy @ 7:30/km only","note":"No tempo reps this week. Just easy running.","editable":True},
    {"day":"Wed","type":"Easy","title":"Easy 5km @ 7:30/km","note":"","editable":False},
    {"day":"Thu","type":"Track","title":"Track club — 4×300m easy strides only","note":"Show face but do very little. Save legs for Hyrox.","editable":True},
    {"day":"Sat","type":"Easy","title":"Easy 12km @ 7:30/km","note":"Last run before Hyrox week. Keep it easy and controlled.","editable":False}]},
23: {"date":"Nov 25","label":"🏋️ HYROX WEEK","vol":10,"lr":None,"target":"Hyrox Women's Doubles on Nov 26. Minimal running. Arrive fresh. Race hard.","sessions":[
    {"day":"Mon","type":"Easy","title":"Easy 4km @ 7:30/km","note":"Only run of the week before Hyrox.","editable":False},
    {"day":"Wed","type":"Easy","title":"Easy 3km shakeout + 4 strides","note":"Just to keep legs ticking. Nothing more.","editable":False},
    {"day":"Thu","type":"Rest","title":"Full rest — no track club this week","note":"Save everything for Hyrox tomorrow.","editable":False},
    {"day":"Wed","type":"🏋️ HYROX","title":"Hyrox Women's Doubles 🏋️","note":"Race it! Recover aggressively after — eat, hydrate, sleep. 10 days to marathon.","editable":False}]},
24: {"date":"Dec 02","label":"RACE WEEK 🏁","vol":8,"lr":None,"target":"Post-Hyrox recovery + marathon prep. Drop Dec 5 pacing if possible — protect Dec 6.","sessions":[
    {"day":"Mon","type":"Rest","title":"Full rest — Hyrox recovery","note":"Legs need 5–7 days after Hyrox. Do not run.","editable":False},
    {"day":"Tue","type":"Rest","title":"Full rest — Hyrox recovery","note":"","editable":False},
    {"day":"Wed","type":"Rest","title":"Full rest — Hyrox recovery","note":"","editable":False},
    {"day":"Thu","type":"Easy","title":"Easy 3km shakeout only","note":"First run post-Hyrox. Legs should feel alive again. If not, skip it.","editable":False},
    {"day":"Fri","type":"Rest","title":"Rest — pack race bags","note":"Early night. Prepare nutrition and kit for both days.","editable":False},
    {"day":"Sat","type":"🏁 PACE","title":"⚠️ Dec 5 pacing duty — consider dropping","note":"If pacing: run 7:50/km, walk ALL uphills, eat 2 gels, hydrate heavily after. Sleep by 9pm. If dropped: 2km easy walk only.","editable":False},
    {"day":"Sun","type":"🏁 RACE","title":"RACE DAY: BYD Full Marathon — Sub-5hr 🏁","note":"Start 7:15/km for first 10km. Settle into 7:05 from 10–30km. Hold on — you've earned this.","editable":False}]},
}

TYPE_EMOJI = {
    "Tempo":"🟢", "Track":"🟣", "Long run":"🔵", "Easy":"⚪",
    "MP run":"🟠", "🏁 RACE":"🏁", "🏁 PACE":"🏁", "Recovery":"⚪",
    "Rest":"😴", "🏋️ HYROX":"🏋️"
}

# ── DATA PERSISTENCE ──────────────────────────────────────────────────────────
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    return {"completed": {}, "photos": {}, "edits": {}, "notes": {}}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ── HELPERS ───────────────────────────────────────────────────────────────────
def current_week():
    start = date(2026, 6, 22)  # Monday of week 1
    today = date.today()
    diff = (today - start).days
    w = diff // 7 + 1
    return max(1, min(24, w))

def session_key(week, idx):
    return f"{week}_{idx}"

def week_summary(week, data):
    wk = PLAN[week]
    sessions = get_sessions(week, data)
    done = sum(1 for i in range(len(sessions)) if data["completed"].get(session_key(week, i)))
    total = len(sessions)
    bar = "█" * done + "░" * (total - done)
    pct = int(done/total*100) if total else 0

    race_flag = "🏁 " if "RACE" in wk["label"] else ""
    text = (
        f"*{race_flag}Week {week} — {wk['date']} — {wk['label']}*\n"
        f"📦 Volume: {wk['vol']}km"
        + (f" | Long run: {wk['lr']}km" if wk['lr'] else "") + "\n"
        f"🎯 {wk['target']}\n\n"
        f"Progress: {bar} {done}/{total} ({pct}%)\n"
    )
    return text

def get_sessions(week, data):
    """Return sessions with any edits applied."""
    sessions = []
    for i, s in enumerate(PLAN[week]["sessions"]):
        key = session_key(week, i)
        edited = data["edits"].get(key)
        if edited:
            s = dict(s)
            s["title"] = edited
        sessions.append(s)
    return sessions

# ── STATES ────────────────────────────────────────────────────────────────────
EDIT_WEEK, EDIT_IDX, EDIT_TEXT = range(3)
PHOTO_WEEK, PHOTO_IDX = range(2)

# ── COMMAND HANDLERS ──────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👟 *Yitian's Marathon Tracker*\n\n"
        "24 weeks to BYD Singapore Marathon — Dec 6, 2026 🏁\n\n"
        "Commands:\n"
        "/week — This week's sessions\n"
        "/week [n] — Any week (e.g. /week 3)\n"
        "/today — Today's session\n"
        "/progress — Overall progress\n"
        "/done [n] — Mark session done in current week\n"
        "/edit — Edit a Tue/Thu session\n"
        "/log — Upload activity screenshot\n"
        "/paces — Your target paces\n"
        "/help — Show this menu",
        parse_mode="Markdown"
    )

async def paces(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📏 *Your Target Paces*\n\n"
        "⚪ Easy / recovery: `7:30–8:00 /km`\n"
        "🔵 Long run: `7:15–7:45 /km`\n"
        "🟠 Marathon pace: `7:00–7:05 /km`\n"
        "🟢 Tempo: `6:40–6:50 /km`\n"
        "🟣 Track reps: `5:30–5:50 /km`\n"
        "🏁 Dec 5 pacing duty: `7:50 /km`\n\n"
        "Sub-5hr = 7:06/km race pace",
        parse_mode="Markdown"
    )

async def show_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    args = ctx.args
    if args and args[0].isdigit():
        week = int(args[0])
        if not 1 <= week <= 24:
            await update.message.reply_text("Week must be between 1 and 24.")
            return
    else:
        week = current_week()

    wk = PLAN[week]
    sessions = get_sessions(week, data)
    text = week_summary(week, data)
    text += "\n*Sessions:*\n"
    for i, s in enumerate(sessions):
        done = data["completed"].get(session_key(week, i), False)
        emoji = TYPE_EMOJI.get(s["type"], "▪️")
        tick = "✅" if done else "⬜"
        edit_flag = " ✏️" if s.get("editable") else ""
        text += f"\n{tick} `[{i+1}]` {emoji} *{s['day']}* — {s['title']}{edit_flag}"
        if s["note"]:
            text += f"\n      _{s['note']}_"

    text += f"\n\n✏️ = editable session (Tue/Thu)\nUse /done [n] to mark complete\nUse /edit to update Tue or Thu session"

    # Nav buttons
    buttons = []
    if week > 1:
        buttons.append(InlineKeyboardButton("◀ Prev", callback_data=f"week_{week-1}"))
    buttons.append(InlineKeyboardButton(f"W{week}", callback_data="noop"))
    if week < 24:
        buttons.append(InlineKeyboardButton("Next ▶", callback_data=f"week_{week+1}"))

    markup = InlineKeyboardMarkup([buttons])
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)

async def nav_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "noop":
        return
    week = int(query.data.split("_")[1])
    data = load_data()
    wk = PLAN[week]
    sessions = get_sessions(week, data)
    text = week_summary(week, data)
    text += "\n*Sessions:*\n"
    for i, s in enumerate(sessions):
        done = data["completed"].get(session_key(week, i), False)
        emoji = TYPE_EMOJI.get(s["type"], "▪️")
        tick = "✅" if done else "⬜"
        edit_flag = " ✏️" if s.get("editable") else ""
        text += f"\n{tick} `[{i+1}]` {emoji} *{s['day']}* — {s['title']}{edit_flag}"
        if s["note"]:
            text += f"\n      _{s['note']}_"
    text += f"\n\n✏️ = editable (Tue/Thu) | /done [n] | /edit"
    buttons = []
    if week > 1:
        buttons.append(InlineKeyboardButton("◀ Prev", callback_data=f"week_{week-1}"))
    buttons.append(InlineKeyboardButton(f"W{week}", callback_data="noop"))
    if week < 24:
        buttons.append(InlineKeyboardButton("Next ▶", callback_data=f"week_{week+1}"))
    markup = InlineKeyboardMarkup([buttons])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)

async def today_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    week = current_week()
    today_name = date.today().strftime("%a")  # Mon, Tue, Wed...
    sessions = get_sessions(week, data)
    matches = [(i, s) for i, s in enumerate(sessions) if s["day"] == today_name]

    if not matches:
        await update.message.reply_text(
            f"*Week {week} — {PLAN[week]['date']}*\n\nNo session scheduled for today ({today_name}). Rest up! 💤",
            parse_mode="Markdown"
        )
        return

    text = f"*Today's session — Week {week} ({today_name}):*\n"
    for i, s in matches:
        done = data["completed"].get(session_key(week, i), False)
        emoji = TYPE_EMOJI.get(s["type"], "▪️")
        tick = "✅ Done!" if done else "⬜ Not yet"
        text += f"\n{emoji} *{s['type']}* [{i+1}]\n{s['title']}\n"
        if s["note"]:
            text += f"_{s['note']}_\n"
        text += f"Status: {tick}\n"
    text += f"\n🎯 Week target: {PLAN[week]['target']}"
    if not all(data["completed"].get(session_key(week, i)) for i, _ in matches):
        text += "\n\nUse /done [n] to mark complete, /log to upload your Strava screenshot."
    await update.message.reply_text(text, parse_mode="Markdown")

async def done_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    week = current_week()
    if not ctx.args or not ctx.args[0].isdigit():
        await update.message.reply_text("Usage: /done [session number]\nE.g. /done 2\n\nCheck /week to see session numbers.")
        return
    idx = int(ctx.args[0]) - 1
    sessions = get_sessions(week, data)
    if idx < 0 or idx >= len(sessions):
        await update.message.reply_text(f"Session number must be between 1 and {len(sessions)}.")
        return
    key = session_key(week, idx)
    was_done = data["completed"].get(key, False)
    data["completed"][key] = not was_done
    save_data(data)
    s = sessions[idx]
    status = "✅ Marked complete!" if not was_done else "⬜ Unmarked."
    await update.message.reply_text(
        f"{status}\n\n*{s['day']} — {s['title']}*\n\n"
        f"Don't forget to /log your Strava screenshot! 📸",
        parse_mode="Markdown"
    )

async def progress_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    cur = current_week()
    total_sessions = 0
    done_sessions = 0
    text = "*📊 Overall Progress*\n\n"
    for w in range(1, cur + 1):
        sessions = get_sessions(w, data)
        n = len(sessions)
        d = sum(1 for i in range(n) if data["completed"].get(session_key(w, i)))
        total_sessions += n
        done_sessions += d
        bar = "█" * d + "░" * (n - d)
        label = PLAN[w]["label"][:12]
        text += f"W{w:02d} {bar} {d}/{n} _{label}_\n"

    pct = int(done_sessions / total_sessions * 100) if total_sessions else 0
    text += f"\n*Total: {done_sessions}/{total_sessions} sessions ({pct}%)*\n"
    text += f"Currently on Week {cur}/24\n"
    weeks_left = 24 - cur
    text += f"{weeks_left} weeks to race day 🏁"
    await update.message.reply_text(text, parse_mode="Markdown")

# ── EDIT CONVERSATION ─────────────────────────────────────────────────────────
async def edit_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    week = current_week()
    ctx.user_data["edit_week"] = week
    data = load_data()
    sessions = get_sessions(week, data)
    editable = [(i, s) for i, s in enumerate(sessions) if s.get("editable")]
    if not editable:
        await update.message.reply_text("No editable sessions (Tue/Thu) this week.")
        return ConversationHandler.END

    text = f"*Edit Week {week} sessions*\nWhich session to update?\n\n"
    buttons = []
    for i, s in editable:
        text += f"`[{i+1}]` {s['day']} — {s['title']}\n"
        buttons.append([InlineKeyboardButton(f"{s['day']} [{i+1}]", callback_data=f"editidx_{i}")])

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
    return EDIT_IDX

async def edit_pick_idx(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    ctx.user_data["edit_idx"] = idx
    week = ctx.user_data["edit_week"]
    data = load_data()
    sessions = get_sessions(week, data)
    s = sessions[idx]
    await query.edit_message_text(
        f"Editing: *{s['day']}* — _{s['title']}_\n\nType the new session description:",
        parse_mode="Markdown"
    )
    return EDIT_TEXT

async def edit_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    new_text = update.message.text.strip()
    week = ctx.user_data["edit_week"]
    idx = ctx.user_data["edit_idx"]
    data = load_data()
    data["edits"][session_key(week, idx)] = new_text
    save_data(data)
    sessions = get_sessions(week, data)
    s = sessions[idx]
    await update.message.reply_text(
        f"✅ Updated!\n\n*{s['day']}* — {new_text}",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def edit_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Edit cancelled.")
    return ConversationHandler.END

# ── PHOTO LOG ─────────────────────────────────────────────────────────────────
async def log_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    week = current_week()
    ctx.user_data["log_week"] = week
    data = load_data()
    sessions = get_sessions(week, data)
    text = f"*Log activity for Week {week}*\nWhich session?\n\n"
    buttons = []
    for i, s in enumerate(sessions):
        done = "✅" if data["completed"].get(session_key(week, i)) else "⬜"
        buttons.append([InlineKeyboardButton(f"{done} {s['day']} — {s['type']}", callback_data=f"logidx_{i}")])

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
    return PHOTO_IDX

async def log_pick_idx(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    ctx.user_data["log_idx"] = idx
    week = ctx.user_data["log_week"]
    data = load_data()
    sessions = get_sessions(week, data)
    s = sessions[idx]
    await query.edit_message_text(
        f"📸 Send your Strava screenshot for:\n*{s['day']} — {s['title']}*\n\n"
        f"(Upload the photo now)",
        parse_mode="Markdown"
    )
    return PHOTO_WEEK

async def log_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    week = ctx.user_data.get("log_week", current_week())
    idx = ctx.user_data.get("log_idx", 0)
    data = load_data()
    photo = update.message.photo[-1]
    key = session_key(week, idx)
    if "photos" not in data:
        data["photos"] = {}
    data["photos"][key] = photo.file_id
    data["completed"][key] = True
    save_data(data)
    sessions = get_sessions(week, data)
    s = sessions[idx]
    await update.message.reply_text(
        f"✅ Logged and marked complete!\n\n"
        f"*{s['day']} — {s['title']}*\n\n"
        f"Great work! Keep the consistency going 💪",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def log_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Log cancelled.")
    return ConversationHandler.END

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await start(update, ctx)

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TOKEN).build()

    # Edit conversation
    edit_conv = ConversationHandler(
        entry_points=[CommandHandler("edit", edit_start)],
        states={
            EDIT_IDX:  [CallbackQueryHandler(edit_pick_idx, pattern="^editidx_")],
            EDIT_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_save)],
        },
        fallbacks=[CommandHandler("cancel", edit_cancel)],
    )

    # Log conversation
    log_conv = ConversationHandler(
        entry_points=[CommandHandler("log", log_start)],
        states={
            PHOTO_IDX:  [CallbackQueryHandler(log_pick_idx, pattern="^logidx_")],
            PHOTO_WEEK: [MessageHandler(filters.PHOTO, log_photo)],
        },
        fallbacks=[CommandHandler("cancel", log_cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("week", show_week))
    app.add_handler(CommandHandler("today", today_cmd))
    app.add_handler(CommandHandler("done", done_cmd))
    app.add_handler(CommandHandler("progress", progress_cmd))
    app.add_handler(CommandHandler("paces", paces))
    app.add_handler(CallbackQueryHandler(nav_week, pattern="^week_"))
    app.add_handler(CallbackQueryHandler(nav_week, pattern="^noop$"))
    app.add_handler(edit_conv)
    app.add_handler(log_conv)

    print("🏃 Marathon bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
