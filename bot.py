#!/usr/bin/env python3
"""
ایجنت تعاملی برنامه‌ریزی شخصی
نسخه سبک با httpx - سازگار با Python 3.13
"""

import os
import json
import sqlite3
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict

import httpx

# ─────────────────────────────────────────────────────────────────────────────
# دیتابیس
# ─────────────────────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).parent / "tasks.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            work_start_hour INTEGER DEFAULT 9,
            work_end_hour INTEGER DEFAULT 17,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            priority INTEGER DEFAULT 2,
            category TEXT DEFAULT 'general',
            duration_minutes INTEGER DEFAULT 60,
            deadline TEXT,
            status TEXT DEFAULT 'pending',
            scheduled_date TEXT,
            scheduled_time TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        );
        
        CREATE TABLE IF NOT EXISTS fixed_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            day_of_week INTEGER NOT NULL,
            start_hour INTEGER NOT NULL,
            start_minute INTEGER DEFAULT 0,
            end_hour INTEGER NOT NULL,
            end_minute INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1
        );
        
        CREATE TABLE IF NOT EXISTS daily_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            task_id INTEGER,
            date TEXT NOT NULL,
            action TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# ثابت‌ها
# ─────────────────────────────────────────────────────────────────────────────

PRIORITY_EMOJI = {1: "🟢", 2: "🟡", 3: "🟠", 4: "🔴"}
PRIORITY_NAMES = {1: "کم‌اهمیت", 2: "معمولی", 3: "مهم", 4: "فوری"}
CATEGORY_EMOJI = {
    "work": "💼", "coding": "💻", "gym": "🏋️", "education": "📚",
    "personal": "👤", "health": "🏥", "admin": "📋", "general": "📝",
}
DAYS_PERSIAN = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]

# وضعیت کاربران
user_states = {}
user_data = {}
offset = 0


# ─────────────────────────────────────────────────────────────────────────────
# API تلگرام
# ─────────────────────────────────────────────────────────────────────────────


class TelegramAPI:
    def __init__(self, token: str):
        self.base_url = f"https://api.telegram.org/bot{token}"
        proxy = os.getenv("PROXY_URL")
        if proxy:
            self.client = httpx.AsyncClient(timeout=30, proxy=proxy)
            print(f"🔗 استفاده از پروکسی: {proxy}")
        else:
            self.client = httpx.AsyncClient(timeout=30)

    async def get_updates(self, offset: int = 0):
        url = f"{self.base_url}/getUpdates"
        params = {"offset": offset, "timeout": 30}
        resp = await self.client.get(url, params=params)
        return resp.json()

    async def send_message(self, chat_id: int, text: str, parse_mode: str = None, reply_markup: dict = None):
        url = f"{self.base_url}/sendMessage"
        data = {"chat_id": chat_id, "text": text}
        if parse_mode:
            data["parse_mode"] = parse_mode
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        resp = await self.client.post(url, data=data)
        return resp.json()

    async def edit_message(self, chat_id: int, message_id: int, text: str, parse_mode: str = None, reply_markup: dict = None):
        url = f"{self.base_url}/editMessageText"
        data = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if parse_mode:
            data["parse_mode"] = parse_mode
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        resp = await self.client.post(url, data=data)
        return resp.json()

    async def answer_callback(self, callback_id: str):
        url = f"{self.base_url}/answerCallbackQuery"
        await self.client.post(url, data={"callback_query_id": callback_id})

    async def send_keyboard(self, chat_id: int, text: str, keyboard: list):
        url = f"{self.base_url}/sendMessage"
        markup = {"keyboard": keyboard, "resize_keyboard": True}
        data = {"chat_id": chat_id, "text": text, "reply_markup": json.dumps(markup)}
        await self.client.post(url, data=data)


# ─────────────────────────────────────────────────────────────────────────────
# توابع دیتابیس
# ─────────────────────────────────────────────────────────────────────────────


def db_user(user_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def db_create_user(user_id, username, first_name):
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?,?,?)",
                 (user_id, username, first_name))
    conn.commit()
    conn.close()


def db_add_task(user_id, title, priority=2, duration=60, deadline=None, category="general"):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO tasks (user_id, title, priority, duration_minutes, deadline, category) VALUES (?,?,?,?,?,?)",
        (user_id, title, priority, duration, deadline, category))
    conn.commit()
    tid = cur.lastrowid
    conn.close()
    return tid


def db_pending(user_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE user_id=? AND status='pending' ORDER BY priority DESC, deadline ASC",
        (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def db_scheduled(user_id, date):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE user_id=? AND scheduled_date=? AND status='pending' ORDER BY scheduled_time",
        (user_id, date)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def db_done(task_id, user_id):
    conn = get_db()
    conn.execute("UPDATE tasks SET status='done', completed_at=CURRENT_TIMESTAMP WHERE id=?", (task_id,))
    conn.execute("INSERT INTO daily_log (user_id, task_id, date, action) VALUES (?,?,?,?)",
                 (user_id, task_id, datetime.now().strftime("%Y-%m-%d"), "completed"))
    conn.commit()
    conn.close()


def db_delete(task_id, user_id):
    conn = get_db()
    conn.execute("DELETE FROM tasks WHERE id=? AND user_id=?", (task_id, user_id))
    conn.commit()
    conn.close()


def db_add_fixed(user_id, title, day, sh, sm, eh, em):
    conn = get_db()
    conn.execute("INSERT INTO fixed_events (user_id, title, day_of_week, start_hour, start_minute, end_hour, end_minute) VALUES (?,?,?,?,?,?,?)",
                 (user_id, title, day, sh, sm, eh, em))
    conn.commit()
    conn.close()


def db_fixed(user_id, day):
    conn = get_db()
    rows = conn.execute("SELECT * FROM fixed_events WHERE user_id=? AND day_of_week=? AND is_active=1",
                        (user_id, day)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def db_all_fixed(user_id):
    conn = get_db()
    rows = conn.execute("SELECT * FROM fixed_events WHERE user_id=? AND is_active=1 ORDER BY day_of_week",
                        (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def db_schedule(task_id, date, time):
    conn = get_db()
    conn.execute("UPDATE tasks SET scheduled_date=?, scheduled_time=? WHERE id=?", (date, time, task_id))
    conn.commit()
    conn.close()


def db_task(task_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ─────────────────────────────────────────────────────────────────────────────
# الگوریتم زمان‌بندی
# ─────────────────────────────────────────────────────────────────────────────


def calc_score(task):
    now = datetime.now()
    score = task["priority"] * 10

    if task.get("deadline"):
        try:
            days = (datetime.strptime(task["deadline"], "%Y-%m-%d") - now).days
            if days < 0: score += 40
            elif days == 0: score += 35
            elif days <= 3: score += 25
            elif days <= 7: score += 15
        except: pass

    if task.get("created_at"):
        try:
            age = (now - datetime.strptime(task["created_at"], "%Y-%m-%d %H:%M:%S")).days
            score += min(age * 2, 20)
        except: pass

    score += {"work": 10, "coding": 9, "education": 8, "gym": 7, "admin": 6}.get(task.get("category", "general"), 3)
    return score


def gen_schedule(user_id, date_str=None):
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")

    user = db_user(user_id)
    if not user:
        return []

    pending = db_pending(user_id)
    if not pending:
        return []

    for t in pending:
        t["score"] = calc_score(t)
    pending.sort(key=lambda x: x["score"], reverse=True)

    work_start = user["work_start_hour"]
    work_end = user["work_end_hour"]
    dow = datetime.strptime(date_str, "%Y-%m-%d").weekday()

    busy = []
    for e in db_fixed(user_id, dow):
        busy.append((e["start_hour"], e["start_minute"], e["end_hour"], e["end_minute"]))

    for t in db_scheduled(user_id, date_str):
        if t.get("scheduled_time"):
            h, m = map(int, t["scheduled_time"].split(":"))
            em = m + t["duration_minutes"]
            eh = h + em // 60
            em %= 60
            busy.append((h, m, eh, em))

    busy.sort()
    schedule = []
    ch, cm = work_start, 0

    for task in pending:
        dur = task["duration_minutes"]
        eh = ch + (cm + dur) // 60
        em = (cm + dur) % 60

        if eh > work_end:
            break

        conflict = False
        for bsh, bsm, beh, bem in busy:
            if (ch, cm) < (beh, bem) and (eh, em) > (bsh, bsm):
                conflict = True
                ch, cm = beh, bem
                break

        if not conflict:
            ts = f"{ch:02d}:{cm:02d}"
            schedule.append({"task": task, "start_time": ts, "end_time": f"{eh:02d}:{em:02d}"})
            db_schedule(task["id"], date_str, ts)
            ch, cm = eh, em + 15
            if cm >= 60:
                ch += 1
                cm -= 60

    return schedule


# ─────────────────────────────────────────────────────────────────────────────
# پردازش پیام‌ها
# ─────────────────────────────────────────────────────────────────────────────


async def process_message(api: TelegramAPI, msg: dict):
    if "text" not in msg:
        return

    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    text = msg["text"].strip()
    first_name = msg["from"].get("first_name", "")
    username = msg["from"].get("username", "")

    db_create_user(user_id, username, first_name)
    state = user_states.get(user_id, "idle")

    # ── لغو ویزار ──
    if text in ["/cancel", "لغو", "❌"]:
        user_states[user_id] = "idle"
        user_data.pop(user_id, None)
        await api.send_message(chat_id, "✅ لغو شد")
        return

    # ── دستورات ──

    if text == "/start":
        kb = [[{"text": "📝 کار جدید"}, {"text": "📋 لیست کارها"}],
              [{"text": "📅 برنامه امروز"}, {"text": "⚡ زمان‌بندی"}],
              [{"text": "📈 آمار"}, {"text": "⚙️ تنظیمات"}]]
        await api.send_keyboard(chat_id,
            f"سلام {first_name}! 👋\n\nمن ایجنت برنامه‌ریز شما هستم.\n\n"
            f"📋 /add - کار جدید\n/list - لیست\n/today - امروز\n/schedule - زمان‌بندی\n/help - راهنما", kb)

    elif text == "/help":
        await api.send_message(chat_id,
            "📋 *دستورات:*\n\n/add - کار جدید\n/list - لیست کارها\n"
            "/today - برنامه امروز\n/schedule - زمان‌بندی\n"
            "/done - انجام کار\n/delete - حذف\n"
            "/fixed - رویداد ثابت\n/fixedlist - لیست رویدادها\n"
            "/settings - تنظیمات\n/sethours - ساعت کاری\n"
            "/cancel - لغو عملیات", "Markdown")

    elif text == "/list":
        tasks = db_pending(user_id)
        if not tasks:
            await api.send_message(chat_id, "📭 کاری ندارید! /add")
            return
        t = "📋 *کارهای شما:*\n\n"
        for i, task in enumerate(tasks, 1):
            pe = PRIORITY_EMOJI.get(task["priority"], "⚪")
            ce = CATEGORY_EMOJI.get(task.get("category", "general"), "📝")
            dl = f" | 📅 {task['deadline']}" if task.get("deadline") else ""
            t += f"*{i}.* {pe} {task['title']}\n   {ce} {task.get('category','general')} | ⏱ {task['duration_minutes']}د{dl}\n\n"
        await api.send_message(chat_id, t, "Markdown")

    elif text == "/today":
        today = datetime.now().strftime("%Y-%m-%d")
        dow = datetime.now().weekday()
        t = f"📅 *برنامه امروز ({DAYS_PERSIAN[dow]}):*\n\n"

        fixed = db_fixed(user_id, dow)
        if fixed:
            t += "📌 *رویداد ثابت:*\n"
            for e in fixed:
                t += f"• {e['title']} ⏰ {e['start_hour']:02d}:{e['start_minute']:02d}-{e['end_hour']:02d}:{e['end_minute']:02d}\n"
            t += "\n"

        scheduled = db_scheduled(user_id, today)
        if scheduled:
            t += "📋 *کارها:*\n"
            for i, task in enumerate(scheduled, 1):
                pe = PRIORITY_EMOJI.get(task["priority"], "⚪")
                t += f"{i}. {pe} {task['title']} ⏰ {task.get('scheduled_time','??:??')} ⏱ {task['duration_minutes']}د\n"
        else:
            t += "💡 /schedule برای زمان‌بندی"

        await api.send_message(chat_id, t, "Markdown")

    elif text == "/schedule":
        today = datetime.now().strftime("%Y-%m-%d")
        schedule = gen_schedule(user_id, today)
        if not schedule:
            await api.send_message(chat_id, "❌ کاری برای زمان‌بندی نیست. /add")
            return
        t = "✅ *زمان‌بندی امروز:*\n\n"
        for i, item in enumerate(schedule, 1):
            task = item["task"]
            pe = PRIORITY_EMOJI.get(task["priority"], "⚪")
            t += f"*{i}.* ⏰ {item['start_time']}-{item['end_time']} {pe} {task['title']} ⏱ {task['duration_minutes']}د\n\n"
        t += "💡 /done [شماره] برای انجام"
        await api.send_message(chat_id, t, "Markdown")

    elif text.startswith("/done"):
        parts = text.split()
        today = datetime.now().strftime("%Y-%m-%d")

        if len(parts) > 1:
            try:
                num = int(parts[1])
            except ValueError:
                await api.send_message(chat_id, "❌ /done 1")
                return
        else:
            scheduled = db_scheduled(user_id, today)
            if not scheduled:
                await api.send_message(chat_id, "📭 کاری امروز نیست!")
                return
            buttons = [[{"text": f"{PRIORITY_EMOJI.get(t['priority'],'⚪')} {t['title']}", "callback_data": f"done_{t['id']}"}] for t in scheduled]
            await api.send_message(chat_id, "✅ کدام انجام شد؟", reply_markup={"inline_keyboard": buttons})
            return

        scheduled = db_scheduled(user_id, today)
        if num < 1 or num > len(scheduled):
            await api.send_message(chat_id, f"❌ شماره ۱ تا {len(scheduled)}")
            return
        task = scheduled[num - 1]
        db_done(task["id"], user_id)
        await api.send_message(chat_id, f"✅ انجام شد: {task['title']} 🎉")

    elif text.startswith("/delete"):
        parts = text.split()
        if len(parts) > 1:
            try:
                num = int(parts[1])
            except ValueError:
                await api.send_message(chat_id, "❌ /delete 1")
                return
            tasks = db_pending(user_id)
            if num < 1 or num > len(tasks):
                await api.send_message(chat_id, f"❌ شماره ۱ تا {len(tasks)}")
                return
            task = tasks[num - 1]
            db_delete(task["id"], user_id)
            await api.send_message(chat_id, f"🗑 حذف شد: {task['title']}")
        else:
            tasks = db_pending(user_id)[:10]
            if not tasks:
                await api.send_message(chat_id, "📭 کاری نیست!")
                return
            buttons = [[{"text": f"{PRIORITY_EMOJI.get(t['priority'],'⚪')} {t['title']}", "callback_data": f"del_{t['id']}"}] for t in tasks]
            await api.send_message(chat_id, "🗑 کدام حذف شود؟", reply_markup={"inline_keyboard": buttons})

    elif text == "/fixed":
        user_states[user_id] = "fixed_title"
        user_data[user_id] = {"selected_days": []}
        await api.send_message(chat_id, "📌 *عنوان رویداد (مثلاً باشگاه):*", "Markdown")

    elif text == "/fixedlist":
        events = db_all_fixed(user_id)
        if not events:
            await api.send_message(chat_id, "📭 رویداد ثابتی نیست! /fixed")
            return
        t = "📌 *رویدادهای ثابت:*\n\n"
        by_day = {}
        for e in events:
            by_day.setdefault(e["day_of_week"], []).append(e)
        for d in sorted(by_day):
            t += f"*{DAYS_PERSIAN[d]}:*\n"
            for e in by_day[d]:
                t += f"• {e['title']} ⏰ {e['start_hour']:02d}:{e['start_minute']:02d}-{e['end_hour']:02d}:{e['end_minute']:02d}\n"
            t += "\n"
        await api.send_message(chat_id, t, "Markdown")

    elif text == "/settings":
        user = db_user(user_id)
        if not user:
            await api.send_message(chat_id, "ابتدا /start")
            return
        await api.send_message(chat_id,
            f"⚙️ *تنظیمات:*\n\n🕐 شروع: {user['work_start_hour']}:00\n"
            f"🕐 پایان: {user['work_end_hour']}:00\n\n/sethours [شروع] [پایان]", "Markdown")

    elif text.startswith("/sethours"):
        parts = text.split()
        if len(parts) != 3:
            await api.send_message(chat_id, "❌ /sethours 8 18")
            return
        try:
            s, e = int(parts[1]), int(parts[2])
            if s >= e or s < 0 or e > 23:
                raise ValueError
        except ValueError:
            await api.send_message(chat_id, "❌ عدد ۰-۲۳ و شروع < پایان")
            return
        conn = get_db()
        conn.execute("UPDATE users SET work_start_hour=?, work_end_hour=? WHERE user_id=?", (s, e, user_id))
        conn.commit()
        conn.close()
        await api.send_message(chat_id, f"✅ ساعت کاری: {s}:00 - {e}:00")

    elif text == "/stats":
        conn = get_db()
        since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT COUNT(*) as total, SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) as done "
            "FROM tasks WHERE user_id=? AND created_at>=?", (user_id, since)).fetchone()
        conn.close()
        total = row["total"] or 0
        done = row["done"] or 0
        score = (done / total * 100) if total > 0 else 0
        await api.send_message(chat_id,
            f"📈 *آمار هفتگی:*\n\n📋 کل: {total}\n✅ انجام: {done}\n🎯 عملکرد: {score:.0f}%", "Markdown")

    # ── ویزارها ──

    elif state == "add_title":
        user_data[user_id]["title"] = text
        user_states[user_id] = "add_priority"
        buttons = [
            [{"text": "🔴 فوری", "callback_data": "p_4"}, {"text": "🟠 مهم", "callback_data": "p_3"}],
            [{"text": "🟡 معمولی", "callback_data": "p_2"}, {"text": "🟢 کم‌اهمیت", "callback_data": "p_1"}],
        ]
        await api.send_message(chat_id, f"✅ عنوان: *{text}*\n\nاولویت:", "Markdown",
                               {"inline_keyboard": buttons})

    elif state == "add_duration":
        try:
            dur = int(text)
            if dur < 5 or dur > 480:
                raise ValueError
        except ValueError:
            await api.send_message(chat_id, "❌ عدد ۵-۴۸۰:")
            return
        user_data[user_id]["duration"] = dur
        user_states[user_id] = "add_deadline"
        await api.send_message(chat_id, f"✅ {dur} دقیقه\n\n📅 مهلت (YYYY-MM-DD) یا 'ندارد':")

    elif state == "add_deadline":
        if text.lower() in ["ندارد", "none", "-"]:
            user_data[user_id]["deadline"] = None
        else:
            try:
                datetime.strptime(text, "%Y-%m-%d")
                user_data[user_id]["deadline"] = text
            except ValueError:
                await api.send_message(chat_id, "❌ YYYY-MM-DD یا 'ندارد'")
                return
        user_states[user_id] = "add_category"
        buttons = [
            [{"text": "💼 کاری", "callback_data": "c_work"}, {"text": "💻 کدنویسی", "callback_data": "c_coding"}],
            [{"text": "📚 آموزش", "callback_data": "c_education"}, {"text": "🏋️ باشگاه", "callback_data": "c_gym"}],
            [{"text": "👤 شخصی", "callback_data": "c_personal"}, {"text": "📋 اداری", "callback_data": "c_admin"}],
        ]
        await api.send_message(chat_id, "دسته:", reply_markup={"inline_keyboard": buttons})

    elif state == "fixed_title":
        user_data[user_id]["title"] = text
        user_states[user_id] = "fixed_days"
        buttons = [[{"text": d, "callback_data": f"fd_{i}"}] for i, d in enumerate(DAYS_PERSIAN)]
        buttons.append([{"text": "ذخیره ✅", "callback_data": "fd_done"}])
        await api.send_message(chat_id, f"✅ {text}\n\nرووزها:", reply_markup={"inline_keyboard": buttons})

    elif state == "fixed_start":
        try:
            h, m = map(int, text.split(":"))
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
        except ValueError:
            await api.send_message(chat_id, "❌ HH:MM مثلاً 14:00")
            return
        user_data[user_id]["start_h"] = h
        user_data[user_id]["start_m"] = m
        user_states[user_id] = "fixed_end"
        await api.send_message(chat_id, f"✅ شروع: {h:02d}:{m:02d}\n\nساعت پایان:")

    elif state == "fixed_end":
        try:
            h, m = map(int, text.split(":"))
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
        except ValueError:
            await api.send_message(chat_id, "❌ HH:MM")
            return
        fixed = user_data[user_id]
        for day in fixed.get("selected_days", []):
            db_add_fixed(user_id, fixed["title"], day, fixed["start_h"], fixed["start_m"], h, m)
        days_txt = ", ".join([DAYS_PERSIAN[d] for d in fixed["selected_days"]])
        user_states[user_id] = "idle"
        user_data.pop(user_id, None)
        await api.send_message(chat_id,
            f"✅ اضافه شد!\n📌 {fixed['title']}\n📅 {days_txt}\n⏰ {fixed['start_h']:02d}:{fixed['start_m']:02d}-{h:02d}:{m:02d}")

    # ── دکمه‌های کیiboard ──

    elif text == "📝 کار جدید":
        user_states[user_id] = "add_title"
        user_data[user_id] = {}
        await api.send_message(chat_id, "📝 *عنوان کار را وارد کنید:*", "Markdown")

    elif text == "📋 لیست کارها":
        tasks = db_pending(user_id)
        if not tasks:
            await api.send_message(chat_id, "📭 کاری ندارید!")
            return
        t = "📋 *کارهای شما:*\n\n"
        for i, task in enumerate(tasks, 1):
            pe = PRIORITY_EMOJI.get(task["priority"], "⚪")
            ce = CATEGORY_EMOJI.get(task.get("category", "general"), "📝")
            dl = f" | 📅 {task['deadline']}" if task.get("deadline") else ""
            t += f"*{i}.* {pe} {task['title']}\n   {ce} {task.get('category','general')} | ⏱ {task['duration_minutes']}د{dl}\n\n"
        await api.send_message(chat_id, t, "Markdown")

    elif text == "📅 برنامه امروز":
        today = datetime.now().strftime("%Y-%m-%d")
        dow = datetime.now().weekday()
        t = f"📅 *برنامه امروز ({DAYS_PERSIAN[dow]}):*\n\n"
        fixed = db_fixed(user_id, dow)
        if fixed:
            t += "📌 *رویداد ثابت:*\n"
            for e in fixed:
                t += f"• {e['title']} ⏰ {e['start_hour']:02d}:{e['start_minute']:02d}-{e['end_hour']:02d}:{e['end_minute']:02d}\n"
            t += "\n"
        scheduled = db_scheduled(user_id, today)
        if scheduled:
            t += "📋 *کارها:*\n"
            for i, task in enumerate(scheduled, 1):
                pe = PRIORITY_EMOJI.get(task["priority"], "⚪")
                t += f"{i}. {pe} {task['title']} ⏰ {task.get('scheduled_time','??:??')} ⏱ {task['duration_minutes']}د\n"
        else:
            t += "💡 /schedule"
        await api.send_message(chat_id, t, "Markdown")

    elif text == "⚡ زمان‌بندی":
        today = datetime.now().strftime("%Y-%m-%d")
        schedule = gen_schedule(user_id, today)
        if not schedule:
            await api.send_message(chat_id, "❌ کاری نیست. /add")
            return
        t = "✅ *زمان‌بندی امروز:*\n\n"
        for i, item in enumerate(schedule, 1):
            task = item["task"]
            pe = PRIORITY_EMOJI.get(task["priority"], "⚪")
            t += f"*{i}.* ⏰ {item['start_time']}-{item['end_time']} {pe} {task['title']} ⏱ {task['duration_minutes']}د\n\n"
        await api.send_message(chat_id, t, "Markdown")

    elif text == "📈 آمار":
        conn = get_db()
        since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT COUNT(*) as total, SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) as done "
            "FROM tasks WHERE user_id=? AND created_at>=?", (user_id, since)).fetchone()
        conn.close()
        total = row["total"] or 0
        done = row["done"] or 0
        score = (done / total * 100) if total > 0 else 0
        await api.send_message(chat_id,
            f"📈 *آمار هفتگی:*\n\n📋 کل: {total}\n✅ انجام: {done}\n🎯 عملکرد: {score:.0f}%", "Markdown")

    elif text == "⚙️ تنظیمات":
        user = db_user(user_id)
        if user:
            await api.send_message(chat_id,
                f"⚙️ *تنظیمات:*\n\n🕐 شروع: {user['work_start_hour']}:00\n"
                f"🕐 پایان: {user['work_end_hour']}:00\n\n/sethours [شروع] [پایان]", "Markdown")


async def process_callback(api: TelegramAPI, cb: dict):
    chat_id = cb["message"]["chat"]["id"]
    user_id = cb["from"]["id"]
    data = cb["data"]

    await api.answer_callback(cb["id"])

    if data.startswith("p_"):
        p = int(data.split("_")[1])
        user_data[user_id]["priority"] = p
        user_states[user_id] = "add_duration"
        await api.edit_message(chat_id, cb["message"]["message_id"],
                               f"✅ اولویت: {PRIORITY_EMOJI[p]}\n\n⏱ مدت (دقیقه):")

    elif data.startswith("c_"):
        cat = data.split("_")[1]
        user_data[user_id]["category"] = cat
        d = user_data[user_id]
        db_add_task(user_id, d["title"], d.get("priority", 2), d.get("duration", 60), d.get("deadline"), cat)
        user_states[user_id] = "idle"
        pe = PRIORITY_EMOJI.get(d.get("priority", 2), "⚪")
        ce = CATEGORY_EMOJI.get(cat, "📝")
        await api.edit_message(chat_id, cb["message"]["message_id"],
            f"✅ *کار اضافه شد!*\n\n📋 {d['title']}\n{pe} اولویت\n⏱ {d.get('duration',60)}د\n📅 {d.get('deadline','ندارد')}\n{ce} {cat}\n\n💡 /schedule", "Markdown")

    elif data.startswith("done_"):
        tid = int(data.split("_")[1])
        db_done(tid, user_id)
        task = db_task(tid)
        await api.edit_message(chat_id, cb["message"]["message_id"], f"✅ {task['title'] if task else ''} انجام شد! 🎉")

    elif data.startswith("del_"):
        tid = int(data.split("_")[1])
        task = db_task(tid)
        db_delete(tid, user_id)
        await api.edit_message(chat_id, cb["message"]["message_id"], f"🗑 حذف شد: {task['title'] if task else ''}")

    elif data.startswith("fd_") and data != "fd_done":
        day = int(data.split("_")[1])
        sel = user_data.get(user_id, {}).get("selected_days", [])
        if day in sel:
            sel.remove(day)
        else:
            sel.append(day)
        user_data.setdefault(user_id, {})["selected_days"] = sel
        buttons = [[{"text": f"{'✅ ' if i in sel else ''}{d}", "callback_data": f"fd_{i}"}]
                   for i, d in enumerate(DAYS_PERSIAN)]
        buttons.append([{"text": "ذخیره ✅", "callback_data": "fd_done"}])
        await api.edit_message(chat_id, cb["message"]["message_id"],
                               f"✅ {user_data[user_id].get('title','')}\n\nرووزها:",
                               reply_markup={"inline_keyboard": buttons})

    elif data == "fd_done":
        sel = user_data.get(user_id, {}).get("selected_days", [])
        if not sel:
            await api.edit_message(chat_id, cb["message"]["message_id"], "❌ حداقل یک روز!")
            return
        user_states[user_id] = "fixed_start"
        await api.edit_message(chat_id, cb["message"]["message_id"], "✅ روزها ثبت شد\n\n⏰ ساعت شروع (HH:MM):")


# ─────────────────────────────────────────────────────────────────────────────
# حلقه اصلی
# ─────────────────────────────────────────────────────────────────────────────


async def main():
    init_db()

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("❌ TELEGRAM_BOT_TOKEN تنظیم نشده!")
        return

    api = TelegramAPI(token)
    print("🚀 ایجنت برنامه‌ریز شخصی فعال شد!")
    print("   Ctrl+C برای توقف")

    global offset

    while True:
        try:
            resp = await api.get_updates(offset)
            if resp.get("ok"):
                for update in resp["result"]:
                    offset = update["update_id"] + 1

                    if "message" in update:
                        await process_message(api, update["message"])
                    elif "callback_query" in update:
                        await process_callback(api, update["callback_query"])
        except KeyboardInterrupt:
            print("\n👋 خداحافظ!")
            break
        except Exception as e:
            print(f"⚠️ خطا: {e}")
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
