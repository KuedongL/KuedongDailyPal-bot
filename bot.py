import logging
import os
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from flask import Flask, request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
DAILY_PUSH_SECRET = os.environ["DAILY_PUSH_SECRET"]
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "bot.db"))
DEFAULT_LOCATION = "Luodong, Taiwan"
TIMEZONE = ZoneInfo("Asia/Taipei")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def today_taipei():
    return datetime.now(TIMEZONE).date()

app = Flask(__name__)


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
        return today_taipei().isoformat()
    if text in ("明天", "tomorrow"):
        return (today_taipei() + timedelta(days=1)).isoformat()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        try:
            year = today_taipei().year
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


def all_chat_ids() -> list[int]:
    conn = db()
    rows = [r[0] for r in conn.execute("SELECT chat_id FROM chats").fetchall()]
    conn.close()
    return rows


def fetch_weather_text(location: str) -> str:
    resp = requests.get(f"https://wttr.in/{location}", params={"format": "j1"}, timeout=10)
    if not resp.ok:
        return f"找不到地點「{location}」,請確認名稱(建議用英文,如 Luodong, Taiwan)。"

    data = resp.json()
    try:
        area_name = data["nearest_area"][0]["areaName"][0]["value"]
        today = data["weather"][0]
        tmax = today["maxtempC"]
        tmin = today["mintempC"]
        pop = max(int(h["chanceofrain"]) for h in today["hourly"])
        desc = data["current_condition"][0]["weatherDesc"][0]["value"]
    except (KeyError, IndexError, ValueError):
        return f"找不到地點「{location}」,請確認名稱(建議用英文,如 Luodong, Taiwan)。"

    return (
        f"📍 {area_name} 今日天氣\n"
        f"{desc}\n"
        f"氣溫:{tmin}°C ~ {tmax}°C\n"
        f"降雨機率:{pop}%"
    )


def send_message(chat_id: int, text: str):
    resp = requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=10,
    )
    if not resp.ok:
        log.warning("sendMessage failed for %s: %s", chat_id, resp.text)


def cmd_start(chat_id: int, args: list[str]) -> str:
    ensure_chat(chat_id)
    return (
        "嗨!我是每日小幫手 🐾\n"
        "每天 7:00 會傳今日天氣和代辦事項給你。\n\n"
        "指令:\n"
        "/list [日期] - 查看代辦事項(預設今天)\n"
        "/add <日期> <內容> - 新增代辦事項\n"
        "/done <編號> - 標記完成\n"
        "/weather [地點] - 查詢天氣\n"
        "/setlocation <地點> - 設定預設地點(目前預設:宜蘭羅東)"
    )


def cmd_list(chat_id: int, args: list[str]) -> str:
    ensure_chat(chat_id)
    if args:
        todo_date = parse_date(" ".join(args))
        if not todo_date:
            return "日期格式看不懂,請用 YYYY-MM-DD、今天或明天。"
    else:
        todo_date = today_taipei().isoformat()

    rows = list_todos(chat_id, todo_date)
    if not rows:
        return f"{todo_date} 沒有代辦事項。"

    lines = [f"📋 {todo_date} 代辦事項:"]
    for todo_id, content, done in rows:
        mark = "✅" if done else "◻️"
        lines.append(f"{mark} [{todo_id}] {content}")
    return "\n".join(lines)


def cmd_add(chat_id: int, args: list[str]) -> str:
    ensure_chat(chat_id)
    if len(args) < 2:
        return "用法:/add <日期> <內容>\n例如:/add 今天 買晚餐"

    todo_date = parse_date(args[0])
    if not todo_date:
        return "日期格式看不懂,請用 YYYY-MM-DD、今天或明天。"

    content = " ".join(args[1:])
    add_todo(chat_id, todo_date, content)
    return f"已新增 {todo_date} 的代辦:{content}"


def cmd_done(chat_id: int, args: list[str]) -> str:
    if not args or not args[0].isdigit():
        return "用法:/done <編號>(編號可從 /list 看到)"

    todo_id = int(args[0])
    if mark_done(chat_id, todo_id):
        return f"已將編號 {todo_id} 標記為完成 ✅"
    return "找不到這個編號,請先用 /list 確認。"


def cmd_weather(chat_id: int, args: list[str]) -> str:
    ensure_chat(chat_id)
    location = " ".join(args) if args else get_location(chat_id)
    return fetch_weather_text(location)


def cmd_setlocation(chat_id: int, args: list[str]) -> str:
    ensure_chat(chat_id)
    if not args:
        return f"用法:/setlocation <地點>\n目前設定:{get_location(chat_id)}"
    location = " ".join(args)
    set_location(chat_id, location)
    return f"已將預設地點設為:{location}"


COMMANDS = {
    "start": cmd_start,
    "list": cmd_list,
    "add": cmd_add,
    "done": cmd_done,
    "weather": cmd_weather,
    "setlocation": cmd_setlocation,
}


def build_daily_message(chat_id: int) -> str:
    location = get_location(chat_id)
    try:
        weather = fetch_weather_text(location)
    except Exception as exc:
        log.warning("weather fetch failed for %s: %s", chat_id, exc)
        weather = "(天氣查詢失敗)"

    today = today_taipei().isoformat()
    rows = list_todos(chat_id, today)
    if rows:
        todo_text = "\n".join(
            f"{'✅' if done else '◻️'} [{tid}] {content}" for tid, content, done in rows
        )
    else:
        todo_text = "今天沒有安排的代辦事項。"

    return f"☀️ 早安!今天的行程\n\n{weather}\n\n📋 代辦事項:\n{todo_text}"


def run_daily_push():
    for chat_id in all_chat_ids():
        try:
            send_message(chat_id, build_daily_message(chat_id))
        except Exception as exc:
            log.warning("daily push failed for %s: %s", chat_id, exc)


@app.route(f"/daily-push/{DAILY_PUSH_SECRET}", methods=["GET", "POST"])
def trigger_daily_push():
    run_daily_push()
    return "ok"


@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    message = update.get("message") or update.get("edited_message")
    if not message or "text" not in message:
        return "ok"

    chat_id = message["chat"]["id"]
    text = message["text"].strip()
    if not text.startswith("/"):
        return "ok"

    parts = text.split()
    command = parts[0][1:].split("@")[0].lower()
    args = parts[1:]

    handler = COMMANDS.get(command)
    if handler:
        try:
            reply = handler(chat_id, args)
        except Exception as exc:
            log.exception("command %s failed", command)
            reply = "處理指令時發生錯誤,請稍後再試。"
        send_message(chat_id, reply)
    return "ok"


@app.route("/")
def index():
    return "Bot is running."


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
