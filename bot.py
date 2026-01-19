import os
import re
import json
import time
import base64
import asyncio
from io import BytesIO

import requests
from PIL import Image

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

# =========================
# Config
# =========================
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
PLANS = ["FREE", "PAID"]  # PAID = $49 Lifetime

# =========================
# Marketing + Gumroad (HTML)
# =========================
GUMROAD_URL = "https://6864159013627.gumroad.com/l/vrjql"
ADMIN_EMAIL = "Al7ebsi17@gmail.com"  # مرجعي فقط

OFFER_TEXT_HTML = (
    "🔥 <b>LIMITED OFFER</b> 🔥\n\n"
    "💎 Trading AI – <b>ULTIMATE</b> (Lifetime)\n"
    "<s>$149</s> ➜ <b>$49</b>\n\n"
    "✅ Unlimited image analysis\n"
    "✅ Unlimited signals\n"
    "✅ Priority support\n\n"
    "━━━━━━━━━━━━\n\n"
    "🔥 <b>عرض محدود</b> 🔥\n\n"
    "💎 Trading AI – <b>ULTIMATE</b> (مدى الحياة)\n"
    "<s>149$</s> ➜ <b>49$</b>\n\n"
    "✅ تحليل غير محدود\n"
    "✅ إشارات غير محدودة\n"
    "✅ دعم أولوية\n\n"
    "⬇️ اضغط للاشتراك 👇"
)

def offer_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Subscribe – $49 (ULTIMATE)", url=GUMROAD_URL)],
        [InlineKeyboardButton("✅ I Paid / Activate", callback_data="paid_activate")]
    ])

# Pending email step (no webhook)
PENDING_EMAIL = {}  # user_id -> True

# =========================
# Language / UI
# =========================
LANGS = ["en", "ar", "fr"]

UI = {
    "en": {
        "choose_lang": "🌍 Please choose your language:",
        "lang_set": "✅ Language set to English.",
        "menu_title": "Choose an option:",
        "btn_analyze": "📸 Analyze Chart",
        "btn_plans": "💳 Subscribe / Plans",
        "btn_help": "❓ Help",
        "btn_lang": "🌐 Language",
        "send_chart_only": "📸 Please send a clear chart screenshot now (zoom on candles).",
        "analyzing": "🔍 Analyzing…",
        "trial_remaining": "🧪 Free Trial remaining: {rem}/{limit}\nSubscribe: /plans ($49 lifetime)",
        "trial_ended": "🔒 <b>Free trial ended.</b>\n\n",
        "edu": "📌 Educational only | Risk 1–2%",
        "need_lang": "Please choose your language first:",
        "send_email": "✉️ Please send the email you used for Gumroad payment.",
        "invalid_email": "❌ Please send a valid email address.",
        "email_received": "✅ Thanks! We received your email.\nYour subscription will be activated after verification.",
        "admin_missing": "⚠️ Admin not configured.\nPlease set ADMIN_IDS in server env.",
        "help_text_html": (
            "❓ <b>Help</b>\n\n"
            "1) Choose your language\n"
            "2) Press Analyze (optional) or just send a chart screenshot\n"
            "3) You will receive: Signal + Entry + TP1/TP2/TP3 + SL\n\n"
            "Tip: Zoom on candles and make sure prices are visible."
        ),
        "plans_text_html": (
            "💎 <b>Trading AI Subscription</b>\n\n"
            f"• FREE: <b>{FREE_TRIAL_LIMIT}</b> image analyses trial\n"
            "• ULTIMATE (LIFETIME): <s>$149</s> ➜ <b>$49</b>\n"
            "  - Unlimited photos\n"
            "  - Unlimited time\n\n"
            "⬇️ Subscribe here 👇"
        ),
        "market_state": "Market State",
        "market": "Market",
        "entry": "Entry Zone",
        "note": "Note",
        "tp": "TP",
        "sl": "SL",
    },
    "ar": {
        "choose_lang": "🌍 يرجى اختيار لغتك:",
        "lang_set": "✅ تم ضبط اللغة على العربية.",
        "menu_title": "اختر خيارًا:",
        "btn_analyze": "📸 تحليل صورة شارت",
        "btn_plans": "💳 الاشتراك / الخطط",
        "btn_help": "❓ مساعدة",
        "btn_lang": "🌐 تغيير اللغة",
        "send_chart_only": "📸 أرسل صورة الشارت الآن بشكل واضح (قرّب الشموع).",
        "analyzing": "🔍 جاري التحليل…",
        "trial_remaining": "🧪 المتبقي من التجربة: {rem}/{limit}\nللاشتراك: /plans (49$ مدى الحياة)",
        "trial_ended": "🔒 <b>انتهت التجربة المجانية.</b>\n\n",
        "edu": "📌 تعليمي فقط | مخاطرة 1–2%",
        "need_lang": "اختر اللغة أولاً:",
        "send_email": "✉️ اكتب الإيميل الذي استخدمته في الدفع عبر Gumroad.",
        "invalid_email": "❌ الرجاء إرسال بريد إلكتروني صحيح.",
        "email_received": "✅ شكرًا! تم استلام الإيميل.\nسيتم تفعيل اشتراكك بعد التحقق.",
        "admin_missing": "⚠️ لم يتم إعداد المدير.\nالرجاء ضبط ADMIN_IDS في السيرفر.",
        "help_text_html": (
            "❓ <b>مساعدة</b>\n\n"
            "1) اختر اللغة\n"
            "2) اضغط (تحليل صورة شارت) أو فقط أرسل صورة الشارت\n"
            "3) ستحصل على: توصية + دخول + TP1/TP2/TP3 + SL\n\n"
            "نصيحة: قرّب الشموع وتأكد أن الأسعار واضحة."
        ),
        "plans_text_html": (
            "💎 <b>اشتراك Trading AI</b>\n\n"
            f"• مجاني: <b>{FREE_TRIAL_LIMIT}</b> تحليلات تجريبية\n"
            "• ULTIMATE (مدى الحياة): <s>149$</s> ➜ <b>49$</b>\n"
            "  - صور غير محدودة\n"
            "  - بدون مدة زمنية\n\n"
            "⬇️ اضغط للاشتراك 👇"
        ),
        "market_state": "حالة السوق",
        "market": "السوق",
        "entry": "منطقة الدخول",
        "note": "ملاحظة",
        "tp": "هدف",
        "sl": "وقف",
    },
    "fr": {
        "choose_lang": "🌍 Veuillez choisir votre langue :",
        "lang_set": "✅ Langue définie sur le Français.",
        "menu_title": "Choisissez une option :",
        "btn_analyze": "📸 Analyser le graphique",
        "btn_plans": "💳 Abonnement / Offres",
        "btn_help": "❓ Aide",
        "btn_lang": "🌐 Langue",
        "send_chart_only": "📸 Envoyez maintenant une capture claire (zoom bougies).",
        "analyzing": "🔍 Analyse…",
        "trial_remaining": "🧪 Essai restant: {rem}/{limit}\nAbonnement: /plans (49$ à vie)",
        "trial_ended": "🔒 <b>Essai gratuit terminé.</b>\n\n",
        "edu": "📌 Éducatif seulement | Risque 1–2%",
        "need_lang": "Veuillez choisir la langue d’abord :",
        "send_email": "✉️ Envoyez l’email utilisé pour le paiement Gumroad.",
        "invalid_email": "❌ Veuillez envoyer un email valide.",
        "email_received": "✅ Merci ! Email reçu.\nActivation après vérification.",
        "admin_missing": "⚠️ Admin non configuré.\nVeuillez définir ADMIN_IDS.",
        "help_text_html": (
            "❓ <b>Aide</b>\n\n"
            "1) Choisir la langue\n"
            "2) Appuyer sur Analyser (optionnel) ou envoyer directement une capture\n"
            "3) Vous recevrez : Signal + Entrée + TP1/TP2/TP3 + SL\n\n"
            "Astuce : Zoom sur les bougies et assurez-vous que les prix sont visibles."
        ),
        "plans_text_html": (
            "💎 <b>Abonnement Trading AI</b>\n\n"
            f"• GRATUIT : <b>{FREE_TRIAL_LIMIT}</b> analyses d’essai\n"
            "• ULTIMATE (À VIE): <s>$149</s> ➜ <b>$49</b>\n"
            "  - Photos illimitées\n"
            "  - Accès illimité\n\n"
            "⬇️ Abonnez-vous ici 👇"
        ),
        "market_state": "État du marché",
        "market": "Marché",
        "entry": "Zone d’entrée",
        "note": "Note",
        "tp": "TP",
        "sl": "SL",
    },
}

def lang_name(code: str) -> str:
    code = (code or "en").lower()
    if code == "ar":
        return "Arabic"
    if code == "fr":
        return "French"
    return "English"

def lang_kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
            InlineKeyboardButton("🇸🇦 العربية", callback_data="lang_ar"),
            InlineKeyboardButton("🇫🇷 Français", callback_data="lang_fr"),
        ]
    ])

def main_menu_kb(lang: str):
    t = UI.get(lang, UI["en"])
    return ReplyKeyboardMarkup(
        [
            [t["btn_analyze"]],
            [t["btn_plans"], t["btn_help"]],
            [t["btn_lang"]],
        ],
        resize_keyboard=True
    )

# =========================
# DB helpers
# =========================
def _now_ts():
    return int(time.time())

def _default_user():
    return {
        "plan": "FREE",
        "expires_at": 0,   # not used for PAID
        "trial_used": 0,
        "created_at": _now_ts(),
        "lang": "",        # en/ar/fr
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

def is_admin(user_id):
    return user_id in ADMIN_IDS

async def get_user(db, user_id):
    uid = str(user_id)
    if uid not in db["users"]:
        db["users"][uid] = _default_user()
        await save_db(db)
    return db["users"][uid]

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

# =========================
# TP enforcement helpers (UNCHANGED)
# =========================
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

# =========================
# Confidence Messaging (Localized, no logic change)
# =========================
def confidence_profile_local(conf, lang):
    try:
        c = int(conf)
    except Exception:
        c = 50

    # Keep meaning same as your EN version, just translated
    if lang == "ar":
        if c >= 80:
            return ("زخم قوي", "السعر قريب من مناطق إنهاك محتملة. أهداف سريعة مقترحة.")
        if 70 <= c < 80:
            return ("زخم متوسط", "الاتجاه نشط. راقب تفاعل السعر عند المستويات المهمة.")
        if 60 <= c < 70:
            return ("محايد", "تشكّل هيكل سعري. يفضّل جني أرباح جزئي.")
        return ("ثقة منخفضة", "وضوح منخفض. انتظر تأكيد وادِر المخاطرة بحذر.")

    if lang == "fr":
        if c >= 80:
            return ("Forte dynamique", "Le prix approche d’une zone d’épuisement. Objectifs rapides conseillés.")
        if 70 <= c < 80:
            return ("Dynamique modérée", "Tendance active. Surveillez la réaction sur les niveaux clés.")
        if 60 <= c < 70:
            return ("Neutre", "La structure se forme. Prise partielle recommandée.")
        return ("Faible conviction", "Peu de clarté. Attendez une confirmation et gérez le risque.")

    # default EN
    if c >= 80:
        return ("Strong momentum", "Price is approaching potential exhaustion. Quick targets recommended.")
    if 70 <= c < 80:
        return ("Mild momentum", "Trend is active. Watch price reaction near key levels.")
    if 60 <= c < 70:
        return ("Neutral", "Structure is forming. Momentum is building. Partial profits recommended.")
    return ("Low conviction", "Low clarity. Wait for confirmation and manage risk carefully.")

def apply_confidence_messaging(result, lang):
    conf = int(result.get("confidence", 50) or 50)
    market_label, note = confidence_profile_local(conf, lang)
    result["market_label"] = market_label
    result["note_local"] = note
    return result

# =========================
# OpenAI vision call (Responses API) + Language + symbol/tf
# =========================
def image_to_base64_jpeg(image_bytes, max_side=1024, quality=85):
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    scale = min(1.0, float(max_side) / float(max(w, h)))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)))
    out = BytesIO()
    img.save(out, format="JPEG", quality=quality, optimize=True)
    return base64.b64encode(out.getvalue()).decode("utf-8")

def openai_analyze_chart(b64jpeg, out_lang="en"):
    if not OPENAI_API_KEY:
        raise RuntimeError("Missing OPENAI_API_KEY")

    out_lang = (out_lang or "en").lower()
    human_lang = lang_name(out_lang)

    prompt = (
        f"You are a trading assistant analyzing a chart screenshot.\n"
        f"IMPORTANT: All human-readable text MUST be written in {human_lang}.\n\n"
        "Return STRICT JSON ONLY (no markdown, no extra text) with these keys:\n"
        "symbol: string (e.g., 'XAUUSD', 'EURUSD', 'BTCUSD') if visible, else ''\n"
        "timeframe: string (e.g., 'M1','M5','M15','M30','H1','H4','D1') if visible, else ''\n"
        "market_state: one of ['Bullish','Bearish','Neutral']\n"
        "signal: one of ['BUY','SELL'] (NEVER return WAIT)\n"
        "confidence: integer 0-100\n"
        "entry_zone: string like '4420.0 - 4424.0' or 'Breakout above 4435.0' (in chosen language)\n"
        "tp1,tp2,tp3: strings (price levels)\n"
        "sl: string (price level)\n"
        "caution: short string (in chosen language)\n"
        "reasoning_short: short 1-2 lines (in chosen language)\n\n"
        "Rules:\n"
        "- If chart is unclear, still give a CONDITIONAL setup (breakout/breakdown) and lower confidence.\n"
        "- Use visible prices from chart when possible.\n"
        "- Keep TP/SL realistic relative to entry.\n"
        "- Do NOT mention policy, do NOT mention that you are an AI.\n"
    )

    url = "https://api.openai.com/v1/responses"
    headers = {
        "Authorization": "Bearer {}".format(OPENAI_API_KEY),
        "Content-Type": "application/json",
    }

    payload = {
        "model": MODEL_VISION,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": "data:image/jpeg;base64,{}".format(b64jpeg)}
                ],
            }
        ],
        "max_output_tokens": 500,
    }

    r = requests.post(url, headers=headers, json=payload, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError("OpenAI error {}: {}".format(r.status_code, r.text))

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
            raise RuntimeError("Invalid JSON from model: {}".format(out_text[:300]))
        parsed = json.loads(m.group(0))

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
    parsed.setdefault("caution", "")
    parsed.setdefault("reasoning_short", "")

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

    # normalize symbol/tf
    parsed["symbol"] = (parsed.get("symbol") or "").strip().upper()
    parsed["timeframe"] = (parsed.get("timeframe") or "").strip().upper()

    return parsed

def normalize_tf(x: str) -> str:
    x = (x or "").strip().upper().replace(" ", "")
    x = x.replace("MIN", "M").replace("MINS", "M")
    x = x.replace("HOUR", "H").replace("HOURS", "H")
    if x.isdigit():
        return "M" + x
    # 5M -> M5
    if re.match(r"^\d+M$", x):
        return "M" + x[:-1]
    # keep common ones
    return x

def guess_symbol_tf(caption):
    if not caption:
        return "", ""
    cap = caption.upper()
    sym = ""
    tf = ""
    for s in ["XAUUSD", "GOLD", "BTCUSD", "EURUSD", "GBPUSD", "USDJPY"]:
        if s in cap:
            sym = "XAUUSD" if s == "GOLD" else s
            break
    m = re.search(r"\b(M1|M5|M15|M30|H1|H4|D1)\b", cap)
    if m:
        tf = m.group(1)
    return sym, tf

def translate_market_state(ms, lang):
    ms = (ms or "Neutral").capitalize()
    if lang == "ar":
        return {"Bullish": "صاعد", "Bearish": "هابط", "Neutral": "محايد"}.get(ms, "محايد")
    if lang == "fr":
        return {"Bullish": "Haussier", "Bearish": "Baissier", "Neutral": "Neutre"}.get(ms, "Neutre")
    return ms

def translate_signal(sig, lang):
    sig = (sig or "BUY").upper()
    if lang == "ar":
        return "شراء" if sig == "BUY" else "بيع"
    if lang == "fr":
        return "ACHAT" if sig == "BUY" else "VENTE"
    return sig

def format_signal_message(lang, symbol_hint, timeframe_hint, result, trial_line):
    t = UI.get(lang, UI["en"])

    ms = result["market_state"]
    sig = result["signal"]
    conf = result["confidence"]
    entry = result["entry_zone"]
    tp1, tp2, tp3 = result["tp1"], result["tp2"], result["tp3"]
    sl = result["sl"]

    market_label = result.get("market_label", "")
    note_local = result.get("note_local", "")
    reasoning = (result.get("reasoning_short") or "").strip()
    caution = (result.get("caution") or "").strip()

    state_emoji = "📈" if ms == "Bullish" else ("📉" if ms == "Bearish" else "⏸️")
    sig_emoji = "🟢" if sig == "BUY" else "🔴"

    sym = symbol_hint or "SYMBOL"
    tf = timeframe_hint or "TF"

    ms_local = translate_market_state(ms, lang)
    sig_local = translate_signal(sig, lang)

    # Message: clean & professional
    msg = (
        f"{sig_emoji} {sig_local} | {sym} | {tf} | {conf}%\n"
        f"{state_emoji} {t['market_state']}: {ms_local}\n"
    )
    if market_label:
        msg += f"🧭 {t['market']}: {market_label}\n"

    msg += (
        f"\n🎯 {t['entry']}: {entry}\n"
        f"✅ {t['tp']}1: {tp1}\n"
        f"✅ {t['tp']}2: {tp2}\n"
        f"✅ {t['tp']}3: {tp3}\n"
        f"🛑 {t['sl']}: {sl}\n"
    )

    # Notes in selected language
    if note_local or reasoning or caution:
        msg += "\n"
    if note_local:
        msg += f"🧠 {t['note']}: {note_local}\n"
    if reasoning:
        msg += f"📌 {reasoning}\n"
    if caution:
        msg += f"⚠️ {caution}\n"

    if trial_line:
        msg += f"\n{trial_line}\n"

    msg += f"\n{t['edu']}"
    return msg

# =========================
# UX state: "Analyze" mode (no menu under)
# =========================
AWAITING_PHOTO = {}  # user_id -> True

# =========================
# Telegram Handlers
# =========================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = await load_db()
    u = await get_user(db, update.effective_user.id)
    lang = (u.get("lang") or "").strip().lower()

    # If no language yet -> show language selection (like Noro)
    if lang not in LANGS:
        await update.message.reply_text(UI["en"]["choose_lang"], reply_markup=lang_kb())
        return

    # If language set -> show clean welcome + main menu
    t = UI[lang]
    await update.message.reply_text(
        t["menu_title"],
        reply_markup=main_menu_kb(lang)
    )

async def cb_set_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    lang = query.data.replace("lang_", "").strip().lower()
    if lang not in LANGS:
        lang = "en"

    db = await load_db()
    u = await get_user(db, query.from_user.id)
    u["lang"] = lang
    await save_db(db)

    t = UI[lang]
    # After language set -> show menu
    await query.message.reply_text(t["lang_set"])
    await query.message.reply_text(t["menu_title"], reply_markup=main_menu_kb(lang))

async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text("✅ Your ID: {}".format(uid))

async def cmd_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = await load_db()
    u = await get_user(db, update.effective_user.id)
    lang = (u.get("lang") or "en").lower()
    t = UI.get(lang, UI["en"])

    await update.message.reply_text(
        t["plans_text_html"],
        parse_mode="HTML",
        reply_markup=offer_keyboard()
    )

async def cmd_setplan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin only:
      /setplan <user_id> FREE
      /setplan <user_id> PAID
    """
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Admin only.")
        return

    parts = (update.message.text or "").split()
    if len(parts) != 3:
        await update.message.reply_text("Usage:\n/setplan <user_id> FREE\n/setplan <user_id> PAID")
        return

    target_id = parts[1].strip()
    plan = parts[2].strip().upper()

    if not target_id.isdigit():
        await update.message.reply_text("❌ user_id must be numeric.")
        return
    if plan not in PLANS:
        await update.message.reply_text("❌ Invalid plan. Use FREE or PAID.")
        return

    db = await load_db()
    await set_plan(db, int(target_id), plan)
    await update.message.reply_text("✅ Set {} plan={}".format(target_id, plan))

async def paid_activate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    db = await load_db()
    u = await get_user(db, uid)
    lang = (u.get("lang") or "en").lower()
    t = UI.get(lang, UI["en"])

    PENDING_EMAIL[uid] = True
    await query.message.reply_text(t["send_email"])

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db = await load_db()
    u = await get_user(db, uid)
    lang = (u.get("lang") or "").lower()
    t = UI.get(lang if lang in LANGS else "en", UI["en"])

    txt = (update.message.text or "").strip()

    # If waiting for payment email
    if uid in PENDING_EMAIL:
        if "@" not in txt or "." not in txt:
            await update.message.reply_text(t["invalid_email"])
            return

        del PENDING_EMAIL[uid]

        username = update.effective_user.username or "NoUsername"
        cmd_ready = f"/setplan {uid} PAID"

        msg_admin = (
            "💰 Payment Request\n\n"
            f"👤 User: @{username}\n"
            f"🆔 ID: {uid}\n"
            f"📧 Email: {txt}\n\n"
            "✅ Verify in Gumroad → Sales (search by email)\n\n"
            f"⚡ Activate command (copy/paste):\n{cmd_ready}\n\n"
            f"(Admin email ref: {ADMIN_EMAIL})"
        )

        if not ADMIN_IDS:
            await update.message.reply_text(t["admin_missing"])
            return

        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(chat_id=admin_id, text=msg_admin)
            except Exception:
                pass

        await update.message.reply_text(t["email_received"], reply_markup=main_menu_kb(lang))
        return

    # Language button
    if txt == t["btn_lang"]:
        await update.message.reply_text(t["choose_lang"], reply_markup=lang_kb())
        return

    # Plans button
    if txt == t["btn_plans"]:
        await update.message.reply_text(t["plans_text_html"], parse_mode="HTML", reply_markup=offer_keyboard())
        return

    # Help button
    if txt == t["btn_help"]:
        await update.message.reply_text(t["help_text_html"], parse_mode="HTML", reply_markup=main_menu_kb(lang))
        return

    # Analyze button: IMPORTANT -> no menu under + only "send chart" (no extra options)
    if txt == t["btn_analyze"]:
        AWAITING_PHOTO[uid] = True
        await update.message.reply_text(
            t["send_chart_only"],
            reply_markup=ReplyKeyboardRemove()
        )
        return

    # If user typed /start etc handled elsewhere
    if txt.startswith("/"):
        return

    # Otherwise gentle hint (no spam)
    if lang not in LANGS:
        await update.message.reply_text(t["need_lang"], reply_markup=lang_kb())
        return

    # If they send random text, keep it clean
    await update.message.reply_text(t["send_chart_only"], reply_markup=ReplyKeyboardRemove())

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = update.effective_user.id

    db = await load_db()
    u = await get_user(db, user_id)

    lang = (u.get("lang") or "").lower()
    if lang not in LANGS:
        await msg.reply_text(UI["en"]["choose_lang"], reply_markup=lang_kb())
        return

    t = UI[lang]
    plan = (u.get("plan", "FREE") or "FREE").upper()

    # FREE limit
    if plan == "FREE":
        rem = await trial_remaining(u)
        if rem <= 0:
            await msg.reply_text(
                t["trial_ended"] + OFFER_TEXT_HTML,
                parse_mode="HTML",
                reply_markup=offer_keyboard()
            )
            return

    # Direct analyze immediately (no extra questions)
    await msg.chat.send_action(ChatAction.TYPING)
    await msg.reply_text(t["analyzing"], reply_markup=ReplyKeyboardRemove())

    photo = msg.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    b = await file.download_as_bytearray()

    caption = msg.caption or ""
    sym_hint, tf_hint = guess_symbol_tf(caption)

    try:
        b64 = image_to_base64_jpeg(bytes(b), max_side=1100, quality=85)
        result = await asyncio.to_thread(openai_analyze_chart, b64, lang)

        # Use model symbol/tf if available
        model_sym = (result.get("symbol") or "").strip().upper()
        model_tf = normalize_tf(result.get("timeframe") or "")
        if model_sym:
            sym_hint = model_sym
        if model_tf:
            tf_hint = model_tf

        # Keep your TP logic unchanged
        result = enforce_tp_rules(result)

        # Localized confidence note (no change in scoring)
        result = apply_confidence_messaging(result, lang)

        trial_line = ""
        if plan == "FREE":
            u["trial_used"] = int(u.get("trial_used", 0) or 0) + 1
            await save_db(db)
            rem_after = await trial_remaining(u)
            trial_line = t["trial_remaining"].format(rem=rem_after, limit=FREE_TRIAL_LIMIT)

        text = format_signal_message(lang, sym_hint, tf_hint, result, trial_line)

        # restore main menu after result (no extra "choose" message)
        AWAITING_PHOTO.pop(user_id, None)
        await msg.reply_text(text, reply_markup=main_menu_kb(lang))

    except Exception as e:
        AWAITING_PHOTO.pop(user_id, None)
        await msg.reply_text(
            "❌ Analysis failed.\n"
            "Try a clearer screenshot (zoom candles) and make sure price/symbol/TF are visible.\n\n"
            "Debug: {}".format(str(e)[:300]),
            reply_markup=main_menu_kb(lang)
        )

# =========================
# Main
# =========================
async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")
    if not ADMIN_IDS:
        print("WARNING: ADMIN_IDS is empty. /setplan will not work and payment requests won't reach you.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("plans", cmd_plans))
    app.add_handler(CommandHandler("setplan", cmd_setplan))

    # Language selection callbacks
    app.add_handler(CallbackQueryHandler(cb_set_lang, pattern=r"^lang_(en|ar|fr)$"))

    # Button callback for "I Paid / Activate"
    app.add_handler(CallbackQueryHandler(paid_activate, pattern="^paid_activate$"))

    # Photo + Text
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
