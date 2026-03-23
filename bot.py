import os
import logging
import requests as req
from datetime import datetime, date, timedelta

import pytz
import telebot
from flask import Flask, request
from apscheduler.schedulers.background import BackgroundScheduler

# ─── Конфиг ───────────────────────────────────────────────────────────────────
BOT_TOKEN      = "8601794781:AAG981vdtZvVkMHFqQAzT2uzr1UQev7qymk"
CHAT_ID        = 7547639359
PUBLICATION_ID = "9cdf72e4-aa1d-45e8-9fd3-faaca804ffd1"
GROUP_ID       = 59
MSK            = pytz.timezone("Europe/Moscow")
REMINDER_MIN   = 10

WEEKDAYS = {1:"Понедельник",2:"Вторник",3:"Среда",
            4:"Четверг",5:"Пятница",6:"Суббота",7:"Воскресенье"}

LESSON_LINKS = {
    "химия":                   "https://edu.livedigital.space/room/RV6Ob44mHT",
    "русский":                 "https://edu.livedigital.space/room/dCOGY5vp5G",
    "литература":              "https://edu.livedigital.space/room/iITZdyEAyJ",
    "математика":              "https://edu.livedigital.space/room/eiQEb5uL62",
    "иностранный":             "https://edu.livedigital.space/room/C64Ktq2H3f",
    "информатика":             "https://edu.livedigital.space/room/zKpIPyW4ap",
    "физика":                  "https://edu.livedigital.space/room/ONqOOYoZWV",
    "история":                 "https://edu.livedigital.space/room/YicAoD7sSp",
    "обществознание":          "https://edu.livedigital.space/room/GI0mvSHdb5",
    "география":               "https://edu.livedigital.space/room/BWYpJFdbCC",
    "физическая культура":     "https://edu.livedigital.space/room/l3KfYpg710",
    "физкультура":             "https://edu.livedigital.space/room/l3KfYpg710",
    "безопасност":             "https://edu.livedigital.space/room/aLL5wh9ekP",
    "индивидуальным проектом": "https://edu.livedigital.space/room/zKpIPyW4ap",
    "кураторский":             "https://edu.livedigital.space/room/G2czgdkN1z",
}

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ─── Вспомогалки ──────────────────────────────────────────────────────────────
def get_link(subject: str) -> str:
    s = subject.lower()
    for k, v in LESSON_LINKS.items():
        if k in s:
            return v
    return ""


def fetch_lessons(for_date: date) -> list:
    resp = req.post(
        "https://schedule.mstimetables.ru/api/publications/group/lessons",
        json={"groupId": GROUP_ID, "publicationId": PUBLICATION_ID,
              "date": for_date.strftime("%Y-%m-%d")},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    weekday = for_date.isoweekday()
    lessons = [l for l in (data.get("lessons") or []) if l.get("weekday") == weekday]
    return sorted(lessons, key=lambda x: x.get("lesson", 0))


def format_schedule(for_date: date, lessons: list) -> str:
    day = f"{WEEKDAYS.get(for_date.isoweekday(),'?')}, {for_date.strftime('%d.%m.%Y')}"
    if not lessons:
        return f"📅 *{day}*\n\n✅ Пар нет — отдыхай!"
    lines = [f"📅 *{day}*\n"]
    for l in lessons:
        num      = l.get("lesson", "?")
        start    = l.get("startTime", "")
        end      = l.get("endTime", "")
        subject  = (l.get("subject") or {}).get("name", "—")
        teachers = ", ".join(t.get("fio","") for t in (l.get("teachers") or []) if t.get("fio"))
        cabinet  = (l.get("cabinet") or {}).get("name", "")
        link     = get_link(subject)
        block    = f"*{num}. {start}–{end}*\n📚 {subject}"
        if teachers: block += f"\n👤 {teachers}"
        if cabinet:  block += f"\n🏫 {cabinet}"
        if link:     block += f"\n🔗 [Войти]({link})"
        lines.append(block)
    return "\n\n".join(lines)


def send_text(text: str):
    bot.send_message(CHAT_ID, text, parse_mode="Markdown",
                     disable_web_page_preview=True)

# ─── Scheduled jobs ───────────────────────────────────────────────────────────
def morning_send():
    """Каждый день в 08:00 МСК — утреннее расписание."""
    today = datetime.now(MSK).date()
    try:
        lessons = fetch_lessons(today)
        send_text(format_schedule(today, lessons))
        log.info("Утреннее расписание отправлено")
    except Exception as e:
        log.error("morning_send error: %s", e)


def check_reminders():
    """Каждую минуту проверяем — не начинается ли пара через REMINDER_MIN минут."""
    now = datetime.now(MSK)
    today = now.date()
    try:
        lessons = fetch_lessons(today)
    except Exception:
        return
    for l in lessons:
        start_str = l.get("startTime", "")
        if not start_str:
            continue
        h, m = map(int, start_str.split(":"))
        lesson_dt   = MSK.localize(datetime.combine(today, datetime.min.time()).replace(hour=h, minute=m))
        reminder_dt = lesson_dt - timedelta(minutes=REMINDER_MIN)
        # Срабатываем если напоминание было в последние 30 секунд
        diff = (now - reminder_dt).total_seconds()
        if 0 <= diff < 60:
            subject  = (l.get("subject") or {}).get("name", "—")
            end      = l.get("endTime", "")
            teachers = ", ".join(t.get("fio","") for t in (l.get("teachers") or []) if t.get("fio"))
            link     = get_link(subject)
            text     = f"🔔 *Через {REMINDER_MIN} минут пара!*\n\n📚 {subject}\n⏰ {start_str}–{end}"
            if teachers: text += f"\n👤 {teachers}"
            if link:     text += f"\n\n[🔗 Войти на пару]({link})"
            try:
                send_text(text)
                log.info("Напоминание: %s %s", subject, start_str)
            except Exception as e:
                log.error("reminder error: %s", e)

# ─── Telegram bot handlers ────────────────────────────────────────────────────
bot = telebot.TeleBot(BOT_TOKEN)

@bot.message_handler(commands=["start"])
def cmd_start(msg):
    bot.reply_to(msg,
        "Привет! 👋\n\n"
        "📋 /schedule — расписание на сегодня\n"
        "📋 /tomorrow — расписание на завтра\n\n"
        "В 08:00 МСК пришлю расписание автоматически.\n"
        "За 10 минут до каждой пары — ссылку на неё."
    )

@bot.message_handler(commands=["schedule"])
def cmd_schedule(msg):
    today = datetime.now(MSK).date()
    try:
        lessons = fetch_lessons(today)
        send_text(format_schedule(today, lessons))
    except Exception as e:
        bot.reply_to(msg, f"⚠️ Ошибка: {e}")

@bot.message_handler(commands=["tomorrow"])
def cmd_tomorrow(msg):
    tomorrow = datetime.now(MSK).date() + timedelta(days=1)
    try:
        lessons = fetch_lessons(tomorrow)
        send_text(format_schedule(tomorrow, lessons))
    except Exception as e:
        bot.reply_to(msg, f"⚠️ Ошибка: {e}")

@bot.message_handler(func=lambda msg: True)
def cmd_unknown(msg):
    import random
    emojis = [
        "😀","😁","😂","🤣","😃","😄","😅","😆","😉","😊","😋","😎","😍","😘","🥰","😗","😙","😚","🙂","🤗",
        "🤩","🤔","🤨","😐","😑","😶","🙄","😏","😣","😥","😮","🤐","😯","😪","😫","🥱","😴","😌","😛","😜",
        "😝","🤤","😒","😓","😔","😕","🙃","🤑","😲","☹️","🙁","😖","😞","😟","😤","😢","😭","😦","😧","😨",
        "😩","🤯","😬","😰","😱","🥵","🥶","😳","🤪","😵","🥴","😷","🤒","🤕","🤢","🤮","🤧","😇","🥳","🥺",
        "🤠","🤡","🤫","🤭","🧐","😈","👿","👹","👺","💀","☠️","👻","👽","👾","🤖","💩","😺","😸","😹","😻",
        "😼","😽","🙀","😿","😾","🙈","🙉","🙊","👋","🤚","🖐","✋","🖖","👌","🤌","🤏","✌️","🤞","🤟","🤘",
        "🤙","👈","👉","👆","🖕","👇","☝️","👍","👎","✊","👊","🤛","🤜","👏","🙌","👐","🤲","🤝","🙏","✍️",
        "💅","🤳","💪","🦵","🦶","👂","🦻","👃","🧠","🫀","🫁","🦷","🦴","👀","👁","👅","👄","💋","🩸","👶",
        "🧒","👦","👧","🧑","👱","👨","🧔","👩","🧓","👴","👵","🙍","🙎","🙅","🙆","💁","🙋","🧏","🙇","🤦",
        "🤷","👮","🕵","💂","🥷","👷","🤴","👸","👳","👲","🧕","🤵","👰","🤰","🤱","👼","🎅","🤶","🧙","🧝",
        "🧛","🧟","🧞","🧜","🧚","👨‍⚕️","👩‍⚕️","👨‍🎓","👩‍🎓","👨‍🏫","👩‍🏫","👨‍⚖️","👩‍⚖️","👨‍🌾","👩‍🌾","👨‍🍳","👩‍🍳","👨‍🔧","👩‍🔧","👨‍🏭",
        "👩‍🏭","👨‍💼","👩‍💼","👨‍🔬","👩‍🔬","👨‍🎨","👩‍🎨","👨‍🚒","👩‍🚒","👨‍✈️","👩‍✈️","👨‍🚀","👩‍🚀","👨‍💻","👩‍💻","🦸","🦹","🐶","🐱","🐭",
        "🐹","🐰","🦊","🐻","🐼","🐻‍❄️","🐨","🐯","🦁","🐮","🐷","🐸","🐵","🙈","🙉","🙊","🐔","🐧","🐦","🐤",
        "🦆","🦅","🦉","🦇","🐺","🐗","🐴","🦄","🐝","🐛","🦋","🐌","🐞","🐜","🪲","🦟","🦗","🕷","🦂","🐢",
        "🐍","🦎","🦖","🦕","🐙","🦑","🦐","🦞","🦀","🐡","🐠","🐟","🐬","🐳","🐋","🦈","🐊","🐅","🐆","🦓",
        "🦍","🦧","🦣","🐘","🦛","🦏","🐪","🐫","🦒","🦘","🦬","🐃","🐂","🐄","🐎","🐖","🐏","🐑","🦙","🐐",
        "🦌","🐕","🐩","🦮","🐕‍🦺","🐈","🐈‍⬛","🪶","🐓","🦃","🦤","🦚","🦜","🦢","🦩","🕊","🐇","🦝","🦨","🦡",
        "🦫","🦦","🦥","🐁","🐀","🐿","🦔","🌵","🎄","🌲","🌳","🌴","🪵","🌱","🌿","☘️","🍀","🎍","🪴","🎋",
        "🍃","🍂","🍁","🍄","🐚","🪨","🌾","💐","🌷","🌹","🥀","🌺","🌸","🌼","🌻","🌞","🌝","🌛","🌜","🌚",
        "🌕","🌖","🌗","🌘","🌑","🌒","🌓","🌔","🌙","🌟","⭐","🌠","🌌","☁️","⛅","🌤","⛈","🌧","🌨","❄️",
        "🌬","💨","🌪","🌫","🌊","🌈","⚡","🔥","💧","🌍","🌎","🌏","🪐","💫","⚽","🏀","🏈","⚾","🥎","🏐",
        "🏉","🎾","🏸","🏒","🏓","🥊","🎯","🎱","🎮","🎲","🎭","🎨","🎬","🎤","🎧","🎼","🎹","🥁","🎷","🎺",
        "🎸","🪕","🎻","🎙","📻","📺","📷","📸","📹","🎥","📽","🎞","📞","☎️","📟","📠","📡","🔋","🔌","💻",
        "🖥","🖨","⌨️","🖱","💾","💿","📀","🧮","🎁","🎀","🎊","🎉","🎈","🎏","🎐","🧧","🎑","🎃","🎆","🎇",
        "✨","🎋","🎍","🧹","🧺","🧻","🚿","🛁","🪠","🧴","🧷","🧽","🧯","🛒","🚪","🪑","🚽","🛋","🛏","🖼",
        "🪞","🪟","🧸","🪆","🪅","🎎","🎏","🎐","🧿","🪬","🗿","🗺","🧭","🌡","⛱","🎠","🎡","🎢","🎪","🚂",
    ]
    bot.reply_to(msg, random.choice(emojis))

# ─── Flask (webhook) ──────────────────────────────────────────────────────────
flask_app = Flask(__name__)

@flask_app.route("/" + BOT_TOKEN, methods=["POST"])
def webhook():
    update = telebot.types.Update.de_json(request.stream.read().decode("utf-8"))
    bot.process_new_updates([update])
    return "", 200

@flask_app.route("/health")
def health():
    return "OK"

@flask_app.route("/")
def index():
    return "Bot is running"

# ─── Запуск ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Установить webhook
    render_url = os.environ.get("RENDER_EXTERNAL_URL", "")
    if render_url:
        bot.remove_webhook()
        bot.set_webhook(url=f"{render_url}/{BOT_TOKEN}")
        log.info("Webhook установлен: %s", render_url)
    else:
        log.warning("RENDER_EXTERNAL_URL не задан — webhook не установлен")

    # Планировщик
    scheduler = BackgroundScheduler(timezone=MSK)
    scheduler.add_job(morning_send,    "cron", hour=8, minute=0)
    scheduler.add_job(check_reminders, "interval", minutes=1)
    scheduler.start()
    log.info("Планировщик запущен")

    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)
