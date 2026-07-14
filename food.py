"""
food.py — Food & Nutrition logging add-on for @YitianMarathonBot

Self-contained module. Reuses the SAME data.json store as bot.py (safe
read-merge-write on the "food_logs" key only, so it won't clobber the
training data bot.py owns).

── Wiring it into bot.py ──────────────────────────────────────────────
1. At the top of bot.py, add:
       import food

2. In main_menu_keyboard(), add a new row, e.g.:
       ["🍽️ Log Food", "📊 Nutrition"],

3. In text_router(), add:
       elif text == "🍽️ Log Food":
           return await food.food_menu(update, ctx)
       elif text == "📊 Nutrition":
           return await food.show_today(update, ctx)

4. In main(), before app.run_polling():
       food.register(app)

That's it — no changes needed to bot.py's data model or existing handlers.
────────────────────────────────────────────────────────────────────────
"""

import os
import re
import json
import base64
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, CallbackQueryHandler,
    MessageHandler, CommandHandler, filters
)

logger = logging.getLogger(__name__)

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DATA_DIR = Path("/app/data")
DATA_FILE = DATA_DIR / "data.json"

# ── Targets — from the TDEE/deficit calc for 63kg / 164cm / 25F.
#    REST_DAY_MAINTENANCE is the ~1780kcal baseline (BMR × activity factor,
#    no training). The actual daily target is computed dynamically in
#    compute_daily_target() below, using whatever's scheduled on that date
#    in bot.py's TRAINING_PLAN — deficit shrinks/vanishes on hard days,
#    stays fuller on rest days it's smaller so the WEEKLY total nets out
#    to a sustainable deficit rather than flat-cutting every day equally. ──
REST_DAY_MAINTENANCE = 1780
NORMAL_DAY_DEFICIT = (250, 350)   # (tighter, looser) kcal below maintenance
RUN_KCAL_PER_KM = 58              # rough burn estimate at ~63kg
HYROX_KCAL = 500
DAILY_PROTEIN_TARGET_G = 100  # ~1.6g/kg bodyweight, reasonable for a runner


# Tag priority (most demanding first) — used when a day has multiple sessions
_TAG_PRIORITY = ["RACE", "PACE", "HYROX", "LONG RUN", "MP RUN", "TEMPO", "TRACK", "EASY"]


def _today_sessions() -> list:
    """Pull today's scheduled session(s) from bot.py's TRAINING_PLAN, if any.
    Returns list of (summary, desc). Lazy import avoids a circular import
    (bot.py imports food.py at load time)."""
    try:
        import bot as _bot
    except ImportError:
        return []
    today = date.today().isoformat()
    return [(s[2], s[3]) for s in _bot.TRAINING_PLAN if s[1] == today]


def compute_daily_target() -> tuple:
    """Returns (low, high, label) for today's calorie target, adjusted for
    whatever's on the training plan today, PLUS any extra gym/strength/cardio
    logged manually via the Extra Exercise buttons (not in TRAINING_PLAN).
    Parses the [Tag] bracket from each session's SUMMARY only (never the
    free-text coach notes, which can contain misleading substrings like
    'skip club tempo')."""
    today = date.today().isoformat()
    extra_burn = _get_extra_exercise(today)
    sessions = _today_sessions()

    if not sessions:
        lo_def, hi_def = NORMAL_DAY_DEFICIT
        lo = REST_DAY_MAINTENANCE - hi_def + extra_burn
        hi = REST_DAY_MAINTENANCE - lo_def + extra_burn
        label = "Rest day" + (" + gym" if extra_burn else "")
        return _round25(lo), _round25(hi), label

    burn = 0.0
    tags_found = []
    total_km = 0.0

    for summary, desc in sessions:
        tag_match = re.search(r'\[(.*?)\]', summary)
        tag = tag_match.group(1).upper() if tag_match else ""
        tags_found.append(tag)

        upper_summary = summary.upper()
        if "HYROX" in tag or "HYROX" in upper_summary:
            burn += HYROX_KCAL

        km_match = re.search(r'(\d+(?:\.\d+)?)\s*km', summary)  # summary first
        if not km_match:
            km_match = re.search(r'(\d+(?:\.\d+)?)\s*km', desc)  # fallback
        if km_match:
            dist = float(km_match.group(1))
            total_km += dist
            burn += dist * RUN_KCAL_PER_KM

    burn += extra_burn
    extra_suffix = " + gym" if extra_burn else ""
    upper_all = " ".join(tags_found) + " " + " ".join(s for s, _ in sessions).upper()

    # No-deficit days: race day, pacing duty — full maintenance + burn
    if "RACE" in upper_all or "PACE" in upper_all:
        label = ("🏁 Race/pace day — fuel fully, no deficit" if "RACE" in upper_all else "Pacing duty — fuel fully, no deficit") + extra_suffix
        target = REST_DAY_MAINTENANCE + burn
        return _round25(target - 50), _round25(target + 100), label

    # Pick the most demanding tag present, for the label
    label = "Training day"
    for candidate in _TAG_PRIORITY:
        if any(candidate in t for t in tags_found):
            pretty = candidate.lower().capitalize()
            label = f"{pretty} day ({total_km:.0f}km)" if total_km and candidate in ("LONG RUN", "EASY") else f"{pretty} day"
            break
    if "HYROX" in upper_all:
        label = "Hyrox day" + (f" + {total_km:.0f}km" if total_km else "")
    label += extra_suffix

    lo_def, hi_def = NORMAL_DAY_DEFICIT
    lo = REST_DAY_MAINTENANCE - hi_def + burn
    hi = REST_DAY_MAINTENANCE - lo_def + burn
    return _round25(lo), _round25(hi), label


def _round25(n: float) -> int:
    return int(round(n / 25) * 25)

# ── Conversation states ──────────────────────────────────────────────
FOOD_AWAIT_INPUT = 300
FOOD_AWAIT_EDIT = 301


# ── Shared-file data helpers (safe merge — never touches other keys) ──
def _load() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return {}


def _save(data: dict):
    DATA_FILE.write_text(json.dumps(data, indent=2))


def _append_log(entry: dict):
    data = _load()
    data.setdefault("food_logs", []).append(entry)
    _save(data)


def _logs_for_day(day: str) -> list:
    data = _load()
    return [l for l in data.get("food_logs", []) if l.get("date") == day]


# ── Extra exercise (gym/strength/cardio not in TRAINING_PLAN) ─────────
EXTRA_EXERCISE_KCAL = {"light": 150, "moderate": 300, "hard": 450}
EXTRA_EXERCISE_LABEL = {"light": "light session (~30min)", "moderate": "moderate session (~45-60min)", "hard": "hard/long session (~60-90min)"}


def _add_extra_exercise(day: str, kcal: int):
    data = _load()
    data.setdefault("exercise_extra", {})
    data["exercise_extra"][day] = data["exercise_extra"].get(day, 0) + kcal
    _save(data)


def _get_extra_exercise(day: str) -> int:
    data = _load()
    return data.get("exercise_extra", {}).get(day, 0)


def _reset_extra_exercise(day: str):
    data = _load()
    data.setdefault("exercise_extra", {})[day] = 0
    _save(data)


def _totals(logs: list) -> dict:
    t = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}
    for l in logs:
        t["calories"] += l.get("calories", 0) or 0
        t["protein"] += l.get("protein", 0) or 0
        t["carbs"] += l.get("carbs", 0) or 0
        t["fat"] += l.get("fat", 0) or 0
    return t


# ── Claude estimation prompt — Singapore-aware ───────────────────────
_SG_CONTEXT = (
    "The user is based in Singapore, so food is very likely to include local "
    "hawker dishes (e.g. chicken rice, laksa, mee goreng, roti prata, char "
    "kway teow, nasi lemak, bak kut teh, wanton noodles, kaya toast, "
    "chwee kueh), kopitiam/cafe items, or Singapore-market packaged/branded "
    "products (check for a visible nutrition label first if it's a photo of "
    "packaging). Calibrate local dishes against known Singapore references: "
    "the Health Promotion Board's Singapore Food Insights Database (energy "
    "and nutrient composition of food commonly eaten in Singapore) and "
    "general hawker-food calorie references (e.g. chicken rice plate "
    "~700kcal, laksa ~590kcal, roti prata plain ~120kcal per piece, mee "
    "goreng ~660kcal, kopi-o ~20kcal, kopi with milk & sugar ~60kcal). "
    "These are reference anchors, not the only source — use general "
    "nutrition knowledge for anything non-local (Western food, other Asian "
    "cuisines, branded packaged snacks, home-cooked meals, etc.), and if a "
    "nutrition label is visible in a photo, prefer the label's numbers over "
    "an estimate."
)

_JSON_INSTRUCTIONS = (
    "Respond with ONLY a raw JSON object, no markdown fences, no preamble, "
    "no explanation outside the JSON. Use this exact schema:\n"
    "{\n"
    '  "food_name": "short description of what was eaten",\n'
    '  "portion": "estimated portion/serving size",\n'
    '  "calories": <integer kcal>,\n'
    '  "protein": <integer grams>,\n'
    '  "carbs": <integer grams>,\n'
    '  "fat": <integer grams>,\n'
    '  "confidence": "high" | "medium" | "low",\n'
    '  "notes": "one short sentence — e.g. flag if this is a rough estimate, '
    'or a heads up if it looks high-sodium/high-sugar for someone marathon '
    'training. Keep it factual, not preachy."\n'
    "}\n"
    "If you genuinely cannot identify the food, set food_name to \"unclear\" "
    "and confidence to \"low\" rather than guessing wildly."
)


def _parse_json_response(text: str) -> dict | None:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    try:
        obj = json.loads(text)
        for k in ("calories", "protein", "carbs", "fat"):
            obj[k] = int(round(float(obj.get(k, 0) or 0)))
        obj.setdefault("food_name", "Unknown food")
        obj.setdefault("portion", "")
        obj.setdefault("confidence", "medium")
        obj.setdefault("notes", "")
        return obj
    except Exception as e:
        logger.error(f"Food JSON parse failed: {e} | raw: {text[:200]}")
        return None


async def estimate_food_from_image(image_bytes: bytes) -> dict | None:
    if not ANTHROPIC_KEY:
        return None
    b64 = base64.standard_b64encode(image_bytes).decode()
    prompt = (
        f"Identify the food/drink in this photo and estimate its nutrition. "
        f"{_SG_CONTEXT}\n\n{_JSON_INSTRUCTIONS}"
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
        text = resp.json()["content"][0]["text"]
        return _parse_json_response(text)
    except Exception as e:
        logger.error(f"Food image estimation failed: {e}")
        return None


async def estimate_food_from_text(description: str) -> dict | None:
    if not ANTHROPIC_KEY:
        return None
    prompt = (
        f'Estimate the nutrition for this food/drink description: "{description}". '
        f"If it names a specific branded product (e.g. a packaged snack sold in "
        f"Singapore), use your knowledge of that product's actual nutrition label "
        f"where possible rather than a generic estimate. "
        f"{_SG_CONTEXT}\n\n{_JSON_INSTRUCTIONS}"
    )
    try:
        async with httpx.AsyncClient(timeout=20) as client:
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
                    "messages": [{"role": "user", "content": prompt}]
                }
            )
        text = resp.json()["content"][0]["text"]
        return _parse_json_response(text)
    except Exception as e:
        logger.error(f"Food text estimation failed: {e}")
        return None


# ── UI: menu ──────────────────────────────────────────────────────────
def _food_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📷 Log via Photo", callback_data="food|photo"),
         InlineKeyboardButton("✍️ Log via Text", callback_data="food|text")],
        [InlineKeyboardButton("🏋️ Extra Exercise", callback_data="exercise_menu")],
        [InlineKeyboardButton("📊 Today's Totals", callback_data="food|today"),
         InlineKeyboardButton("📅 This Week", callback_data="food|week")],
    ])


async def food_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🍽️ *Log Food*\n\nHow do you want to log this meal? Or log an extra "
        "gym/cardio session that's not already on your training plan.",
        parse_mode="Markdown",
        reply_markup=_food_menu_keyboard()
    )


# ── UI: extra exercise (gym/strength/cardio not on the training plan) ──
def _exercise_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Light (~30min)", callback_data="exercise|light")],
        [InlineKeyboardButton("🟡 Moderate (~45-60min)", callback_data="exercise|moderate")],
        [InlineKeyboardButton("🔴 Hard/long (~60-90min)", callback_data="exercise|hard")],
        [InlineKeyboardButton("↩️ Undo today's extra", callback_data="exercise|reset")],
    ])


async def cb_exercise_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🏋️ *Extra Exercise*\n\nGym, strength, extra cardio — doesn't matter "
        "which, just pick roughly how much it took out of you. No need to be precise.",
        parse_mode="Markdown",
        reply_markup=_exercise_keyboard()
    )


async def cb_exercise_log(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, level = query.data.split("|")
    today = date.today().isoformat()

    if level == "reset":
        _reset_extra_exercise(today)
        await query.answer("Cleared")
        await query.edit_message_text("Cleared today's extra exercise adjustment.")
        return

    kcal = EXTRA_EXERCISE_KCAL[level]
    _add_extra_exercise(today, kcal)
    await query.answer(f"+{kcal} kcal added")
    lo, hi, label = compute_daily_target()
    await query.edit_message_text(
        f"🏋️ Logged: {EXTRA_EXERCISE_LABEL[level]} (+{kcal} kcal)\n\n"
        f"Today's target is now *{lo}-{hi} kcal* ({label})",
        parse_mode="Markdown"
    )


async def cb_food_start_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📷 Send a photo of your food/drink now.")
    return FOOD_AWAIT_INPUT


async def cb_food_start_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✍️ Describe what you ate — e.g. \"chicken rice, small plate\" or "
        "\"Yeo's soya bean drink, 300ml\".\n\nType it now:"
    )
    return FOOD_AWAIT_INPUT


def _confirm_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Save", callback_data="food_save"),
         InlineKeyboardButton("✏️ Edit", callback_data="food_edit"),
         InlineKeyboardButton("❌ Cancel", callback_data="food_cancel")],
    ])


def _format_estimate(est: dict) -> str:
    conf_icon = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(est.get("confidence", "medium"), "🟡")
    text = (
        f"*{est['food_name']}*"
        + (f" _({est['portion']})_" if est.get("portion") else "")
        + "\n\n"
        f"🔥 {est['calories']} kcal   "
        f"🥩 {est['protein']}g protein\n"
        f"🍚 {est['carbs']}g carbs   🥑 {est['fat']}g fat\n\n"
        f"{conf_icon} Confidence: {est.get('confidence', 'medium')}"
    )
    if est.get("notes"):
        text += f"\n_{est['notes']}_"
    return text


async def food_receive_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔎 Analysing photo…")
    photo_file = await update.message.photo[-1].get_file()
    image_bytes = await photo_file.download_as_bytearray()
    est = await estimate_food_from_image(bytes(image_bytes))
    if not est:
        await msg.edit_text("⚠️ Couldn't analyse that photo. Try again or use text logging instead.")
        return ConversationHandler.END
    ctx.user_data["pending_food"] = est
    await msg.edit_text(_format_estimate(est), parse_mode="Markdown", reply_markup=_confirm_keyboard())
    return ConversationHandler.END


async def food_receive_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    desc = update.message.text
    msg = await update.message.reply_text("🔎 Estimating…")
    est = await estimate_food_from_text(desc)
    if not est:
        await msg.edit_text("⚠️ Couldn't estimate that. Try rephrasing, e.g. include brand + rough portion.")
        return ConversationHandler.END
    ctx.user_data["pending_food"] = est
    await msg.edit_text(_format_estimate(est), parse_mode="Markdown", reply_markup=_confirm_keyboard())
    return ConversationHandler.END


async def cb_food_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Saved")
    est = ctx.user_data.pop("pending_food", None)
    if not est:
        await query.edit_message_text("⚠️ Nothing to save — that estimate expired, please log again.")
        return
    entry = {
        "date": date.today().isoformat(),
        "logged_at": datetime.now().isoformat(timespec="seconds"),
        "food_name": est["food_name"],
        "portion": est.get("portion", ""),
        "calories": est["calories"],
        "protein": est["protein"],
        "carbs": est["carbs"],
        "fat": est["fat"],
    }
    _append_log(entry)

    todays = _logs_for_day(entry["date"])
    totals = _totals(todays)
    lo, hi, label = compute_daily_target()
    remaining = hi - totals["calories"]
    summary = (
        f"✅ Logged: *{entry['food_name']}* ({entry['calories']} kcal)\n\n"
        f"📊 Today so far: *{totals['calories']} kcal* / {lo}-{hi} target ({label}) · "
        f"{totals['protein']}g protein\n"
    )
    if remaining > 0:
        summary += f"You have roughly *{remaining} kcal* left today."
    else:
        summary += "You're at/above today's target range — that's fine occasionally, especially after a hard session."
    await query.edit_message_text(summary, parse_mode="Markdown")


async def cb_food_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Cancelled")
    ctx.user_data.pop("pending_food", None)
    await query.edit_message_text("Cancelled — nothing saved.")


async def cb_food_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✏️ Type a corrected description (e.g. add portion size, or say "
        "\"actually large portion\" / \"no rice\"). I'll re-estimate:"
    )
    return FOOD_AWAIT_EDIT


async def food_receive_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    desc = update.message.text
    msg = await update.message.reply_text("🔎 Re-estimating…")
    est = await estimate_food_from_text(desc)
    if not est:
        await msg.edit_text("⚠️ Still couldn't estimate that — try /cancel and log again.")
        return ConversationHandler.END
    ctx.user_data["pending_food"] = est
    await msg.edit_text(_format_estimate(est), parse_mode="Markdown", reply_markup=_confirm_keyboard())
    return ConversationHandler.END


async def food_cancel_conv(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("pending_food", None)
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


# ── Views ─────────────────────────────────────────────────────────────
async def _send_today_summary(reply_target, day: str = None):
    day = day or date.today().isoformat()
    logs = _logs_for_day(day)
    totals = _totals(logs)

    if day == date.today().isoformat():
        lo, hi, label = compute_daily_target()
        target_str = f"{lo}-{hi} ({label})"
    else:
        lo_def, hi_def = NORMAL_DAY_DEFICIT
        lo, hi = _round25(REST_DAY_MAINTENANCE - hi_def), _round25(REST_DAY_MAINTENANCE - lo_def)
        target_str = f"{lo}-{hi}"

    if not logs:
        text = f"📊 *{day}*\n\nNothing logged yet today. Target: {target_str}"
    else:
        lines = "\n".join(f"• {l['food_name']} — {l['calories']}kcal" for l in logs)
        text = (
            f"📊 *{day}*\n\n{lines}\n\n"
            f"*Total: {totals['calories']} kcal* (target {target_str})\n"
            f"Protein: {totals['protein']}g (target ~{DAILY_PROTEIN_TARGET_G}g) · "
            f"Carbs: {totals['carbs']}g · Fat: {totals['fat']}g"
        )
    await reply_target(text, parse_mode="Markdown")


async def show_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _send_today_summary(update.message.reply_text)


async def cb_food_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _send_today_summary(query.message.reply_text)


async def cb_food_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = _load()
    all_logs = data.get("food_logs", [])
    today = date.today()
    lines = []
    week_total = 0
    for i in range(6, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        day_logs = [l for l in all_logs if l.get("date") == d]
        day_total = _totals(day_logs)["calories"]
        week_total += day_total
        marker = " 👈" if d == today.isoformat() else ""
        lines.append(f"{d}: {day_total} kcal{marker}" if day_logs else f"{d}: —")
    avg = week_total // 7
    text = "📅 *Last 7 days*\n\n" + "\n".join(lines) + f"\n\n*Average: {avg} kcal/day*"
    await query.message.reply_text(text, parse_mode="Markdown")


async def cb_food_menu_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handles the non-conversation-entry buttons on the food menu."""
    query = update.callback_query
    _, action = query.data.split("|")
    if action == "today":
        await cb_food_today(update, ctx)
    elif action == "week":
        await cb_food_week(update, ctx)


# ── Registration ─────────────────────────────────────────────────────
def register(app):
    """Call once from bot.py's main(), before app.run_polling()."""
    food_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_food_start_photo, pattern=r"^food\|photo$"),
            CallbackQueryHandler(cb_food_start_text, pattern=r"^food\|text$"),
            CallbackQueryHandler(cb_food_edit, pattern=r"^food_edit$"),
        ],
        states={
            FOOD_AWAIT_INPUT: [
                MessageHandler(filters.PHOTO, food_receive_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, food_receive_text),
            ],
            FOOD_AWAIT_EDIT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, food_receive_edit),
            ],
        },
        fallbacks=[CommandHandler("cancel", food_cancel_conv)],
        per_message=False,
    )
    app.add_handler(food_conv)
    app.add_handler(CallbackQueryHandler(cb_food_menu_router, pattern=r"^food\|(today|week)$"))
    app.add_handler(CallbackQueryHandler(cb_food_save, pattern=r"^food_save$"))
    app.add_handler(CallbackQueryHandler(cb_food_cancel, pattern=r"^food_cancel$"))
    app.add_handler(CallbackQueryHandler(cb_exercise_menu, pattern=r"^exercise_menu$"))
    app.add_handler(CallbackQueryHandler(cb_exercise_log, pattern=r"^exercise\|"))
    app.add_handler(CommandHandler("foodtoday", show_today))
    logger.info("food.py handlers registered")
