import os
import re
import json
import time
import base64
import asyncio
from io import BytesIO

import requests
from PIL import Image
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

# ============================================================
# CONFIG
# ============================================================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
MODEL_VISION = os.getenv("MODEL_VISION", "gpt-4.1-mini").strip()

FREE_TRIAL_LIMIT = int(os.getenv("FREE_TRIAL_LIMIT", "5"))

# --- TP rules (points -> price)
POINT_VALUE = float(os.getenv("POINT_VALUE", "0.01"))  # 0.01 = 1 point
CONF_STRONG = int(os.getenv("CONF_STRONG", "70"))

# ✅ TP1 fixed always (Marketing rule)
TP1_FIXED_POINTS = int(os.getenv("TP1_FIXED_POINTS", "200"))

# TP2/TP3 weak vs strong
TP2_WEAK_POINTS = int(os.getenv("TP2_WEAK_POINTS", "400"))
TP3_WEAK_POINTS = int(os.getenv("TP3_WEAK_POINTS", "600"))

TP2_STRONG_POINTS = int(os.getenv("TP2_STRONG_POINTS", "500"))
TP3_STRONG_POINTS = int(os.getenv("TP3_STRONG_POINTS", "700"))

# Admin IDs: "7269750900,123"
ADMIN_IDS = set()
_admin_raw = os.getenv("ADMIN_IDS", "").strip()
if _admin_raw:
    for x in re.split(r"[,\s]+", _admin_raw):
        x = x.strip()
        if x.isdigit():
            ADMIN_IDS.add(int(x))

DB_FILE = "db.json"
DB_LOCK = asyncio.Lock()

# ✅ Plans: ONLY FREE + PAID (Lifetime)
PLANS = ["FREE", "PAID"]  # PAID = Lifetime

# ============================================================
# Marketing + Gumroad
# ============================================================
GUMROAD_URL = os.getenv("GUMROAD_URL", "https://6864159013627.gumroad.com/l/vrjql").strip()
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "Al7ebsi17@gmail.com").strip()  # مرجعي فقط

# ============================================================
# I18N (AR / EN / FR)
# ============================================================
LANGS = {"en": "English", "ar": "العربية", "fr": "Français"}

T = {
    "en": {
        "choose_lang": "🌍 Please choose your language:",
        "lang_set": "✅ Language set to English.",
        "welcome_title": "🤖 Trading AI Bot",
        "welcome_body": (
            "Send a CLEAR chart screenshot (zoom on candles).\n"
            "You will receive:\n"
            "• Market State (Bullish/Bearish/Neutral)\n"
            "• Signal (BUY/SELL) + Entry Zone\n"
            "• TP1/TP2/TP3 + SL\n"
        ),
        "free_trial": "🧪 Free Trial: {n} analyses",
        "menu_analyze": "📸 Analyze Chart",
        "menu_plans": "💳 Subscribe / Plans",
        "menu_help": "❓ Help",
        "menu_lang": "🌐 Language",
        "send_chart_now": "📸 Please send a clear chart screenshot now.\nTip: Ensure SYMBOL + TF are visible on the chart.",
        "help_text": (
            "✅ How to use:\n"
            "1) Press 📸 Analyze Chart\n"
            "2) Send a clear chart screenshot (zoom candles)\n"
            "3) Get entry + TP/SL instantly\n\n"
            "Notes:\n"
            "• Best results when price scale, symbol, timeframe are visible.\n"
        ),
        "plans_title": "💎 Trading AI — ULTIMATE (Lifetime)",
        "plans_body": (
            "✅ Unlimited analyses\n"
            "✅ Unlimited signals\n"
            "✅ Priority support\n\n"
            "🔥 LIMITED OFFER: $49 (was $149)\n"
        ),
        "btn_subscribe": "💳 Subscribe — $49 (ULTIMATE)",
        "btn_paid": "✅ I Paid / Activate",
        "activate_ask_email": "✉️ Please send the email you used for Gumroad payment.\n\n(Or press Cancel)",
        "btn_cancel": "✖️ Cancel",
        "activate_cancelled": "✅ Activation cancelled.",
        "invalid_email": "❌ Please send a valid email address (example: name@gmail.com).",
        "thanks_email": "✅ Thanks! We received your email.\nYour subscription will be activated after verification.",
        "trial_ended": "🔒 Free trial ended.\nSubscribe to unlock unlimited analysis.",
        "admin_only": "⛔ Admin only.",
        "setplan_usage": "Usage:\n/setplan <user_id> FREE\n/setplan <user_id> PAID",
        "setplan_ok": "✅ Set {uid} plan={plan}",
        "analysis_failed": "❌ Analysis failed.\nTry a clearer screenshot (zoom candles) and make sure price/symbol/TF are visible.",
        "header": "━━━━━━━━━━━━━━━━\n🤖 Trading AI — Signal\n━━━━━━━━━━━━━━━━",
        "market_state": "Market State",
        "market": "Market",
        "entry": "Entry Zone",
        "sl": "SL",
        "note": "Note",
        "educational": "📌 Educational only | Risk 1–2%",
        "trial_remaining": "🧪 Free Trial remaining: {rem}/{tot}",
        "subscribe_hint": "Subscribe: /plans ($49 lifetime)",
        "legal_note": "Momentum supports the setup, but market conditions may change quickly. Manage risk accordingly.",
        "signal_buy": "BUY",
        "signal_sell": "SELL",
        "bullish": "Bullish",
        "bearish": "Bearish",
        "neutral": "Neutral",
        "strong_mom": "Strong momentum",
        "mild_mom": "Mild momentum",
        "neutral_mom": "Neutral",
        "low_conv": "Low conviction",
    },
    "ar": {
        "choose_lang": "🌍 اختر لغتك:",
        "lang_set": "✅ تم ضبط اللغة على العربية.",
        "welcome_title": "🤖 Trading AI Bot",
        "welcome_body": (
            "أرسل صورة شارت واضحة (قرّب الشموع).\n"
            "ستحصل على:\n"
            "• حالة السوق (صاعد/هابط/محايد)\n"
            "• توصية (شراء/بيع) + منطقة دخول\n"
            "• أهداف TP1/TP2/TP3 + وقف خسارة SL\n"
        ),
        "free_trial": "🧪 التجربة المجانية: {n} تحليلات",
        "menu_analyze": "📸 تحليل صورة شارت",
        "menu_plans": "💳 الاشتراك / الخطط",
        "menu_help": "❓ مساعدة",
        "menu_lang": "🌐 تغيير اللغة",
        "send_chart_now": "📸 أرسل الآن صورة شارت واضحة للتحليل.\nنصيحة: تأكد أن اسم الزوج + الفريم ظاهرين على الشارت.",
        "help_text": (
            "✅ طريقة الاستخدام:\n"
            "1) اضغط 📸 تحليل صورة شارت\n"
            "2) أرسل صورة شارت واضحة (قرّب الشموع)\n"
            "3) تحصل على دخول + TP/SL فورًا\n\n"
            "ملاحظات:\n"
            "• أفضل نتيجة عندما يكون السعر واسم الزوج والفريم ظاهرين.\n"
        ),
        "plans_title": "💎 Trading AI — ULTIMATE (مدى الحياة)",
        "plans_body": (
            "✅ تحليلات غير محدودة\n"
            "✅ إشارات غير محدودة\n"
            "✅ دعم أولوية\n\n"
            "🔥 عرض محدود: 49$ (بدلاً من 149$)\n"
        ),
        "btn_subscribe": "💳 اشتراك — 49$ (ULTIMATE)",
        "btn_paid": "✅ دفعت / تفعيل",
        "activate_ask_email": "✉️ اكتب الإيميل الذي استخدمته في الدفع عبر Gumroad.\n\n(أو اضغط إلغاء)",
        "btn_cancel": "✖️ إلغاء",
        "activate_cancelled": "✅ تم إلغاء التفعيل.",
        "invalid_email": "❌ اكتب بريد إلكتروني صحيح (مثال: name@gmail.com).",
        "thanks_email": "✅ شكرًا! تم استلام الإيميل.\nسيتم تفعيل اشتراكك بعد التحقق.",
        "trial_ended": "🔒 انتهت التجربة المجانية.\nاشترك لفتح التحليل غير المحدود.",
        "admin_only": "⛔ للأدمن فقط.",
        "setplan_usage": "الاستخدام:\n/setplan <user_id> FREE\n/setplan <user_id> PAID",
        "setplan_ok": "✅ تم ضبط {uid} على خطة {plan}",
        "analysis_failed": "❌ فشل التحليل.\nجرّب صورة أوضح (قرّب الشموع) وتأكد أن السعر/الزوج/الفريم ظاهرين.",
        "header": "━━━━━━━━━━━━━━━━\n🤖 Trading AI — Signal\n━━━━━━━━━━━━━━━━",
        "market_state": "حالة السوق",
        "market": "السوق",
        "entry": "منطقة الدخول",
        "sl": "وقف الخسارة",
        "note": "ملاحظة",
        "educational": "📌 لأغراض تعليمية فقط | مخاطرة 1–2%",
        "trial_remaining": "🧪 المتبقي من التجربة: {rem}/{tot}",
        "subscribe_hint": "للاشتراك: /plans (49$ مدى الحياة)",
        "legal_note": "الزخم يدعم هذا السيناريو، لكن السوق قد يتغير بسرعة. إدارة المخاطر ضرورية.",
        "signal_buy": "شراء",
        "signal_sell": "بيع",
        "bullish": "صاعد",
        "bearish": "هابط",
        "neutral": "محايد",
        "strong_mom": "زخم قوي",
        "mild_mom": "زخم متوسط",
        "neutral_mom": "محايد",
        "low_conv": "وضوح منخفض",
    },
    "fr": {
        "choose_lang": "🌍 Veuillez choisir votre langue :",
        "lang_set": "✅ Langue définie sur Français.",
        "welcome_title": "🤖 Trading AI Bot",
        "welcome_body": (
            "Envoyez une capture d’écran claire du graphique (zoomez sur les bougies).\n"
            "Vous recevrez :\n"
            "• État du marché (Haussier/Baissier/Neutre)\n"
            "• Signal (ACHAT/VENTE) + Zone d’entrée\n"
            "• TP1/TP2/TP3 + SL\n"
        ),
        "free_trial": "🧪 Essai gratuit : {n} analyses",
        "menu_analyze": "📸 Analyser le graphique",
        "menu_plans": "💳 Abonnement / Offres",
        "menu_help": "❓ Aide",
        "menu_lang": "🌐 Langue",
        "send_chart_now": "📸 Envoyez maintenant une capture claire du graphique.\nAstuce : Assurez-vous que le symbole + TF sont visibles.",
        "help_text": (
            "✅ Comment utiliser :\n"
            "1) Appuyez sur 📸 Analyser le graphique\n"
            "2) Envoyez une capture claire (zoomez sur les bougies)\n"
            "3) Recevez Entrée + TP/SL instantanément\n\n"
            "Notes :\n"
            "• Meilleurs résultats si prix, symbole et timeframe sont visibles.\n"
        ),
        "plans_title": "💎 Trading AI — ULTIMATE (À vie)",
        "plans_body": (
            "✅ Analyses illimitées\n"
            "✅ Signaux illimités\n"
            "✅ Support prioritaire\n\n"
            "🔥 Offre limitée : 49$ (au lieu de 149$)\n"
        ),
        "btn_subscribe": "💳 S’abonner — 49$ (ULTIMATE)",
        "btn_paid": "✅ J’ai payé / Activer",
        "activate_ask_email": "✉️ Envoyez l’email utilisé pour le paiement Gumroad.\n\n(Ou appuyez sur Annuler)",
        "btn_cancel": "✖️ Annuler",
        "activate_cancelled": "✅ Activation annulée.",
        "invalid_email": "❌ Veuillez envoyer une adresse email valide.",
        "thanks_email": "✅ Merci ! Email reçu.\nVotre abonnement sera activé après vérification.",
        "trial_ended": "🔒 Essai gratuit terminé.\nAbonnez-vous pour débloquer l’illimité.",
        "admin_only": "⛔ Admin seulement.",
        "setplan_usage": "Usage:\n/setplan <user_id> FREE\n/setplan <user_id> PAID",
        "setplan_ok": "✅ Plan défini: {uid} = {plan}",
        "analysis_failed": "❌ Analyse échouée.\nEssayez une image plus claire et assurez-vous que prix/symbole/TF sont visibles.",
        "header": "━━━━━━━━━━━━━━━━\n🤖 Trading AI — Signal\n━━━━━━━━━━━━━━━━",
        "market_state": "État du marché",
        "market": "Marché",
        "entry": "Zone d’entrée",
        "sl": "SL",
        "note": "Note",
        "educational": "📌 Éducatif seulement | Risque 1–2%",
        "trial_remaining": "🧪 Essai restant : {rem}/{tot}",
        "subscribe_hint": "S’abonner : /plans (49$ à vie)",
        "legal_note": "Le momentum soutient ce scénario, mais le marché peut changer rapidement. Gérez le risque.",
        "signal_buy": "ACHAT",
        "signal_sell": "VENTE",
        "bullish": "Haussier",
        "bearish": "Baissier",
        "neutral": "Neutre",
        "strong_mom": "Momentum fort",
        "mild_mom": "Momentum modéré",
        "neutral_mom": "Neutre",
        "low_conv": "Faible conviction",
    },
}

DEFAULT_LANG = os.getenv("DEFAULT_LANG", "en").strip().lower()
if DEFAULT_LANG not in LANGS:
    DEFAULT_LANG = "en"

# ============================================================
# DB helpers
# ============================================================
def _now_ts():
    return int(time.time())

def _default_user():
    return {
        "plan": "FREE",
        "expires_at": 0,
        "trial_used": 0,
        "created_at": _now_ts(),
        "lang": DEFAULT_LANG,
    }

async def load_db():
    async with DB_LOCK:
        if not os.path.exists(DB_FILE):
            return {"users": {}}
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"users": {}}

async def save_db(db):
    async with DB_LOCK:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)

async def get_user(db, user_id):
    uid = str(user_id)
    if uid not in db["users"]:
        db["users"][uid] = _default_user()
        await save_db(db)
    # ensure lang exists
    if "lang" not in db["users"][uid] or db["users"][uid]["lang"] not in LANGS:
        db["users"][uid]["lang"] = DEFAULT_LANG
        await save_db(db)
    return db["users"][uid]

async def set_lang(db, user_id, lang):
    u = await get_user(db, user_id)
    u["lang"] = lang
    await save_db(db)

def is_admin(user_id):
    return user_id in ADMIN_IDS

async def set_plan(db, user_id, plan):
    plan = (plan or "").strip().upper()
    if plan not in PLANS:
        raise ValueError("Invalid plan")
    u = await get_user(db, user_id)
    u["plan"] = plan
    u["expires_at"] = 0
    await save_db(db)

async def trial_remaining(u):
    used = int(u.get("trial_used", 0) or 0)
    return max(0, FREE_TRIAL_LIMIT - used)

# ============================================================
# Menus (INLINE ONLY) - no reply keyboard (prevents email trap)
# ============================================================
def lang_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇬🇧 English", callback_data="setlang_en"),
            InlineKeyboardButton("🇸🇦 العربية", callback_data="setlang_ar"),
            InlineKeyboardButton("🇫🇷 Français", callback_data="setlang_fr"),
        ]
    ])

def main_menu(lang):
    tt = T[lang]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(tt["menu_analyze"], callback_data="menu_analyze")],
        [
            InlineKeyboardButton(tt["menu_plans"], callback_data="menu_plans"),
            InlineKeyboardButton(tt["menu_help"], callback_data="menu_help"),
        ],
        [InlineKeyboardButton(tt["menu_lang"], callback_data="menu_lang")],
    ])

def plans_keyboard(lang):
    tt = T[lang]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(tt["btn_subscribe"], url=GUMROAD_URL)],
        [InlineKeyboardButton(tt["btn_paid"], callback_data="paid_activate")],
        [InlineKeyboardButton(tt["menu_lang"], callback_data="menu_lang")],
    ])

def cancel_keyboard(lang):
    tt = T[lang]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(tt["btn_cancel"], callback_data="cancel_activate")]
    ])

# ============================================================
# Pending activation state
# ============================================================
PENDING_EMAIL = set()  # user_id set

# ============================================================
# TP enforcement helpers
# ============================================================
_NUM_RE = re.compile(r"(-?\d+(?:\.\d+)?)")

def _extract_floats(text):
    if not text:
        return []
    return [float(x) for x in _NUM_RE.findall(text)]

def _detect_decimals(text, default=1):
    if not text:
        return default
    m = re.search(r"\d+\.(\d+)", text)
    if m:
        return min(4, max(0, len(m.group(1))))
    return default

def _format_price(x, decimals):
    fmt = "{:." + str(decimals) + "f}"
    return fmt.format(x)

def _parse_entry_anchor(entry_zone):
    nums = _extract_floats(entry_zone or "")
    if not nums:
        return None
    if len(nums) >= 2 and ("-" in (entry_zone or "") or "–" in (entry_zone or "")):
        return (nums[0] + nums[1]) / 2.0
    return nums[0]

def enforce_tp_rules(result):
    try:
        conf = int(result.get("confidence", 50) or 50)
    except Exception:
        conf = 50

    entry_zone = str(result.get("entry_zone", "") or "")
    anchor = _parse_entry_anchor(entry_zone)
    if anchor is None:
        return result

    decimals = _detect_decimals(entry_zone, default=1)

    sig = str(result.get("signal", "BUY") or "BUY").upper()
    if sig not in ("BUY", "SELL"):
        sig = "BUY"

    strong = conf >= CONF_STRONG

    p1 = TP1_FIXED_POINTS
    p2 = TP2_STRONG_POINTS if strong else TP2_WEAK_POINTS
    p3 = TP3_STRONG_POINTS if strong else TP3_WEAK_POINTS

    d1 = p1 * POINT_VALUE
    d2 = p2 * POINT_VALUE
    d3 = p3 * POINT_VALUE

    if sig == "BUY":
        tp1 = anchor + d1
        tp2 = anchor + d2
        tp3 = anchor + d3
    else:
        tp1 = anchor - d1
        tp2 = anchor - d2
        tp3 = anchor - d3

    result["tp1"] = _format_price(tp1, decimals)
    result["tp2"] = _format_price(tp2, decimals)
    result["tp3"] = _format_price(tp3, decimals)
    return result

# ============================================================
# Confidence profile -> marketing label (localized)
# ============================================================
def confidence_label_key(conf):
    try:
        c = int(conf)
    except Exception:
        c = 50
    if c >= 80:
        return "strong_mom"
    if 70 <= c < 80:
        return "mild_mom"
    if 60 <= c < 70:
        return "neutral_mom"
    return "low_conv"

# ============================================================
# OpenAI vision call (Responses API)
# ============================================================
def image_to_base64_jpeg(image_bytes, max_side=1024, quality=85):
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    scale = min(1.0, float(max_side) / float(max(w, h)))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)))
    out = BytesIO()
    img.save(out, format="JPEG", quality=quality, optimize=True)
    return base64.b64encode(out.getvalue()).decode("utf-8")

def openai_analyze_chart(b64jpeg):
    if not OPENAI_API_KEY:
        raise RuntimeError("Missing OPENAI_API_KEY")

    # IMPORTANT: We keep core analysis same, just add symbol/timeframe extraction if visible
    prompt = (
        "You are a trading assistant analyzing a chart screenshot.\n"
        "Return STRICT JSON ONLY (no markdown, no extra text) with these keys:\n"
        "symbol: string like 'XAUUSD' or 'EURUSD' or 'BTCUSD' (best guess from chart; if unknown return empty string)\n"
        "timeframe: string like 'M1','M5','M15','M30','H1','H4','D1' (best guess from chart; if unknown empty)\n"
        "market_state: one of ['Bullish','Bearish','Neutral']\n"
        "signal: one of ['BUY','SELL'] (NEVER return WAIT)\n"
        "confidence: integer 0-100\n"
        "entry_zone: string like '4420.0 - 4424.0' or 'Breakout above 4435.0'\n"
        "tp1,tp2,tp3: strings (price levels)\n"
        "sl: string (price level)\n"
        "caution: short string\n"
        "reasoning_short: short 1-2 lines\n\n"
        "Rules:\n"
        "- If chart is unclear, still give a CONDITIONAL setup (breakout/breakdown) and lower confidence.\n"
        "- Use visible prices from chart when possible.\n"
        "- Keep TP/SL realistic relative to entry.\n"
        "- Do NOT mention policy, do NOT mention that you are an AI.\n"
    )

    url = "https://api.openai.com/v1/responses"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": MODEL_VISION,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64jpeg}"},
                ],
            }
        ],
        "max_output_tokens": 500,
    }

    r = requests.post(url, headers=headers, json=payload, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"OpenAI error {r.status_code}: {r.text}")

    data = r.json()

    out_text = ""
    for item in data.get("output", []):
        for c in item.get("content", []):
            if c.get("type") in ("output_text", "text") and "text" in c:
                out_text += c["text"]

    out_text = (out_text or "").strip()
    if not out_text:
        raise RuntimeError("Empty OpenAI output")

    try:
        parsed = json.loads(out_text)
    except Exception:
        m = re.search(r"\{.*\}", out_text, re.S)
        if not m:
            raise RuntimeError(f"Invalid JSON from model: {out_text[:300]}")
        parsed = json.loads(m.group(0))

    # defaults
    parsed.setdefault("symbol", "")
    parsed.setdefault("timeframe", "")
    parsed.setdefault("market_state", "Neutral")
    parsed.setdefault("signal", "BUY")
    parsed.setdefault("confidence", 50)
    parsed.setdefault("entry_zone", "N/A")
    parsed.setdefault("tp1", "N/A")
    parsed.setdefault("tp2", "N/A")
    parsed.setdefault("tp3", "N/A")
    parsed.setdefault("sl", "N/A")
    parsed.setdefault("caution", "Use risk management.")
    parsed.setdefault("reasoning_short", "")

    # sanitize
    try:
        parsed["confidence"] = int(parsed["confidence"])
    except Exception:
        parsed["confidence"] = 50

    sig = str(parsed.get("signal", "BUY")).upper()
    parsed["signal"] = "BUY" if sig not in ("BUY", "SELL") else sig

    ms = str(parsed.get("market_state", "Neutral")).capitalize()
    if ms not in ("Bullish", "Bearish", "Neutral"):
        ms = "Neutral"
    parsed["market_state"] = ms

    sym = str(parsed.get("symbol", "") or "").upper().strip()
    tf = str(parsed.get("timeframe", "") or "").upper().strip()
    # normalize timeframe
    if tf and tf not in ("M1", "M5", "M15", "M30", "H1", "H4", "D1"):
        tf = ""
    parsed["symbol"] = sym
    parsed["timeframe"] = tf

    return parsed

# ============================================================
# Fallback symbol/tf from caption (optional)
# ============================================================
def guess_symbol_tf_from_caption(caption):
    if not caption:
        return "", ""
    cap = caption.upper()
    sym = ""
    tf = ""
    for s in ["XAUUSD", "GOLD", "BTCUSD", "ETHUSD", "EURUSD", "GBPUSD", "USDJPY", "US30", "NAS100", "SPX", "WTI", "BRENT"]:
        if s in cap:
            sym = "XAUUSD" if s == "GOLD" else s
            break
    m = re.search(r"\b(M1|M5|M15|M30|H1|H4|D1)\b", cap)
    if m:
        tf = m.group(1)
    return sym, tf

# ============================================================
# Formatting (PRO header + localized labels)
# ============================================================
def localize_market_state(lang, ms):
    tt = T[lang]
    if ms == "Bullish":
        return tt["bullish"]
    if ms == "Bearish":
        return tt["bearish"]
    return tt["neutral"]

def localize_signal(lang, sig):
    tt = T[lang]
    return tt["signal_buy"] if sig == "BUY" else tt["signal_sell"]

def format_signal_message(lang, symbol, timeframe, result, trial_line):
    tt = T[lang]

    ms = result["market_state"]
    sig = result["signal"]
    conf = int(result.get("confidence", 50) or 50)
    entry = str(result.get("entry_zone", "N/A") or "N/A")
    tp1, tp2, tp3 = str(result.get("tp1", "N/A")), str(result.get("tp2", "N/A")), str(result.get("tp3", "N/A"))
    sl = str(result.get("sl", "N/A"))

    # Emojis
    state_emoji = "📈" if ms == "Bullish" else ("📉" if ms == "Bearish" else "⏸️")
    sig_emoji = "🟢" if sig == "BUY" else "🔴"

    # Localized text
    ms_local = localize_market_state(lang, ms)
    sig_local = localize_signal(lang, sig)

    label_key = confidence_label_key(conf)
    market_label = tt[label_key]

    sym = symbol or "SYMBOL"
    tf = timeframe or "TF"

    # ✅ PRO header
    header = tt["header"]

    # ✅ Legal note (short, safe)
    legal_note = tt["legal_note"]

    lines = []
    lines.append(header)
    lines.append(f"{sig_emoji} {sig_local} | {sym} | {tf} | {conf}%")
    lines.append(f"{state_emoji} {tt['market_state']}: {ms_local}")
    lines.append(f"🧭 {tt['market']}: {market_label}")
    lines.append("")
    lines.append(f"🎯 {tt['entry']}: {entry}")
    lines.append(f"✅ TP1: {tp1}")
    lines.append(f"✅ TP2: {tp2}")
    lines.append(f"✅ TP3: {tp3}")
    lines.append(f"🛑 {tt['sl']}: {sl}")
    lines.append("")
    lines.append(f"🧠 {tt['note']}: {legal_note}")

    if trial_line:
        lines.append("")
        lines.append(trial_line)

    lines.append("")
    lines.append(tt["educational"])
    return "\n".join(lines)

# ============================================================
# Handlers
# ============================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db = await load_db()
    u = await get_user(db, user_id)
    lang = u.get("lang", DEFAULT_LANG)

    # Always show language selection first (Noro style)
    await update.message.reply_text(T[lang]["choose_lang"], reply_markup=lang_keyboard())

async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(f"✅ Your ID: {uid}")

async def cmd_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db = await load_db()
    u = await get_user(db, user_id)
    lang = u.get("lang", DEFAULT_LANG)
    tt = T[lang]

    msg = f"{tt['plans_title']}\n\n{tt['plans_body']}"
    await update.message.reply_text(msg, reply_markup=plans_keyboard(lang))

async def cmd_setplan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db = await load_db()
    u = await get_user(db, uid)
    lang = u.get("lang", DEFAULT_LANG)
    tt = T[lang]

    if not is_admin(uid):
        await update.message.reply_text(tt["admin_only"])
        return

    parts = (update.message.text or "").split()
    if len(parts) != 3:
        await update.message.reply_text(tt["setplan_usage"])
        return

    target_id = parts[1].strip()
    plan = parts[2].strip().upper()

    if not target_id.isdigit():
        await update.message.reply_text("❌ user_id must be numeric.")
        return
    if plan not in PLANS:
        await update.message.reply_text("❌ Invalid plan. Use FREE or PAID.")
        return

    await set_plan(db, int(target_id), plan)
    await update.message.reply_text(tt["setplan_ok"].format(uid=target_id, plan=plan))

async def send_welcome_and_menu(chat_id, context, lang):
    tt = T[lang]
    # Welcome card (fancy + simple)
    db = await load_db()
    u = await get_user(db, chat_id)
    rem = await trial_remaining(u)

    welcome = (
        f"{tt['welcome_title']}\n\n"
        f"{tt['welcome_body']}\n"
        f"{tt['free_trial'].format(n=FREE_TRIAL_LIMIT)}\n"
        f"{tt['trial_remaining'].format(rem=rem, tot=FREE_TRIAL_LIMIT)}"
    )
    await context.bot.send_message(chat_id=chat_id, text=welcome, reply_markup=main_menu(lang))

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    db = await load_db()
    u = await get_user(db, user_id)
    lang = u.get("lang", DEFAULT_LANG)

    data = query.data or ""

    # Language menu
    if data == "menu_lang":
        await query.message.reply_text(T[lang]["choose_lang"], reply_markup=lang_keyboard())
        return

    # Set language
    if data.startswith("setlang_"):
        new_lang = data.split("_", 1)[1].strip().lower()
        if new_lang not in LANGS:
            new_lang = DEFAULT_LANG
        await set_lang(db, user_id, new_lang)

        # Do NOT break activation state; just confirm and show menu
        await query.message.reply_text(T[new_lang]["lang_set"])
        await send_welcome_and_menu(query.message.chat_id, context, new_lang)
        return

    # Plans
    if data == "menu_plans":
        await query.message.reply_text(
            f"{T[lang]['plans_title']}\n\n{T[lang]['plans_body']}",
            reply_markup=plans_keyboard(lang)
        )
        return

    # Help
    if data == "menu_help":
        await query.message.reply_text(T[lang]["help_text"], reply_markup=main_menu(lang))
        return

    # Analyze (just prompt to send photo; no extra menus)
    if data == "menu_analyze":
        # Mark awaiting photo (UX)
        context.user_data["awaiting_photo"] = True
        await query.message.reply_text(T[lang]["send_chart_now"])
        return

    # Activation flow
    if data == "paid_activate":
        PENDING_EMAIL.add(user_id)
        await query.message.reply_text(T[lang]["activate_ask_email"], reply_markup=cancel_keyboard(lang))
        return

    if data == "cancel_activate":
        if user_id in PENDING_EMAIL:
            PENDING_EMAIL.discard(user_id)
        await query.message.reply_text(T[lang]["activate_cancelled"], reply_markup=main_menu(lang))
        return

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = update.effective_user.id

    db = await load_db()
    u = await get_user(db, user_id)
    lang = u.get("lang", DEFAULT_LANG)
    tt = T[lang]

    # If user was asked for email, and they send photo => ignore email state, analyze photo (more pro UX)
    if user_id in PENDING_EMAIL:
        # keep pending activation, but allow analysis
        pass

    plan = (u.get("plan", "FREE") or "FREE").upper()

    if plan == "FREE":
        rem = await trial_remaining(u)
        if rem <= 0:
            await msg.reply_text(tt["trial_ended"], reply_markup=plans_keyboard(lang))
            return

    await msg.chat.send_action(ChatAction.TYPING)

    photo = msg.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    b = await file.download_as_bytearray()

    caption = msg.caption or ""
    sym_cap, tf_cap = guess_symbol_tf_from_caption(caption)

    try:
        b64 = image_to_base64_jpeg(bytes(b), max_side=1100, quality=85)

        # Analyze with OpenAI (thread)
        result = await asyncio.to_thread(openai_analyze_chart, b64)

        # TP rules (keep your marketing TP1 close)
        result = enforce_tp_rules(result)

        # Determine symbol/tf:
        sym_img = (result.get("symbol", "") or "").strip().upper()
        tf_img = (result.get("timeframe", "") or "").strip().upper()

        symbol = sym_img or sym_cap or ""
        timeframe = tf_img or tf_cap or ""

        # Trial update
        trial_line = ""
        if plan == "FREE":
            u["trial_used"] = int(u.get("trial_used", 0) or 0) + 1
            await save_db(db)
            rem_after = await trial_remaining(u)
            trial_line = (
                tt["trial_remaining"].format(rem=rem_after, tot=FREE_TRIAL_LIMIT) + "\n" +
                tt["subscribe_hint"]
            )

        text = format_signal_message(lang, symbol, timeframe, result, trial_line)

        # ✅ IMPORTANT: Do NOT send menu after analysis (as you requested)
        await msg.reply_text(text)

        # After successful analysis, no longer "awaiting_photo"
        context.user_data["awaiting_photo"] = False

    except Exception as e:
        await msg.reply_text(f"{tt['analysis_failed']}\n\nDebug: {str(e)[:220]}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    t = (update.message.text or "").strip()

    db = await load_db()
    u = await get_user(db, user_id)
    lang = u.get("lang", DEFAULT_LANG)
    tt = T[lang]

    # Activation email step
    if user_id in PENDING_EMAIL:
        # Allow user to still change language or open plans without being trapped
        # But since our buttons are INLINE, this mostly happens if they type manually.
        if t.startswith("/"):
            return

        # Validate email
        if "@" not in t or "." not in t or len(t) < 6:
            await update.message.reply_text(tt["invalid_email"], reply_markup=cancel_keyboard(lang))
            return

        # accept
        PENDING_EMAIL.discard(user_id)

        username = update.effective_user.username or "NoUsername"
        cmd_ready = f"/setplan {user_id} PAID"

        msg_admin = (
            "💰 Payment Request\n\n"
            f"👤 User: @{username}\n"
            f"🆔 ID: {user_id}\n"
            f"📧 Email: {t}\n\n"
            "✅ Verify in Gumroad → Sales (search by email)\n\n"
            f"⚡ Activate command (copy/paste):\n{cmd_ready}\n\n"
            f"(Admin email ref: {ADMIN_EMAIL})"
        )

        if ADMIN_IDS:
            for admin_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(chat_id=admin_id, text=msg_admin)
                except Exception:
                    pass

        await update.message.reply_text(tt["thanks_email"], reply_markup=main_menu(lang))
        return

    # Normal chat text: guide user to send screenshot
    if t.startswith("/"):
        return

    # If user typed random text, keep it clean and pro:
    await update.message.reply_text(tt["send_chart_now"])

# ============================================================
# Main
# ============================================================
async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")
    if not OPENAI_API_KEY:
        print("WARNING: OPENAI_API_KEY missing. Analysis will fail.")
    if not ADMIN_IDS:
        print("WARNING: ADMIN_IDS is empty. Payment requests won't reach you and /setplan won't work.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("plans", cmd_plans))
    app.add_handler(CommandHandler("setplan", cmd_setplan))

    app.add_handler(CallbackQueryHandler(on_callback))

    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("✅ Bot starting (Polling)...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
