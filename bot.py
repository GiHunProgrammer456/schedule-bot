import asyncio
import logging
import requests
from datetime import datetime, date, timedelta

import pytz
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ─── Конфиг ───────────────────────────────────────────────────────────────────
BOT_TOKEN      = "8601794781:AAG981vdtZvVkMHFqQAzT2uzr1UQev7qymk"
CHAT_ID        = 7547639359
PUBLICATION_ID = "9cdf72e4-aa1d-45e8-9fd3-faaca804ffd1"
GROUP_ID       = 59
SEND_HOUR_MSK  = 8
SEND_MINUTE    = 0
REMINDER_BEFORE_MIN = 10

MSK = pytz.timezone("Europe/Moscow")

WEEKDAYS = {1: "Понедельник", 2: "Вторник", 3: "Среда",
            4: "Четверг", 5: "Пятница", 6: "Суббота", 7: "Воскресенье"}

# Ключи — подстроки из названия предмета (нижний регистр)
LESSON_LINKS = {
    "химия":                         "https://edu.livedigital.space/room/RV6Ob44mHT",
    "русский":                       "https://edu.livedigital.space/room/dCOGY5vp5G",
    "литература":                    "https://edu.livedigital.space/room/iITZdyEAyJ",
    "математика":                    "https://edu.livedigital.space/room/eiQEb5uL62",
    "иностранный":                   "https://edu.livedigital.space/room/C64Ktq2H3f",
    "информатика":                   "https://edu.livedigital.space/room/zKpIPyW4ap",
    "физика":                        "https://edu.livedigital.space/room/ONqOOYoZWV",
    "история":                       "https://edu.livedigital.space/room/SacbAzZ4mg",
    "обществознание":                "https://edu.livedigital.space/room/GI0mvSHdb5",
    "география":                     "https://edu.livedigital.space/room/BWYpJFdbCC",
    "физическая культура":           "https://edu.livedigital.space/room/l3KfYpg710",
    "физкультура":                   "https://edu.livedigital.space/room/l3KfYpg710",
    "безопасност":                   "https://edu.livedigital.space/room/aLL5wh9ekP",
    "индивидуальным проектом":       "https://edu.livedigital.space/room/zKpIPyW4ap",
    "кураторский":                   "https://edu.livedigital.space/room/G2czgdkN1z",
}

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


# ─── Вспомогалки ──────────────────────────────────────────────────────────────
def get_lesson_link(subject_name: str) -> str:
    name_lower = subject_name.lower()
    for key, url in LESSON_LINKS.items():
        if key in name_lower:
            return url
    return ""


def get_lessons_for_date(for_date: date) -> list:
    resp = requests.post(
        "https://schedule.mstimetables.ru/api/publications/group/lessons",
        json={
            "groupId": GROUP_ID,
            "publicationId": PUBLICATION_ID,
            "date": for_date.strftime("%Y-%m-%d"),
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    weekday = for_date.isoweekday()
    lessons = [l for l in (data.get("lessons") or []) if l.get("weekday") == weekday]
    return sorted(lessons, key=lambda x: x.get("lesson", 0))


def format_schedule(for_date: date, lessons: list) -> str:
    weekday = for_date.isoweekday()
    day_str = f"{WEEKDAYS.get(weekday, '?')}, {for_date.strftime('%d.%m.%Y')}"

    if not lessons:
        return f"📅 *{day_str}*\n\n✅ Пар нет — можно отдыхать!"

    lines = [f"📅 *{day_str}*\n"]
    for lesson in lessons:
        num      = lesson.get("lesson", "?")
        start    = lesson.get("startTime", "")
        end      = lesson.get("endTime", "")
        subject  = (lesson.get("subject") or {}).get("name", "—")
        teachers = ", ".join(t.get("fio", "") for t in (lesson.get("teachers") or []) if t.get("fio"))
        cabinet  = (lesson.get("cabinet") or {}).get("name", "")
        link     = get_lesson_link(subject)

        block = f"*{num}. {start}–{end}*\n📚 {subject}"
        if teachers:
            block += f"\n👤 {teachers}"
        if cabinet:
            block += f"\n🏫 {cabinet}"
        if link:
            block += f"\n🔗 [Войти на пару]({link})"
        lines.append(block)

    return "\n\n".join(lines)


# ─── Напоминалки за 10 минут ──────────────────────────────────────────────────
async def send_reminder(bot, lesson: dict, for_date: date) -> None:
    subject  = (lesson.get("subject") or {}).get("name", "—")
    start    = lesson.get("startTime", "")
    end      = lesson.get("endTime", "")
    teachers = ", ".join(t.get("fio", "") for t in (lesson.get("teachers") or []) if t.get("fio"))
    link     = get_lesson_link(subject)

    text = f"🔔 *Через {REMINDER_BEFORE_MIN} минут пара\\!*\n\n📚 {subject}\n⏰ {start}–{end}"
    if teachers:
        text += f"\n👤 {teachers}"
    if link:
        text += f"\n\n[🔗 Войти на пару]({link})"

    await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="MarkdownV2")
    log.info("Напоминание отправлено: %s %s", subject, start)


async def schedule_day_reminders(bot, lessons: list, for_date: date) -> None:
    """Запускает asyncio-таймер для каждой пары дня."""
    for lesson in lessons:
        start_str = lesson.get("startTime", "")
        if not start_str:
            continue
        h, m = map(int, start_str.split(":"))
        lesson_dt   = MSK.localize(datetime.combine(for_date, datetime.min.time()).replace(hour=h, minute=m))
        reminder_dt = lesson_dt - timedelta(minutes=REMINDER_BEFORE_MIN)
        now         = datetime.now(MSK)

        if reminder_dt <= now:
            continue  # уже прошло

        wait = (reminder_dt - now).total_seconds()
        subject = (lesson.get("subject") or {}).get("name", "?")
        log.info("Напоминание для '%s' через %.0f сек", subject, wait)
        asyncio.create_task(_delayed_reminder(bot, lesson, for_date, wait))


async def _delayed_reminder(bot, lesson, for_date, wait_seconds):
    await asyncio.sleep(wait_seconds)
    try:
        await send_reminder(bot, lesson, for_date)
    except Exception as e:
        log.error("Ошибка напоминания: %s", e)


# ─── Команды ──────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Я слежу за твоим расписанием.\n\n"
        "📋 /schedule — расписание на сегодня\n"
        "📋 /tomorrow — расписание на завтра\n\n"
        "Каждый день в 08:00 МСК пришлю расписание.\n"
        "За 10 минут до каждой пары пришлю ссылку на неё."
    )


async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    today = datetime.now(MSK).date()
    try:
        lessons = get_lessons_for_date(today)
        text    = format_schedule(today, lessons)
    except Exception as e:
        text = f"⚠️ Ошибка: {e}"
    await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)


async def cmd_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tomorrow = datetime.now(MSK).date() + timedelta(days=1)
    try:
        lessons = get_lessons_for_date(tomorrow)
        text    = format_schedule(tomorrow, lessons)
    except Exception as e:
        text = f"⚠️ Ошибка: {e}"
    await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)


# ─── Основной цикл ────────────────────────────────────────────────────────────
async def daily_loop(bot) -> None:
    while True:
        now    = datetime.now(MSK)
        target = now.replace(hour=SEND_HOUR_MSK, minute=SEND_MINUTE, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)

        wait = (target - now).total_seconds()
        log.info("Следующая рассылка через %.0f сек (%s МСК)", wait, target.strftime("%d.%m %H:%M"))
        await asyncio.sleep(wait)

        today = datetime.now(MSK).date()
        try:
            lessons = get_lessons_for_date(today)
            text    = format_schedule(today, lessons)
            await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="Markdown",
                                   disable_web_page_preview=True)
            log.info("Утреннее расписание отправлено на %s", today)

            # Запланировать напоминания
            await schedule_day_reminders(bot, lessons, today)
        except Exception as e:
            log.error("Ошибка дневного цикла: %s", e)


async def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("schedule", cmd_schedule))
    app.add_handler(CommandHandler("tomorrow", cmd_tomorrow))

    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        await daily_loop(app.bot)


if __name__ == "__main__":
    asyncio.run(main())
