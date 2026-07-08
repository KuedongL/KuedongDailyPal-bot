import logging
import os
import sqlite3
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
DB_PATH = os.environ.get("DB_PATH", "bot.db")
DEFAULT_LOCATION = "Luodong, Taiwan"
DAILY_HOUR = int(os.environ.get("DAILY_HOUR", "7"))
TIMEZONE = "Asia/Taipei"

WEATHER_CODES = {
    0: "晴朗", 1: "大致晴朗", 2: "多雲", 3: "陰天",
    45: "起霧", 48: "霧淞",
    51: "毛毛雨(小)", 53: "毛毛雨(中)", 55: "毛毛雨(大)",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "凍雨(小)", 67: "凍雨(大)",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "陣雨(小)", 81: "陣雨(中)", 82: "陣雨(大)",
    85: "陣雪(小)", 86: "陣雪(大)",
    95: "雷雨", 96: "雷雨伴冰雹", 99: "強雷雨伴冰雹",
}


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS chats (chat_id INTEGER PRIMARY KEY, location TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS todos ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "chat_id INTEGER, todo_date TEXT, content TEXT, done INTEGER DEFAULT 0)"
    )
    return conn


def ensure_chat(chat_id: int):
    conn = db()
    conn.execute(
        "INSERT OR IGNORE INTO chats (chat_id, location) VALUES (?, ?)",
        (chat_id, DEFAULT_LOCATION),
    )
    conn.commit()
    conn.close()


def get_location(chat_id: int) -> str:
    conn = db()
    row = conn.execute("SELECT location FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.close()
    return row[0] if row and row[0] else DEFAULT_LOCATION


def set_location(chat_id: int, location: str):
    conn = db()
    conn.execute(
        "INSERT INTO chats (chat_id, location) VALUES (?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET location = excluded.location",
        (chat_id, location),
    )
    conn.commit()
    conn.close()


def parse_date(text: str) -> str | None:
    text = text.strip()
    if text in ("今天", "today"):
        return date.today().isoformat()
    if text in ("明天", "tomorrow"):
        return (date.today() + timedelta(days=1)).isoformat()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        try:
            year = date.today().year
            return datetime.strptime(f"{year}-{text}", "%Y-%m-%d").date().isoformat()
        except ValueError:
            return None


def add_todo(chat_id: int, todo_date: str, content: str):
    conn = db()
    conn.execute(
        "INSERT INTO todos (chat_id, todo_date, content) VALUES (?, ?, ?)",
        (chat_id, todo_date, content),
    )
    conn.commit()
    conn.close()


def list_todos(chat_id: int, todo_date: str):
    conn = db()
    rows = conn.execute(
        "SELECT id, content, done FROM todos WHERE chat_id = ? AND todo_date = ? ORDER BY id",
        (chat_id, todo_date),
    ).fetchall()
    conn.close()
    return rows


def mark_done(chat_id: int, todo_id: int) -> bool:
    conn = db()
    cur = conn.execute(
        "UPDATE todos SET done = 1 WHERE chat_id = ? AND id = ?", (chat_id, todo_id)
    )
    conn.commit()
    changed = cur.rowcount > 0
    conn.close()
    return changed


def fetch_weather_text(location: str) -> str:
    geo = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": location, "count": 5, "language": "zh"},
        timeout=10,
    ).json()
    results = geo.get("results")
    if not results:
        return f"找不到地點「{location}」,請確認名稱(建議用英文,如 Luodong, Taiwan)。"

    place = next((r for r in results if r.get("country_code") == "TW"), results[0])
    lat, lon = place["latitude"], place["longitude"]
    name = place.get("name", location)

    forecast = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode",
            "timezone": TIMEZONE,
            "forecast_days": 1,
        },
        timeout=10,
    ).json()

    daily = forecast["daily"]
    code = daily["weathercode"][0]
    desc = WEATHER_CODES.get(code, "未知天氣")
    tmax = daily["temperature_2m_max"][0]
    tmin = daily["temperature_2m_min"][0]
    pop = daily["precipitation_probability_max"][0]

    return (
        f"📍 {name} 今日天氣\n"
        f"{desc}\n"
        f"氣溫:{tmin}°C ~ {tmax}°C\n"
        f"降雨機率:{pop}%"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_chat(update.effective_chat.id)
    await update.message.reply_text(
        "嗨!我是每日小幫手 🐾\n"
        "每天 7:00 會傳今日天氣和代辦事項給你。\n\n"
        "指令:\n"
        "/list [日期] - 查看代辦事項(預設今天)\n"
        "/add <日期> <內容> - 新增代辦事項\n"
        "/done <編號> - 標記完成\n"
        "/weather [地點] - 查詢天氣\n"
        "/setlocation <地點> - 設定預設地點(目前預設:宜蘭羅東)"
    )


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ensure_chat(chat_id)
    if context.args:
        todo_date = parse_date(" ".join(context.args))
        if not todo_date:
            await update.message.reply_text("日期格式看不懂,請用 YYYY-MM-DD、今天或明天。")
            return
    else:
        todo_date = date.today().isoformat()

    rows = list_todos(chat_id, todo_date)
    if not rows:
        await update.message.reply_text(f"{todo_date} 沒有代辦事項。")
        return

    lines = [f"📋 {todo_date} 代辦事項:"]
    for todo_id, content, done in rows:
        mark = "✅" if done else "◻️"
        lines.append(f"{mark} [{todo_id}] {content}")
    await update.message.reply_text("\n".join(lines))


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ensure_chat(chat_id)
    if len(context.args) < 2:
        await update.message.reply_text("用法:/add <日期> <內容>\n例如:/add 今天 買晚餐")
        return

    todo_date = parse_date(context.args[0])
    if not todo_date:
        await update.message.reply_text("日期格式看不懂,請用 YYYY-MM-DD、今天或明天。")
        return

    content = " ".join(context.args[1:])
    add_todo(chat_id, todo_date, content)
    await update.message.reply_text(f"已新增 {todo_date} 的代辦:{content}")


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("用法:/done <編號>(編號可從 /list 看到)")
        return

    todo_id = int(context.args[0])
    if mark_done(chat_id, todo_id):
        await update.message.reply_text(f"已將編號 {todo_id} 標記為完成 ✅")
    else:
        await update.message.reply_text("找不到這個編號,請先用 /list 確認。")


async def cmd_weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ensure_chat(chat_id)
    location = " ".join(context.args) if context.args else get_location(chat_id)
    await update.message.reply_text(fetch_weather_text(location))


async def cmd_setlocation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ensure_chat(chat_id)
    if not context.args:
        await update.message.reply_text(
            f"用法:/setlocation <地點>\n目前設定:{get_location(chat_id)}"
        )
        return
    location = " ".join(context.args)
    set_location(chat_id, location)
    await update.message.reply_text(f"已將預設地點設為:{location}")


async def daily_job(context: ContextTypes.DEFAULT_TYPE):
    conn = db()
    chat_ids = [r[0] for r in conn.execute("SELECT chat_id FROM chats").fetchall()]
    conn.close()

    today = date.today().isoformat()
    for chat_id in chat_ids:
        location = get_location(chat_id)
        try:
            weather = fetch_weather_text(location)
        except Exception as exc:
            log.warning("weather fetch failed for %s: %s", chat_id, exc)
            weather = "(天氣查詢失敗)"

        rows = list_todos(chat_id, today)
        if rows:
            todo_lines = [
                f"{'✅' if done else '◻️'} [{tid}] {content}" for tid, content, done in rows
            ]
            todo_text = "\n".join(todo_lines)
        else:
            todo_text = "今天沒有安排的代辦事項。"

        text = f"☀️ 早安!今天的行程\n\n{weather}\n\n📋 代辦事項:\n{todo_text}"
        try:
            await context.bot.send_message(chat_id=chat_id, text=text)
        except Exception as exc:
            log.warning("send to %s failed: %s", chat_id, exc)


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("weather", cmd_weather))
    app.add_handler(CommandHandler("setlocation", cmd_setlocation))

    app.job_queue.run_daily(
        daily_job,
        time=time(hour=DAILY_HOUR, minute=0, tzinfo=ZoneInfo(TIMEZONE)),
        name="daily_weather_todo",
    )

    log.info("Bot starting (polling mode)")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
