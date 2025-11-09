import logging
import re
import os
import sqlite3
from datetime import datetime
import json

import gspread
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ✅ импортируем оба типа Credentials
from google.oauth2.credentials import Credentials as UserCredentials
from google.oauth2.service_account import Credentials as ServiceCredentials

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from pyowm import OWM
from pyowm.utils.config import get_default_config



# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events"
]

SPREADSHEET_ID = "1tEkPxovVUmi3HwwnG-92LmsSB9RhqYczh_jrmlY-7KU"

creds_json = os.getenv("SHEETS_CREDENTIALS")
creds_dict = json.loads(creds_json)
gc = gspread.service_account_from_dict(creds_dict)
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))  # твой chat_id (узнается через /id)
OWM_API_KEY = os.getenv("OWM_API_KEY")

# OWM (погода)
if not OWM_API_KEY:
    print("⚠️ OWM_API_KEY не задан, команда /weather не будет работать")
else:
    config_dict = get_default_config()
    config_dict["language"] = "ru"
    owm = OWM(OWM_API_KEY, config_dict)
    mgr = owm.weather_manager()

logging.basicConfig(level=logging.INFO)

# --- Google Calendar auth ---
# --- Google Calendar auth ---
def get_calendar_service():
    creds = None
    if os.path.exists("token.json"):
        creds = UserCredentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("calendar_credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    return build("calendar", "v3", credentials=creds)

# --- Google Sheets ---
# --- Google Sheets ---
from google.oauth2 import service_account

creds_json = os.getenv("SHEETS_CREDENTIALS")
if not creds_json:
    raise ValueError("❌ Переменная SHEETS_CREDENTIALS не найдена. Добавь её в Railway → Settings → Variables.")

try:
    creds_dict = json.loads(creds_json)
except json.JSONDecodeError as e:
    raise ValueError(f"❌ Ошибка чтения JSON: {e}")

# Правильные права доступа
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Авторизация сервисного аккаунта
creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
gc = gspread.authorize(creds)

SPREADSHEET_ID = "1tEkPxovVUmi3HwwnG-92LmsSB9RhqYczh_jrmlY-7KU"
sh = gc.open_by_key(SPREADSHEET_ID)
worksheet = sh.sheet1

print("✅ Авторизация Google Sheets прошла успешно.")

# --- БД для дней рождений ---
def init_db():
    conn = sqlite3.connect("birthdays.db")
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS birthdays (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        date TEXT
    )""")
    conn.commit()
    conn.close()

def add_birthday(name, date):
    conn = sqlite3.connect("birthdays.db")
    cur = conn.cursor()
    cur.execute("INSERT INTO birthdays (name, date) VALUES (?, ?)", (name, date))
    conn.commit()
    conn.close()

def get_all_birthdays():
    conn = sqlite3.connect("birthdays.db")
    cur = conn.cursor()
    cur.execute("SELECT name, date FROM birthdays")
    rows = cur.fetchall()
    conn.close()
    return rows

def get_today_birthdays():
    today = datetime.now().strftime("%d.%m")
    conn = sqlite3.connect("birthdays.db")
    cur = conn.cursor()
    cur.execute("SELECT name, date FROM birthdays WHERE date = ?", (today,))
    rows = cur.fetchall()
    conn.close()
    return rows

# --- Telegram Bot ---
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

# --- Команда /event ---
@dp.message(F.text.startswith("/event"))
async def add_event(message: types.Message):
    text = message.text.replace("/event", "").strip()
    match = re.match(r"(\d{1,2}) (\w{3}) (\d{1,2}:\d{2})-(\d{1,2}:\d{2}) (.+)", text)

    if not match:
        await message.answer("⚠️ Неверный формат.\nПример: /event 1 Oct 9:00-10:00 Встреча")
        return

    day, month, start_time, end_time, summary = match.groups()
    year = datetime.now().year

    start_dt = datetime.strptime(f"{day} {month} {year} {start_time}", "%d %b %Y %H:%M")
    end_dt = datetime.strptime(f"{day} {month} {year} {end_time}", "%d %b %Y %H:%M")

    service = get_calendar_service()
    event = {
        "summary": summary,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Tashkent"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Tashkent"},
    }
    event = service.events().insert(calendarId="primary", body=event).execute()

    await message.answer(f"✅ Событие добавлено: {event.get('htmlLink')}")

# --- Учёт расходов/доходов ---
@dp.message(F.text.regexp(r"^\d+ (доход|расход) .+"))
async def handle_expense(message: types.Message):
    try:
        parts = message.text.strip().split()
        amount = parts[0]
        type_ = parts[1].lower()
        category = " ".join(parts[2:])

        date = datetime.now().strftime("%d.%m.%Y")
        worksheet.append_row([date, amount, type_, category])

        await message.answer(f"✅ Запись добавлена:\n<b>{amount} {type_}</b> — {category}")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {e}")    

# --- Погода ---
@dp.message(F.text.startswith("/weather"))
async def weather(message: types.Message):
    if not OWM_API_KEY or not mgr:
        await message.answer("⚠️ Погодный API-ключ не настроен. Добавь OWM_API_KEY в Railway → Settings → Variables.")
        return

    try:
        city = message.text.replace("/weather", "").strip()
        if not city:
            await message.answer("⚠️ Укажи город.\nПример: /weather Tashkent")
            return

        observation = mgr.weather_at_place(city)
        w = observation.weather
        temp = w.temperature("celsius")["temp"]
        humidity = w.humidity 

        answer = f"🌍 Город: {city}\n"
        answer += f"☁️ Погода: {w.detailed_status}\n"
        answer += f"🌡 Температура: {temp:.1f}°C"
        answer += f"🌡 Температура: {humidity:.1f}°C"

        await message.answer(answer)
    except Exception as e:
        await message.answer(f"⚠️ Не удалось получить погоду: {e}")

# --- Дни рождения ---
@dp.message(F.text.startswith("/bdayadd"))
async def bday_add(message: types.Message):
    try:
        parts = message.text.split()
        if len(parts) < 3:
            await message.answer("⚠️ Формат: /bdayadd Имя ДД.ММ\nПример: /bdayadd Вася 01.10")
            return
        name = " ".join(parts[1:-1])
        date = parts[-1]
        add_birthday(name, date)
        await message.answer(f"✅ День рождения {name} ({date}) добавлен")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {e}")

@dp.message(F.text == "/bdays")
async def bday_list(message: types.Message):
    rows = get_all_birthdays()
    if not rows:
        await message.answer("📭 Список пуст")
    else:
        text = "🎂 Дни рождения:\n"
        for name, date in rows:
            text += f"- {name} 🎂 {date}\n"
        await message.answer(text)

# --- Ежедневная проверка ДР в 00:00 ---
async def check_birthdays():
    rows = get_today_birthdays()
    if rows and ADMIN_CHAT_ID != 0:
        text = "🎉 Сегодня день рождения у:\n"
        for name, date in rows:
            text += f"- {name} 🎂 {date}\n"
        await bot.send_message(ADMIN_CHAT_ID, text)

scheduler.add_job(check_birthdays, "cron", hour=0, minute=0)

# --- Запуск бота ---
if __name__ == "__main__":
    import asyncio

    init_db()

    async def main():
        scheduler.start()
        await dp.start_polling(bot)

    asyncio.run(main())
