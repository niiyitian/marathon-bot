"""
food.py — Food, Nutrition & Exercise add-on for @YitianMarathonBot

Self-contained module. Reuses the SAME data.json store as bot.py (safe
read-merge-write, only touching the "food_logs", "exercise_extra", and
"profile" keys — never touches bot.py's own data).

── Wiring it into bot.py ──────────────────────────────────────────────
1. At the top of bot.py, add:
       import food

2. In main_menu_keyboard(), add new rows, e.g.:
       ["🍽️ Log Food", "📊 Nutrition"],
       ["🏋️ Extra Exercise", "⚖️ My Profile"],

3. In text_router(), add:
       elif text == "🍽️ Log Food":
           return await food.food_menu(update, ctx)
       elif text == "📊 Nutrition":
           return await food.show_today(update, ctx)
       elif text == "🏋️ Extra Exercise":
           return await food.exercise_menu(update, ctx)
       elif text == "⚖️ My Profile":
           return await food.profile_menu(update, ctx)

4. In main(), BEFORE the generic photo/text catch-all handlers
   (MessageHandler(filters.PHOTO, ...) / MessageHandler(filters.TEXT, ...))
   — same slot as your other ConversationHandlers:
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

# ── Conversation states ──────────────────────────────────────────────
FOOD_AWAIT_INPUT = 300
FOOD_AWAIT_EDIT = 301
PROFILE_AWAIT_WEIGHT = 302
PROFILE_AWAIT_FULL = 303
FOOD_AWAIT_EXACT = 304


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


def _totals(logs: list) -> dict:
    t = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}
    for l in logs:
        t["calories"] += l.get("calories", 0) or 0
        t["protein"] += l.get("protein", 0) or 0
        t["carbs"] += l.get("carbs", 0) or 0
        t["fat"] += l.get("fat", 0) or 0
    return t


# ── Profile — feeds the BMR/maintenance calc, so targets stay accurate ──
# Seeded from the TDEE conversation (63kg/164cm/25/F). Update via the
# "⚖️ My Profile" menu whenever weight changes rather than editing this.
_DEFAULT_PROFILE = {"weight_kg": 63.0, "height_cm": 164.0, "age": 25, "sex": "F"}
_REFERENCE_WEIGHT_KG = 63.0  # the weight the flat kcal tables below are calibrated at


def _get_profile() -> dict:
    data = _load()
    return {**_DEFAULT_PROFILE, **data.get("profile", {})}


def _save_profile(updates: dict):
    data = _load()
    profile = {**_DEFAULT_PROFILE, **data.get("profile", {})}
    profile.update(updates)
    data["profile"] = profile
    _save(data)


def _bmr(p: dict) -> float:
    """Mifflin-St Jeor."""
    base = 10 * p["weight_kg"] + 6.25 * p["height_cm"] - 5 * p["age"]
    return base + 5 if p["sex"].upper().startswith("M") else base - 161


def _rest_day_maintenance() -> float:
    """BMR × activity factor (1.3, desk-based day, no training)."""
    return _bmr(_get_profile()) * 1.3


def _run_kcal_per_km() -> float:
    """~0.92 kcal/kg/km running economy estimate, scaled to current weight."""
    return _get_profile()["weight_kg"] * 0.92


def _weight_scale() -> float:
    return _get_profile()["weight_kg"] / _REFERENCE_WEIGHT_KG


# ── Targets ──────────────────────────────────────────────────────────
NORMAL_DAY_DEFICIT = (250, 350)   # (tighter, looser) kcal below maintenance
HYROX_KCAL_BASE = 500             # at reference weight, scaled by _weight_scale()
DAILY_PROTEIN_TARGET_G = 100      # ~1.6g/kg bodyweight, reasonable for a runner

# Tag priority (most demanding first) — used when a day has multiple sessions
_TAG_PRIORITY = ["RACE", "PACE", "HYROX", "LONG RUN", "MP RUN", "TEMPO", "TRACK", "EASY"]


def _today_sessions() -> list:
    """Pull today's scheduled session(s) from bot.py's TRAINING_PLAN, WITH any
    edits applied — mirrors bot.py's own session_display() override logic
    (data["edits"][uid] overrides "summary"/"desc"), since editing a session
    via "✏️ Edit Tuesday/Thursday" does NOT change TRAINING_PLAN itself, only
    the edits dict in the shared data.json. Returns list of (summary, desc).
    Lazy import avoids a circular import (bot.py imports food.py at load time)."""
    try:
        import bot as _bot
    except ImportError:
        return []
    today = date.today().isoformat()
    edits = _load().get("edits", {})
    out = []
    for s in _bot.TRAINING_PLAN:
        uid, dt, summary, desc = s[0], s[1], s[2], s[3]
        if dt != today:
            continue
        override = edits.get(uid, {})
        summary = override.get("summary", summary)
        desc = override.get("desc", desc)
        out.append((summary, desc))
    return out


def _classify_tag(summary: str) -> str:
    """Extract session type from a [Tag] bracket if present. Edited sessions
    often DON'T have a bracket (the user just typed free text, e.g. "Tempo
    Run Club - 6x10mins..."), so fall back to a keyword search in the
    summary itself (never the desc/notes, to avoid false-matching phrases
    like 'skip club tempo' in coach notes)."""
    tag_match = re.search(r'\[(.*?)\]', summary)
    if tag_match:
        return tag_match.group(1).upper()
    upper_summary = summary.upper()
    for candidate in _TAG_PRIORITY:
        if candidate in upper_summary:
            return candidate
    return ""


# ── Manual per-day override — for when training gets reshuffled and the
#    plan/edits no longer reflect what's actually happening today. Only
#    affects nutrition targeting; never touches TRAINING_PLAN or edits. ──
_OVERRIDE_TYPES = [
    ("EASY", "🟢 Easy"), ("TEMPO", "🟠 Tempo"), ("TRACK", "🔵 Track"),
    ("LONG RUN", "🟣 Long run"), ("MP RUN", "🟤 MP run"),
    ("HYROX", "🏋️ Hyrox"), ("RACE", "🏁 Race/Pace"), ("REST", "😴 Rest day"),
]
_OVERRIDE_KM_PRESETS = [5, 8, 10, 12, 15, 18, 20, 25, 30, 42]


def _set_session_override(day: str, session_type: str, km: float | None):
    data = _load()
    data.setdefault("session_override", {})[day] = {"type": session_type, "km": km}
    _save(data)


def _get_session_override(day: str) -> dict | None:
    return _load().get("session_override", {}).get(day)


def _clear_session_override(day: str):
    data = _load()
    data.get("session_override", {}).pop(day, None)
    _save(data)


def compute_daily_target() -> tuple:
    """Returns (low, high, label) for today's calorie target: rest-day
    maintenance (from your CURRENT profile) ± deficit, adjusted for whatever's
    actually happening today, PLUS any extra gym/cardio/strength logged via
    the Extra Exercise menu.

    Priority order:
    1. Manual override ("🔀 Adjust Today's Session") — wins if set, since it's
       an explicit statement of what's actually happening today.
    2. Training plan + edits (from TRAINING_PLAN + data["edits"]).
    3. Rest day, if nothing's scheduled and no override is set.
    """
    today = date.today().isoformat()
    maintenance = _rest_day_maintenance()
    run_kcal_per_km = _run_kcal_per_km()
    hyrox_kcal = HYROX_KCAL_BASE * _weight_scale()
    extra_burn = _get_extra_exercise_total(today)
    extra_suffix = " + extra exercise" if extra_burn else ""
    lo_def, hi_def = NORMAL_DAY_DEFICIT

    override = _get_session_override(today)
    if override:
        type_ = override["type"]
        km = override.get("km") or 0.0
        burn = km * run_kcal_per_km
        if type_ == "HYROX":
            burn += hyrox_kcal
        burn += extra_burn

        if type_ == "RACE":
            label = "🏁 Race/pace day — fuel fully, no deficit (manual)" + extra_suffix
            target = maintenance + burn
            return _round25(target - 50), _round25(target + 100), label
        if type_ == "REST":
            lo = maintenance - hi_def + extra_burn
            hi = maintenance - lo_def + extra_burn
            return _round25(lo), _round25(hi), "Rest day (manual)" + extra_suffix

        pretty = type_.lower().capitalize()
        label = (f"{pretty} day ({km:.0f}km, manual)" if km else f"{pretty} day (manual)") + extra_suffix
        lo = maintenance - hi_def + burn
        hi = maintenance - lo_def + burn
        return _round25(lo), _round25(hi), label

    sessions = _today_sessions()

    if not sessions:
        lo = maintenance - hi_def + extra_burn
        hi = maintenance - lo_def + extra_burn
        label = "Rest day" + extra_suffix
        return _round25(lo), _round25(hi), label

    burn = 0.0
    tags_found = []
    total_km = 0.0

    for summary, desc in sessions:
        tag = _classify_tag(summary)
        tags_found.append(tag)

        if "HYROX" in tag:
            burn += hyrox_kcal

        km_match = re.search(r'(\d+(?:\.\d+)?)\s*km', summary)  # summary first
        if not km_match:
            km_match = re.search(r'(\d+(?:\.\d+)?)\s*km', desc)  # fallback
        if km_match:
            dist = float(km_match.group(1))
            total_km += dist
            burn += dist * run_kcal_per_km

    burn += extra_burn
    upper_all = " ".join(tags_found)

    # No-deficit days: race day, pacing duty — full maintenance + burn
    if "RACE" in upper_all or "PACE" in upper_all:
        label = ("🏁 Race/pace day — fuel fully, no deficit" if "RACE" in upper_all else "Pacing duty — fuel fully, no deficit") + extra_suffix
        target = maintenance + burn
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

    lo = maintenance - hi_def + burn
    hi = maintenance - lo_def + burn
    return _round25(lo), _round25(hi), label


def _round25(n: float) -> int:
    return int(round(n / 25) * 25)


# ── Extra exercise: gym/strength/cardio NOT in TRAINING_PLAN ──────────
# Flat per-session estimates at reference weight (63kg), scaled by
# _weight_scale() for the current profile. Cardio burns more per minute
# than strength at comparable effort, hence the separate tables.
CARDIO_KCAL = {"light": 200, "moderate": 400, "hard": 600}
STRENGTH_KCAL = {"light": 150, "moderate": 280, "hard": 420}
_INTENSITY_LABEL = {"light": "light (~30min)", "moderate": "moderate (~45-60min)", "hard": "hard/long (~60-90min)"}


def _add_extra_exercise_entry(day: str, ex_type: str, intensity: str, kcal: int):
    data = _load()
    data.setdefault("exercise_extra", {}).setdefault(day, []).append({
        "type": ex_type, "intensity": intensity, "kcal": kcal,
        "logged_at": datetime.now().isoformat(timespec="seconds"),
    })
    _save(data)


def _get_extra_exercise_entries(day: str) -> list:
    data = _load()
    return data.get("exercise_extra", {}).get(day, [])


def _get_extra_exercise_total(day: str) -> int:
    return sum(e.get("kcal", 0) for e in _get_extra_exercise_entries(day))


def _undo_last_extra_exercise(day: str) -> dict | None:
    data = _load()
    entries = data.get("exercise_extra", {}).setdefault(day, [])
    if not entries:
        return None
    removed = entries.pop()
    _save(data)
    return removed


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


# ── UI: food menu ────────────────────────────────────────────────────
def _food_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📷 Log via Photo", callback_data="food|photo"),
         InlineKeyboardButton("✍️ Log via Text", callback_data="food|text")],
        [InlineKeyboardButton("🔀 Adjust Today's Session", callback_data="sessover_menu")],
        [InlineKeyboardButton("📊 Today's Totals", callback_data="food|today"),
         InlineKeyboardButton("📅 This Week", callback_data="food|week")],
    ])


async def food_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🍽️ *Log Food*\n\nHow do you want to log this meal?",
        parse_mode="Markdown",
        reply_markup=_food_menu_keyboard()
    )


# ── UI: adjust today's session (manual override for nutrition targeting) ──
def _override_type_keyboard():
    rows, row = [], []
    for type_, label in _OVERRIDE_TYPES:
        row.append(InlineKeyboardButton(label, callback_data=f"sessover_type|{type_}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("↩️ Revert to plan", callback_data="sessover_clear")])
    return InlineKeyboardMarkup(rows)


def _override_km_keyboard(session_type: str):
    rows, row = [], []
    for km in _OVERRIDE_KM_PRESETS:
        row.append(InlineKeyboardButton(f"{km}km", callback_data=f"sessover_km|{session_type}|{km}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


async def cb_sessover_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔀 *Adjust Today's Session*\n\nMoving things around this week? Tell me what "
        "you're actually doing today and I'll recalculate the target. This only "
        "affects today's nutrition — your training plan stays untouched.",
        parse_mode="Markdown",
        reply_markup=_override_type_keyboard()
    )


async def cb_sessover_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, session_type = query.data.split("|")
    today = date.today().isoformat()

    if session_type in ("REST", "HYROX", "RACE"):
        _set_session_override(today, session_type, None)
        lo, hi, label = compute_daily_target()
        pretty = session_type.lower().capitalize() if session_type != "RACE" else "Race/Pace"
        await query.edit_message_text(
            f"✅ Today set to {pretty}.\n\nTarget: *{lo}-{hi} kcal* ({label})",
            parse_mode="Markdown"
        )
        return

    await query.edit_message_text(
        f"How many km? Pick the closest:",
        reply_markup=_override_km_keyboard(session_type)
    )


async def cb_sessover_km(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, session_type, km_str = query.data.split("|")
    km = float(km_str)
    today = date.today().isoformat()
    _set_session_override(today, session_type, km)
    lo, hi, label = compute_daily_target()
    pretty = session_type.lower().capitalize()
    await query.edit_message_text(
        f"✅ Today set to {pretty} ({km:.0f}km).\n\nTarget: *{lo}-{hi} kcal* ({label})",
        parse_mode="Markdown"
    )


async def cb_sessover_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    today = date.today().isoformat()
    _clear_session_override(today)
    await query.answer("Reverted")
    lo, hi, label = compute_daily_target()
    await query.edit_message_text(
        f"↩️ Reverted to your training plan.\n\nTarget: *{lo}-{hi} kcal* ({label})",
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
         InlineKeyboardButton("✏️ Edit", callback_data="food_edit")],
        [InlineKeyboardButton("🔢 Enter Exact (from label)", callback_data="food_exact")],
        [InlineKeyboardButton("❌ Cancel", callback_data="food_cancel")],
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


async def cb_food_exact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔢 Got the label in hand? Type the exact values, comma-separated:\n"
        "`calories, protein(g), carbs(g), fat(g)`\n\n"
        "e.g. `165, 30.1, 8.1, 1.4`\n\n"
        "_Tip: use the numbers for the portion you actually had — if the label "
        "is per 100ml and you drank 200ml, double it first._",
        parse_mode="Markdown"
    )
    return FOOD_AWAIT_EXACT


async def food_receive_exact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 4:
        await update.message.reply_text(
            "Please send exactly 4 numbers separated by commas: `calories, protein, carbs, fat`\n"
            "e.g. `165, 30.1, 8.1, 1.4`",
            parse_mode="Markdown"
        )
        return FOOD_AWAIT_EXACT
    try:
        calories, protein, carbs, fat = [float(p) for p in parts]
        if min(calories, protein, carbs, fat) < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "Couldn't parse that — make sure it's 4 plain numbers, e.g. `165, 30.1, 8.1, 1.4`",
            parse_mode="Markdown"
        )
        return FOOD_AWAIT_EXACT

    est = ctx.user_data.get("pending_food") or {}
    est["calories"] = int(round(calories))
    est["protein"] = int(round(protein))
    est["carbs"] = int(round(carbs))
    est["fat"] = int(round(fat))
    est["confidence"] = "high"
    est["notes"] = "Entered manually from nutrition label."
    est.setdefault("food_name", "Unknown food")
    est.setdefault("portion", "")
    ctx.user_data["pending_food"] = est

    await update.message.reply_text(
        _format_estimate(est), parse_mode="Markdown", reply_markup=_confirm_keyboard()
    )
    return ConversationHandler.END


async def food_cancel_conv(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("pending_food", None)
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


# ── Views: today / week ──────────────────────────────────────────────
async def _send_today_summary(reply_target, day: str = None):
    day = day or date.today().isoformat()
    logs = _logs_for_day(day)
    totals = _totals(logs)

    if day == date.today().isoformat():
        lo, hi, label = compute_daily_target()
        target_str = f"{lo}-{hi} ({label})"
    else:
        lo_def, hi_def = NORMAL_DAY_DEFICIT
        maintenance = _rest_day_maintenance()
        lo, hi = _round25(maintenance - hi_def), _round25(maintenance - lo_def)
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


# ── UI: extra exercise (standalone main-menu entry) ────────────────────
def _exercise_type_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏃 Cardio", callback_data="extype|cardio"),
         InlineKeyboardButton("🏋️ Strength", callback_data="extype|strength")],
        [InlineKeyboardButton("↩️ Undo my last entry", callback_data="exundo")],
    ])


async def exercise_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏋️ *Extra Exercise*\n\nGym, strength, extra cardio — log anything not "
        "already on your training plan. Was this cardio or strength?",
        parse_mode="Markdown",
        reply_markup=_exercise_type_keyboard()
    )


def _intensity_keyboard(ex_type: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🟢 {_INTENSITY_LABEL['light']}", callback_data=f"exlevel|{ex_type}|light")],
        [InlineKeyboardButton(f"🟡 {_INTENSITY_LABEL['moderate']}", callback_data=f"exlevel|{ex_type}|moderate")],
        [InlineKeyboardButton(f"🔴 {_INTENSITY_LABEL['hard']}", callback_data=f"exlevel|{ex_type}|hard")],
    ])


async def cb_exercise_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, ex_type = query.data.split("|")
    icon = "🏃" if ex_type == "cardio" else "🏋️"
    await query.edit_message_text(
        f"{icon} How much did it take out of you? Rough is fine — no need to be precise.",
        reply_markup=_intensity_keyboard(ex_type)
    )


async def cb_exercise_level(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, ex_type, intensity = query.data.split("|")
    table = CARDIO_KCAL if ex_type == "cardio" else STRENGTH_KCAL
    kcal = round(table[intensity] * _weight_scale())
    today = date.today().isoformat()
    _add_extra_exercise_entry(today, ex_type, intensity, kcal)
    await query.answer(f"+{kcal} kcal added")
    lo, hi, label = compute_daily_target()
    icon = "🏃" if ex_type == "cardio" else "🏋️"
    await query.edit_message_text(
        f"{icon} Logged: {ex_type} — {_INTENSITY_LABEL[intensity]} (+{kcal} kcal)\n\n"
        f"Today's target is now *{lo}-{hi} kcal* ({label})",
        parse_mode="Markdown"
    )


async def cb_exercise_undo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    today = date.today().isoformat()
    removed = _undo_last_extra_exercise(today)
    if removed:
        await query.answer("Removed")
        await query.edit_message_text(
            f"↩️ Removed: {removed['type']} — {removed.get('intensity', '')} ({removed['kcal']} kcal)."
        )
    else:
        await query.answer("Nothing to undo")
        await query.edit_message_text("No extra exercise logged today.")


# ── UI: profile (standalone main-menu entry) ────────────────────────────
def _profile_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Update Weight", callback_data="profile|weight")],
        [InlineKeyboardButton("✏️ Update Full Profile", callback_data="profile|full")],
    ])


async def profile_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    p = _get_profile()
    bmr = _bmr(p)
    maint = _rest_day_maintenance()
    text = (
        f"⚖️ *My Profile*\n\n"
        f"Weight: {p['weight_kg']}kg\n"
        f"Height: {p['height_cm']}cm\n"
        f"Age: {p['age']}\n"
        f"Sex: {p['sex']}\n\n"
        f"BMR: ~{bmr:.0f} kcal · Rest-day maintenance: ~{maint:.0f} kcal\n\n"
        f"_These feed your daily nutrition targets — update your weight here "
        f"as it changes so targets stay accurate._"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=_profile_keyboard())


async def cb_profile_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, which = query.data.split("|")
    if which == "weight":
        await query.edit_message_text("Type your current weight in kg (e.g. 61.5):")
        return PROFILE_AWAIT_WEIGHT
    p = _get_profile()
    await query.edit_message_text(
        "Type your weight, height, age, sex as: `weight, height, age, sex`\n"
        f"e.g. `61.5, 164, 25, F`\n\n"
        f"Current: {p['weight_kg']}, {p['height_cm']}, {p['age']}, {p['sex']}",
        parse_mode="Markdown"
    )
    return PROFILE_AWAIT_FULL


async def profile_receive_weight(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    try:
        w = float(re.sub(r'[^\d.]', '', raw))
        if not (30 <= w <= 200):
            raise ValueError
    except ValueError:
        await update.message.reply_text("Couldn't parse that — just send a number, e.g. 61.5")
        return PROFILE_AWAIT_WEIGHT
    _save_profile({"weight_kg": w})
    lo, hi, label = compute_daily_target()
    await update.message.reply_text(
        f"✅ Weight updated to {w}kg.\n\nToday's target recalculated: *{lo}-{hi} kcal* ({label})",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


async def profile_receive_full(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 4:
        await update.message.reply_text(
            "Please send exactly 4 values separated by commas: `weight, height, age, sex`",
            parse_mode="Markdown"
        )
        return PROFILE_AWAIT_FULL
    try:
        w = float(parts[0])
        h = float(parts[1])
        a = int(float(parts[2]))
        sex = parts[3].strip().upper()[0]
        if sex not in ("M", "F"):
            raise ValueError
        if not (30 <= w <= 200 and 100 <= h <= 220 and 10 <= a <= 100):
            raise ValueError
    except (ValueError, IndexError):
        await update.message.reply_text(
            "Couldn't parse that. Format: `61.5, 164, 25, F`", parse_mode="Markdown"
        )
        return PROFILE_AWAIT_FULL
    _save_profile({"weight_kg": w, "height_cm": h, "age": a, "sex": sex})
    lo, hi, label = compute_daily_target()
    await update.message.reply_text(
        f"✅ Profile updated.\n\nToday's target recalculated: *{lo}-{hi} kcal* ({label})",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


async def profile_cancel_conv(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


# ── Registration ─────────────────────────────────────────────────────
def register(app):
    """Call once from bot.py's main(), before app.run_polling() and BEFORE
    the generic photo/text catch-all MessageHandlers."""
    food_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_food_start_photo, pattern=r"^food\|photo$"),
            CallbackQueryHandler(cb_food_start_text, pattern=r"^food\|text$"),
            CallbackQueryHandler(cb_food_edit, pattern=r"^food_edit$"),
            CallbackQueryHandler(cb_food_exact, pattern=r"^food_exact$"),
        ],
        states={
            FOOD_AWAIT_INPUT: [
                MessageHandler(filters.PHOTO, food_receive_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, food_receive_text),
            ],
            FOOD_AWAIT_EDIT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, food_receive_edit),
            ],
            FOOD_AWAIT_EXACT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, food_receive_exact),
            ],
        },
        fallbacks=[CommandHandler("cancel", food_cancel_conv)],
        per_message=False,
    )
    profile_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_profile_start, pattern=r"^profile\|")],
        states={
            PROFILE_AWAIT_WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, profile_receive_weight)],
            PROFILE_AWAIT_FULL: [MessageHandler(filters.TEXT & ~filters.COMMAND, profile_receive_full)],
        },
        fallbacks=[CommandHandler("cancel", profile_cancel_conv)],
        per_message=False,
    )
    app.add_handler(food_conv)
    app.add_handler(profile_conv)
    app.add_handler(CallbackQueryHandler(cb_food_menu_router, pattern=r"^food\|(today|week)$"))
    app.add_handler(CallbackQueryHandler(cb_food_save, pattern=r"^food_save$"))
    app.add_handler(CallbackQueryHandler(cb_food_cancel, pattern=r"^food_cancel$"))
    app.add_handler(CallbackQueryHandler(cb_exercise_type, pattern=r"^extype\|"))
    app.add_handler(CallbackQueryHandler(cb_exercise_level, pattern=r"^exlevel\|"))
    app.add_handler(CallbackQueryHandler(cb_exercise_undo, pattern=r"^exundo$"))
    app.add_handler(CallbackQueryHandler(cb_sessover_menu, pattern=r"^sessover_menu$"))
    app.add_handler(CallbackQueryHandler(cb_sessover_type, pattern=r"^sessover_type\|"))
    app.add_handler(CallbackQueryHandler(cb_sessover_km, pattern=r"^sessover_km\|"))
    app.add_handler(CallbackQueryHandler(cb_sessover_clear, pattern=r"^sessover_clear$"))
    app.add_handler(CommandHandler("foodtoday", show_today))
    logger.info("food.py handlers registered")
