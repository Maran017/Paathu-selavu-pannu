from dotenv import load_dotenv
load_dotenv()  # must be at the very top, before reading env variables

import telebot
import os
from telebot import types
import csv, json, re, traceback
from datetime import datetime
import pytz

import cv2
from paddleocr import PaddleOCR

IST = pytz.timezone("Asia/Kolkata")
# ================= TOKENS =================
TELEGRAM_BOT_TOKEN = os.getenv("BOT_TOKEN")
print("BOT TOKEN:", TELEGRAM_BOT_TOKEN)  # debug: check if token is loaded
# =========================================

if TELEGRAM_BOT_TOKEN is None:
    raise ValueError("BOT_TOKEN not found in environment variables!")

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# PaddleOCR (BEST for bills)
ocr = PaddleOCR(
    use_textline_orientation=True,
    lang="en"
)

DATA_FOLDER = "user_data"
os.makedirs(DATA_FOLDER, exist_ok=True)

pending_entries = {}

# ================= UTIL =================

def get_user_file(user_id):
    return os.path.join(DATA_FOLDER, f"{user_id}.json")

def load_user_data(user_id):
    if os.path.exists(get_user_file(user_id)):
        with open(get_user_file(user_id), "r") as f:
            return json.load(f)
    return []

def save_user_data(user_id, data):
    with open(get_user_file(user_id), "w") as f:
        json.dump(data, f, indent=2)

def add_expense(user_id, date, time, place, category, amount):
    data = load_user_data(user_id)
    data.append({
        "date": date,
        "time": time,
        "place": place,
        "category": category,
        "amount": float(amount)
    })
    save_user_data(user_id, data)

def get_total_expense(user_id):
    return sum(e["amount"] for e in load_user_data(user_id))

def reset_data(user_id):
    if os.path.exists(get_user_file(user_id)):
        os.remove(get_user_file(user_id))

def create_csv(user_id):
    data = load_user_data(user_id)
    path = os.path.join(DATA_FOLDER, f"{user_id}_expenses.csv")

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Time", "Place", "Category", "Amount"])
        for d in data:
            writer.writerow([d["date"], d["time"], d["place"], d["category"], d["amount"]])
        writer.writerow([])
        writer.writerow(["", "", "", "TOTAL", get_total_expense(user_id)])

    return path

# ================= MENUS =================

def main_menu():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True)
    m.add("📸 Add by Bill Photo", "✏️ Add Manually")
    m.add("💰 Total Expense", "📥 Download CSV")
    m.add("🗑️ Reset Data", "🚫 Cancel")
    return m

@bot.message_handler(func=lambda m: m.text == "🚫 Cancel")
def cancel_current_process(message):
    chat_id = message.chat.id

    # Remove any pending state
    if chat_id in pending_entries:
        del pending_entries[chat_id]

    bot.send_message(
        chat_id,
        "🚫 Current process cancelled.\nBack to main menu.",
        reply_markup=main_menu()
    )


from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

def confirm_menu():
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("✅ Yes", callback_data="confirm_yes"),
        InlineKeyboardButton("❌ No", callback_data="confirm_no")
    )
    return kb

def edit_menu():
    kb = InlineKeyboardMarkup()
    for f in ["date", "time", "place", "category", "amount"]:
        kb.add(InlineKeyboardButton(f.capitalize(), callback_data=f"edit_{f}"))
    return kb


def date_menu():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True)
    m.add("📅 Use Current Date", "✏️ Enter Date Manually")
    m.add("🚫 Cancel")
    return m


def time_menu():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True)
    m.add("🕐 Use Current Time", "✏️ Enter Time Manually")
    m.add("🚫 Cancel")
    return m


def category_menu():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True)
    m.add("🍔 Food", "🚕 Travel", "⛽ Fuel")
    m.add("🛒 Groceries", "🛍️ Shopping", "🎬 Entertainment")
    m.add("🏥 Medical", "💡 Utilities", "🎓 Education")
    m.add("📱 Subscription", "🏨 Hotel", "🧾 Bills")
    m.add("📦 Other")
    m.add("🚫 Cancel")
    return m


# ================= START =================

@bot.message_handler(commands=["start", "help"])
def start(message):
    user_name = message.from_user.first_name or "Friend"

    bot.send_message(
        message.chat.id,
        f"Hi {user_name} *Welcome to Paathu Selavu Pannu 👋!*\n\n"
        f"📊 Track your *Expenses* easily\n"
        f"📸 Upload bill photos to save time\n"
        f"✍️ Manual entry available anytime",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

# ================= OCR =================

def extract_text_from_bill(image_path):
    result = ocr.predict(image_path)

    lines = []
    for res in result:
        if "rec_texts" in res:
            for txt in res["rec_texts"]:
                if txt.strip():
                    lines.append(txt.strip())

    return "\n".join(lines)

import re

AMOUNT_KEYWORDS = [
    "amount",
    "grand total",
    "net amount",
    "total amount",
    "amount payable",
    "total payable",
    "amount paid",
    "cash paid",
    "paid amount",
    "final amount",
    "invoice total",
    "bill total",
    "bill value",
    "total fare",
    "fare amount",
    "total rs",
    "total inr",
    "total value",
    "total",
    "gross amount"
]

def extract_amount(text):
    text = text.lower()
    candidates = []

    # 1️⃣ KEYWORD-BASED AMOUNTS (collect all)
    for key in AMOUNT_KEYWORDS:
        pattern = rf"{key}[:\-]?\s*(?:rs\.?|₹|rupees)?\s*([\d,]+(?:\.\d{{1,2}})?)"
        pattern = pattern.replace("{{1,2}}", "{1,2}")

        for m in re.findall(pattern, text):
            val = float(m.replace(",", ""))
            if val >= 50:
                candidates.append(val)

    # 2️⃣ CURRENCY / SUFFIX BASED (final bill usually here)
    currency_patterns = [
        r"rs\.?\s*([\d,]+(?:\.\d{1,2})?)",
        r"rupees\s*([\d,]+(?:\.\d{1,2})?)",
        r"₹\s*([\d,]+(?:\.\d{1,2})?)",
        r"([\d,]+(?:\.\d{1,2})?)\s*/-",
        r"([\d,]+(?:\.\d{1,2})?)\s*only"
    ]

    for p in currency_patterns:
        for m in re.findall(p, text):
            val = float(m.replace(",", ""))
            if val >= 50:
                candidates.append(val)

    if not candidates:
        return ""

    return str(max(set(candidates)))


def extract_date(text):
    m = re.search(r"(\d{2}[/-]\d{2}[/-]\d{2,4})", text)
    return m.group(1) if m else ""


def extract_time(text):
    m = re.search(r"(\d{1,2}:\d{2})", text)
    return m.group(1) if m else ""

BUSINESS_WORDS = [
    "travels","store","mart","hotel","restaurant","cafe",
    "bakery","medical","pharmacy","shop","agency","unit",
    "enterprise","traders","fashion","textiles","electronics",
    "mobiles","footwear","supermarket","center","centre",
    "food","foods","pvt","ltd","stores","store"
]

AREA_WORDS = [
    "nagar","pur","patti","pettai","kottai","palayam",
    "town","city","airport","delhi","chennai","madurai"
]

GENERIC_ADDRESS = [
    "road","rd","street","main"
]

KNOWN_BRANDS = [
    # 🛍️ Fashion & Retail
    "zudio",
    "trends",
    "pantaloons",
    "westside",
    "max",
    "lifestyle",
    "reliance",
    "reliance trends",
    "reliance digital",
    "dmart",

    # 📱 Electronics / Mobile
    "poorvika",
    "sangeetha",
    "croma",
    "vijay sales",

    # 🍔 Food / Café
    "kfc",
    "dominos",
    "pizza hut",
    "mcdonalds",
    "starbucks",
    "subway",

    # 🛒 Online / Delivery
    "amazon",
    "flipkart",
    "zomato",
    "swiggy"
]

def extract_place(lines):
    shop_name = ""
    area_name = ""

    for line in lines[:12]:
        raw = line.strip()
        lower = raw.lower()
        clean = re.sub(r"[^a-z ]", "", lower).strip()

        if len(clean) < 3:
            continue

        # ⭐ 1️⃣ BRAND MATCH (HIGHEST PRIORITY)
        for brand in KNOWN_BRANDS:
            if brand in clean:
                return brand.title()

        # 🏪 2️⃣ BUSINESS NAME (company / shop)
        if any(word in clean for word in BUSINESS_WORDS):
            # Avoid generic address-only lines
            if not any(addr in clean for addr in GENERIC_ADDRESS):
                shop_name = raw.title()

        # 🌆 3️⃣ AREA / CITY (fallback)
        if any(word in clean for word in AREA_WORDS):
            area_name = raw.title()

        # 🌆 Special case: FULL CAPS city names (ARUPPUKOTTAI)
        if raw.isupper() and len(clean) > 5 and clean.isalpha():
            area_name = raw.title()

    # ✅ FINAL PRIORITY
    if shop_name:
        return shop_name
    if area_name:
        return area_name

    return ""


CATEGORY_KEYWORDS = {

    "Medical": [
        "hospital","clinic","pharmacy","chemist","doctor",
        "tablet","capsule","syrup","injection","medicine",
        "lab","laboratory","scan","xray","ecg","bandage",
        "healthcare","diagnostic","medicalstore"
    ],

    "Hotel": [
        "hotel","lodge","resort","inn","hostel",
        "room","stay","checkin","checkout",
        "oyo","booking","accommodation"
    ],

    "Food": [
        "restaurant","cafe","coffee","tea","bakery","canteen","food",
        "kfc","mcdonald","domino","pizza","burger","shawarma",
        "biryani","meal","combo","lunch","dinner",
        "zomato","swiggy","parotta","dosa","idly","pongal",
        "poori","friedrice","noodles","grill","dine in","take away"
    ],

    "Groceries": [
        "grocery","groceries","supermarket","mart","provision",
        "rice","wheat","atta","flour","curd","butter","ghee",
        "vegetable","fruit","onion","tomato","potato",
        "dhal","masala","spices","salt","sugar","dal","groundnutoil","sunfloweroil" ,"cookingoil"
    ],

    "Fuel": [
        "petrol","diesel","fuel","cng",
        "petrolpump","fillingstation",
        "indianoil","bharatpetroleum","hindustanpetroleum"
    ],

    "Travel": [
        "uber","ola","rapido","taxi","cab","auto",
        "bus","train","metro","railway",
        "ticket","travels","transport","journey"
        "travels","trip","kilometer","kilometre","km",
        "vehicle","veh","driver","driverbatta",
        "toll","tollgate","route","from","to"
    ],

    "Shopping": [
        "shirt","tshirt","t-shirt","pant","pants","trouser",
        "jeans","dress","kurti","saree","top","jacket",
        "shoe","shoes","chappal","slipper","sandals",
        "belt","wallet","handbag","backpack",
        "watch","garment","clothing","fashion"
    ],

    "Entertainment": [
        "movie","cinema","theatre","screen",
        "netflix","primevideo","hotstar",
        "bookmyshow","show","concert","event"
    ],

    "Education": [
        "school","college","university","tuition",
        "coaching","course","training","exam",
        "book","notebook","stationery","education"
    ],

    "Utilities": [
        "electricity","water","gas","lpg",
        "wifi","broadband","internet",
        "mobile","recharge","dataplan","postpaid","prepaid"
    ],

    "GADGETS":[
        "earbuds", "headphones", "bluetooth", "smartwatch",
        "mobile", "laptop", "tablet", "charger",
        "powerbank", "camera", "speaker",
        "router", "modem", "keyboard", "mouse",
        "usb", "ssd", "harddisk", "monitor",
        "printer", "projector"
    ],

     "MECHANICAL": [
        "spanner", "wrench", "hammer", "screwdriver",
        "drill", "grinder", "lathe", "cutter",
        "plier", "measuring tape", "vernier",
        "caliper", "bearing", "gear", "chain",
        "compressor", "welding", "soldering",
        "tool kit", "machine oil"
    ]

}

def detect_category(text):
    t = text.lower()
    scores = {}

    for category, keywords in CATEGORY_KEYWORDS.items():
        scores[category] = sum(1 for kw in keywords if kw in t)

    best_category = max(scores, key=scores.get)

    return best_category if scores[best_category] > 0 else "Other"


def parse_bill(text):
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    full = " ".join(lines)

    date = extract_date(full)
    time = extract_time(full)
    amount = extract_amount(full)

    if not date:
        date = datetime.now().strftime("%d-%m-%Y")
    if not time:
        time = datetime.now().strftime("%H:%M")

    place = extract_place(lines)
    category = detect_category(full)

    return {
        "date": date,
        "time": time,
        "place": place,
        "category": category,
        "amount": amount
    }

# ================= BILL PHOTO =================

@bot.message_handler(func=lambda m: m.text == "📸 Add by Bill Photo")
def bill_start(message):
    pending_entries[message.chat.id] = {"state": "bill_photo"}
    bot.send_message(message.chat.id, "📸 Send the bill photo clearly")

@bot.message_handler(content_types=["photo"])
def bill_photo_handler(message):
    try:
        entry = pending_entries.get(message.chat.id)
        if not entry or entry.get("state") != "bill_photo":
            return
        
        processing_msg = bot.send_message(
            message.chat.id,
            "🧾 Bill received!\n⏳ Processing, please wait..."
        )

        # Download image
        file_id = message.photo[-1].file_id
        file_info = bot.get_file(file_id)
        file_bytes = bot.download_file(file_info.file_path)

        img_path = f"bill_{message.chat.id}.jpg"
        with open(img_path, "wb") as f:
            f.write(file_bytes)

        # OCR (NO preprocessing)
        text = extract_text_from_bill(img_path)

        # Delete image immediately (privacy)
        os.remove(img_path)

        print("===== OCR TEXT =====")
        print(text)
        print("====================")

        if not text.strip():
            raise ValueError("Empty OCR")

        # Rule-based extraction
        data = parse_bill(text)

        bot.delete_message(
            message.chat.id,
            processing_msg.message_id
        )

        pending_entries[message.chat.id] = {
            "state": "confirm",
            "data": data
        }

        bot.send_message(
            message.chat.id,
            f"""📋 *Confirm Details*

📅 Date: {data.get('date') or '—'}
🕐 Time: {data.get('time') or '—'}
📍 Place: {data.get('place') or '—'}
📁 Category: {data.get('category') or '—'}
💵 Amount: ₹{data.get('amount') or '—'}""",
            parse_mode="Markdown",
            reply_markup=confirm_menu()
        )

    except Exception as e:
        traceback.print_exc()
        bot.send_message(
            message.chat.id,
            "❌ Couldn't read bill clearly.\nTry another image or use manual entry.",
            reply_markup=main_menu()
        )

@bot.callback_query_handler(func=lambda c: c.data in ["confirm_yes", "confirm_no"])
def handle_confirmation(call):
    chat_id = call.message.chat.id
    entry = pending_entries.get(chat_id)

    if not entry:
        return

    if call.data == "confirm_yes":
        d = entry["data"]
        add_expense(chat_id, d["date"], d["time"], d["place"], d["category"], d["amount"])
        pending_entries.pop(chat_id, None)

        bot.send_message(chat_id, "✅ Expense saved!", reply_markup=main_menu())
        return

    entry["state"] = "edit_field"
    bot.send_message(chat_id, "❓ Which detail is wrong?", reply_markup=edit_menu())


@bot.callback_query_handler(func=lambda c: c.data.startswith("edit_"))
def edit_field(call):
    chat_id = call.message.chat.id
    field = call.data.replace("edit_", "")

    pending_entries[chat_id]["state"] = "edit_value"
    pending_entries[chat_id]["field"] = field

    bot.send_message(chat_id, f"✏️ Enter correct {field}:")


@bot.message_handler(func=lambda m: pending_entries.get(m.chat.id, {}).get("state") == "edit_value")
def receive_edit(m):
    entry = pending_entries[m.chat.id]
    field = entry["field"]

    entry["data"][field] = m.text.strip()
    entry["state"] = "confirm"

    d = entry["data"]

    bot.send_message(
        m.chat.id,
        f"""🔁 *Confirm Again*

📅 Date: {d['date']}
🕐 Time: {d['time']}
📍 Place: {d['place']}
📁 Category: {d['category']}
💵 Amount: ₹{d['amount']}""",
        parse_mode="Markdown",
        reply_markup=confirm_menu()
    )


# ================= MANUAL ENTRY =================

@bot.message_handler(func=lambda m: m.text == "✏️ Add Manually")
def manual_start(message):
    pending_entries[message.chat.id] = {"state": "amount"}
    bot.send_message(
        message.chat.id,
        "💵 Enter amount:",
        reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🚫 Cancel")
    )


@bot.message_handler(
    func=lambda m: m.chat.id in pending_entries
    and pending_entries[m.chat.id]["state"]
    not in ["confirm", "bill_photo"]
)
def manual_flow(message):
    entry = pending_entries[message.chat.id]

    # -------- AMOUNT --------
    if entry["state"] == "amount":
        entry["amount"] = re.sub(r"[^\d.]", "", message.text)
        entry["state"] = "date"
        bot.send_message(
            message.chat.id,
            "📅 Use current date or enter manually?",
            reply_markup=date_menu()
        )

    # -------- DATE OPTION --------
    elif entry["state"] == "date":
        if "Current" in message.text:
            entry["date"] = datetime.now(IST).strftime("%d-%m-%Y")
            entry["state"] = "time"
            bot.send_message(
                message.chat.id,
                "🕐 Use current time or enter manually?",
                reply_markup=time_menu()
            )
        else:
            entry["state"] = "date_manual"
            m = types.ReplyKeyboardMarkup(resize_keyboard=True)
            m.add("🚫 Cancel")

            bot.send_message(
                message.chat.id,
                "✏️ Enter date (DD-MM-YYYY):",
                reply_markup=m
            )

    # -------- DATE MANUAL INPUT --------
    elif entry["state"] == "date_manual":
        entry["date"] = message.text
        entry["state"] = "time"
        bot.send_message(
            message.chat.id,
            "🕐 Use current time or enter manually?",
            reply_markup=time_menu()
        )

    # -------- TIME OPTION --------
    elif entry["state"] == "time":
        if "Current" in message.text:
            entry["time"] = datetime.now(IST).strftime("%H:%M")
            entry["state"] = "place"
            m = types.ReplyKeyboardMarkup(resize_keyboard=True)
            m.add("🚫 Cancel")

            bot.send_message(
                message.chat.id,
                "📍 Enter place:",
                reply_markup=m
            )

        else:
            entry["state"] = "time_manual"
            m = types.ReplyKeyboardMarkup(resize_keyboard=True)
            m.add("🚫 Cancel")

            bot.send_message(
                message.chat.id,
                "✏️ Enter time (HH:MM):",
                reply_markup=m
            )

    # -------- TIME MANUAL INPUT --------
    elif entry["state"] == "time_manual":
        entry["time"] = message.text
        entry["state"] = "place"
        bot.send_message(
            message.chat.id,
            "📍 Enter place:"
        )

    # -------- PLACE --------
    elif entry["state"] == "place":
        entry["place"] = message.text
        entry["state"] = "category"
        bot.send_message(
            message.chat.id,
            "📁 Select category:",
            reply_markup=category_menu()
        )

    # -------- CATEGORY --------
    elif entry["state"] == "category":
        entry["category"] = message.text.split(" ", 1)[-1]

        pending_entries[message.chat.id] = {
            "state": "confirm",
            "data": entry
        }

        d = entry
        bot.send_message(
            message.chat.id,
            f"""📋 Confirm Details

📅 {d['date']}
🕐 {d['time']}
📍 {d['place']}
📁 {d['category']}
💵 ₹{d['amount']}""",
            reply_markup=confirm_menu()
        )

# ================= OTHER =================

@bot.message_handler(func=lambda m: m.text == "💰 Total Expense")
def total(message):
    bot.send_message(message.chat.id, f"💰 Total: ₹{get_total_expense(message.chat.id)}")

@bot.message_handler(func=lambda m: m.text == "📥 Download CSV")
def csv_download(message):
    if not load_user_data(message.chat.id):
        bot.send_message(message.chat.id, "No data yet!")
        return
    path = create_csv(message.chat.id)
    with open(path, "rb") as f:
        bot.send_document(message.chat.id, f)
    os.remove(path)

@bot.message_handler(func=lambda m: m.text == "🗑️ Reset Data")
def reset(message):
    reset_data(message.chat.id)
    bot.send_message(message.chat.id, "🗑️ All data cleared!", reply_markup=main_menu())

# ================= RUN =================

if __name__ == "__main__":
    print("🤖 Bot running with PaddleOCR...")
    bot.infinity_polling()
