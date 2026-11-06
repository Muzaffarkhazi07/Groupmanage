"""
MUZAFFAR GC MANAGER
Telegram group management bot using PyTelegramBotAPI + SQLite.
Python 3.10+ | Railway deployment

Environment variables:
  BOT_TOKEN       Required Telegram bot token
  OWNER_ID        Your numeric Telegram user ID (optional but recommended)
  LOG_CHANNEL_ID  Optional channel/group ID for moderation logs
  DB_PATH         Optional SQLite path (default: bot_data.sqlite3)
"""

import os
import re
import time
import html
import math
import random
import sqlite3
import logging
import threading
from datetime import datetime, timedelta, timezone

import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing.")

OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0") or 0)
DB_PATH = os.getenv("DB_PATH", "bot_data.sqlite3")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("muzaffar_gc_manager")

DB_LOCK = threading.RLock()
FLOOD_CACHE = {}
CAPTCHA_CACHE = {}
WARN_LIMIT = 3
FLOOD_MAX = 6
FLOOD_WINDOW = 8


# ------------------------------ DATABASE ------------------------------

def db():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with DB_LOCK, db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS groups (
            chat_id INTEGER PRIMARY KEY,
            title TEXT DEFAULT '',
            welcome TEXT DEFAULT 'Welcome {mention} to {chat}!',
            goodbye TEXT DEFAULT 'Goodbye {name}!',
            rules TEXT DEFAULT 'Please follow the group rules.',
            log_channel INTEGER DEFAULT 0,
            captcha INTEGER DEFAULT 0,
            anti_link INTEGER DEFAULT 0,
            anti_forward INTEGER DEFAULT 0,
            anti_spam INTEGER DEFAULT 1,
            locked TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            last_seen TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS members (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            PRIMARY KEY(chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS warnings (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            count INTEGER DEFAULT 0,
            PRIMARY KEY(chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS filters (
            chat_id INTEGER NOT NULL,
            keyword TEXT NOT NULL,
            response TEXT NOT NULL,
            PRIMARY KEY(chat_id, keyword)
        );
        """)
        con.commit()

def ensure_group(chat):
    with DB_LOCK, db() as con:
        con.execute(
            "INSERT OR IGNORE INTO groups(chat_id,title) VALUES(?,?)",
            (chat.id, getattr(chat, "title", "") or "")
        )
        con.execute("UPDATE groups SET title=? WHERE chat_id=?",
                    (getattr(chat, "title", "") or "", chat.id))
        con.commit()

def group_cfg(chat_id):
    with DB_LOCK, db() as con:
        row = con.execute("SELECT * FROM groups WHERE chat_id=?", (chat_id,)).fetchone()
        return dict(row) if row else {}

def set_cfg(chat_id, field, value):
    allowed = {"welcome", "goodbye", "rules", "log_channel", "captcha",
               "anti_link", "anti_forward", "anti_spam", "locked"}
    if field not in allowed:
        return
    with DB_LOCK, db() as con:
        con.execute(f"UPDATE groups SET {field}=? WHERE chat_id=?", (value, chat_id))
        con.commit()

def save_user(user):
    if not user or getattr(user, "is_bot", False):
        return
    with DB_LOCK, db() as con:
        con.execute("""
            INSERT INTO users(user_id,username,first_name,last_seen)
            VALUES(?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username, first_name=excluded.first_name,
            last_seen=CURRENT_TIMESTAMP
        """, (user.id, user.username or "", user.first_name or ""))
        con.commit()

def remember_member(chat_id, user_id):
    with DB_LOCK, db() as con:
        con.execute("INSERT OR IGNORE INTO members(chat_id,user_id) VALUES(?,?)",
                    (chat_id, user_id))
        con.commit()

def forget_member(chat_id, user_id):
    with DB_LOCK, db() as con:
        con.execute("DELETE FROM members WHERE chat_id=? AND user_id=?",
                    (chat_id, user_id))
        con.commit()

def warning_count(chat_id, user_id):
    with DB_LOCK, db() as con:
        row = con.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?",
                          (chat_id, user_id)).fetchone()
        return int(row["count"]) if row else 0

def add_warning(chat_id, user_id):
    with DB_LOCK, db() as con:
        con.execute("""
            INSERT INTO warnings(chat_id,user_id,count) VALUES(?,?,1)
            ON CONFLICT(chat_id,user_id) DO UPDATE SET count=count+1
        """, (chat_id, user_id))
        con.commit()
    return warning_count(chat_id, user_id)

def clear_warnings(chat_id, user_id):
    with DB_LOCK, db() as con:
        con.execute("DELETE FROM warnings WHERE chat_id=? AND user_id=?",
                    (chat_id, user_id))
        con.commit()

def add_filter(chat_id, keyword, response):
    with DB_LOCK, db() as con:
        con.execute("INSERT OR REPLACE INTO filters(chat_id,keyword,response) VALUES(?,?,?)",
                    (chat_id, keyword.lower(), response))
        con.commit()

def remove_filter(chat_id, keyword):
    with DB_LOCK, db() as con:
        con.execute("DELETE FROM filters WHERE chat_id=? AND keyword=?",
                    (chat_id, keyword.lower()))
        con.commit()

def get_filters(chat_id):
    with DB_LOCK, db() as con:
        return con.execute("SELECT keyword,response FROM filters WHERE chat_id=? ORDER BY keyword",
                           (chat_id,)).fetchall()

def stats():
    with DB_LOCK, db() as con:
        return {
            "users": con.execute("SELECT COUNT(*) n FROM users").fetchone()["n"],
            "groups": con.execute("SELECT COUNT(*) n FROM groups").fetchone()["n"],
            "members": con.execute("SELECT COUNT(*) n FROM members").fetchone()["n"],
        }

init_db()


# ------------------------------ HELPERS ------------------------------

def esc(value):
    return html.escape(str(value or ""))

def mention(user):
    return f'<a href="tg://user?id={user.id}">{esc(user.first_name or "User")}</a>'

def is_group(message):
    return message.chat.type in ("group", "supergroup")

def is_owner(user_id):
    return OWNER_ID and user_id == OWNER_ID

def is_admin(chat_id, user_id):
    try:
        member = bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False

def admin_only(message):
    if not is_group(message):
        bot.reply_to(message, "Use this command inside a group.")
        return False
    if not is_admin(message.chat.id, message.from_user.id) and not is_owner(message.from_user.id):
        bot.reply_to(message, "Only group admins can use this command.")
        return False
    return True

def target_from_message(message, args=None):
    if message.reply_to_message:
        return message.reply_to_message.from_user
    if args:
        first = args.split()[0]
        if first.lstrip("-").isdigit():
            # Keep numeric IDs as IDs. This also allows /unban for users
            # who are currently banned and cannot be fetched with get_chat_member.
            return int(first)
        if first.startswith("@"):
            try:
                return bot.get_chat(first).id
            except Exception:
                return None
    return None

def log_action(chat_id, text):
    cfg = group_cfg(chat_id)
    destination = int(cfg.get("log_channel") or LOG_CHANNEL_ID or 0)
    if destination:
        try:
            bot.send_message(destination, f"🧾 <b>Moderation log</b>\n{text}")
        except Exception as e:
            log.warning("Could not send log: %s", e)

def parse_duration(value):
    """Accept 10m, 2h, 1d; return seconds or None."""
    match = re.fullmatch(r"(\d+)([mhd])", value.lower().strip())
    if not match:
        return None
    amount = int(match.group(1))
    unit = {"m": 60, "h": 3600, "d": 86400}[match.group(2)]
    seconds = amount * unit
    return seconds if 0 < seconds <= 366 * 86400 else None

def bot_can_restrict(chat_id):
    try:
        me = bot.get_me()
        member = bot.get_chat_member(chat_id, me.id)
        return member.status == "creator" or (
            member.status == "administrator" and getattr(member, "can_restrict_members", False)
        )
    except Exception:
        return False

def help_text():
    return (
        "🌹 <b>MUZAFFAR GC MANAGER</b>\n\n"
        "<b>Moderation</b>\n"
        "/ban [reply/user_id] · /unban user_id · /kick · /mute [time] · /unmute\n"
        "/warn · /warnings · /resetwarns · /purge · /pin · /unpin\n"
        "/promote · /demote · /adminlist · /id · /info\n\n"
        "<b>Group settings (admins)</b>\n"
        "/setwelcome text · /setgoodbye text · /setrules text · /rules\n"
        "/captcha on|off · /antilink on|off · /antiforward on|off · /antispam on|off\n"
        "/lock links|media|stickers|all · /unlock links|media|stickers|all\n"
        "/filter keyword | response · /stop keyword · /filters\n"
        "/setlog chat_id · /dashboard\n\n"
        "<b>Owner</b>\n"
        "/stats · /broadcast message\n\n"
        "Placeholders: {mention}, {name}, {chat}. Bot needs admin permissions for moderation."
    )


# ------------------------------ START / HELP ------------------------------

@bot.message_handler(commands=["start", "help"])
def start_help(message):
    save_user(message.from_user)
    if is_group(message):
        ensure_group(message.chat)
    bot.reply_to(message, help_text(), disable_web_page_preview=True)

@bot.message_handler(commands=["id"])
def cmd_id(message):
    text = f"👤 User ID: <code>{message.from_user.id}</code>\n💬 Chat ID: <code>{message.chat.id}</code>"
    if message.reply_to_message:
        text += f"\n↳ Replied user ID: <code>{message.reply_to_message.from_user.id}</code>"
    bot.reply_to(message, text)

@bot.message_handler(commands=["info"])
def cmd_info(message):
    user = message.reply_to_message.from_user if message.reply_to_message else message.from_user
    try:
        member = bot.get_chat_member(message.chat.id, user.id) if is_group(message) else None
        status = member.status if member else "private chat"
        bot.reply_to(message,
            f"👤 <b>User info</b>\nName: {esc(user.first_name)}\n"
            f"Username: @{esc(user.username) if user.username else 'none'}\n"
            f"ID: <code>{user.id}</code>\nStatus: <code>{esc(status)}</code>")
    except Exception:
        bot.reply_to(message, f"User ID: <code>{user.id}</code>")

@bot.message_handler(commands=["adminlist", "admins"])
def cmd_adminlist(message):
    if not is_group(message):
        return bot.reply_to(message, "Use inside a group.")
    try:
        admins = bot.get_chat_administrators(message.chat.id)
        lines = ["👮 <b>Group admins</b>"]
        for item in admins:
            u = item.user
            lines.append(f"• {mention(u)} — <code>{esc(item.status)}</code>")
        bot.reply_to(message, "\n".join(lines))
    except Exception as e:
        bot.reply_to(message, f"Could not fetch admins: {esc(e)}")


# ------------------------------ MODERATION ------------------------------

@bot.message_handler(commands=["ban", "kick", "unban", "mute", "unmute"])
def moderation(message):
    if not admin_only(message):
        return
    command = message.text.split()[0].split("@")[0][1:]
    args = message.text.partition(" ")[2].strip()
    target = target_from_message(message, args)
    if not target:
        return bot.reply_to(message, "Reply to the user's message or provide their numeric user ID.")
    target_id = target.id if hasattr(target, "id") else int(target)
    if target_id == message.from_user.id:
        return bot.reply_to(message, "You cannot moderate yourself.")
    try:
        if command in ("ban", "kick"):
            bot.ban_chat_member(message.chat.id, target_id)
            if command == "kick":
                bot.unban_chat_member(message.chat.id, target_id)
            bot.reply_to(message, f"🚫 User <code>{target_id}</code> {command}ed.")
        elif command == "unban":
            bot.unban_chat_member(message.chat.id, target_id)
            bot.reply_to(message, f"✅ User <code>{target_id}</code> unbanned.")
        elif command == "mute":
            duration = parse_duration(args.split()[-1]) if args and parse_duration(args.split()[-1]) else 3600
            until = int(time.time()) + duration
            permissions = types.ChatPermissions(can_send_messages=False)
            bot.restrict_chat_member(message.chat.id, target_id, permissions, until_date=until)
            bot.reply_to(message, f"🔇 User <code>{target_id}</code> muted for {duration // 60} minutes.")
        elif command == "unmute":
            permissions = types.ChatPermissions(
                can_send_messages=True, can_send_audios=True, can_send_documents=True,
                can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
                can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
                can_add_web_page_previews=True
            )
            bot.restrict_chat_member(message.chat.id, target_id, permissions)
            bot.reply_to(message, f"🔊 User <code>{target_id}</code> unmuted.")
        log_action(message.chat.id, f"{esc(command)} by {mention(message.from_user)} for <code>{target_id}</code>")
    except Exception as e:
        bot.reply_to(message, f"Action failed. Check bot admin permissions.\n<code>{esc(e)}</code>")

@bot.message_handler(commands=["promote", "demote"])
def promote_demote(message):
    if not admin_only(message):
        return
    target = target_from_message(message, message.text.partition(" ")[2].strip())
    if not target:
        return bot.reply_to(message, "Reply to a user's message or provide numeric user ID.")
    uid = target.id if hasattr(target, "id") else int(target)
    try:
        if message.text.startswith("/promote"):
            bot.promote_chat_member(message.chat.id, uid,
                can_change_info=True, can_delete_messages=True, can_invite_users=True,
                can_restrict_members=True, can_pin_messages=True, can_manage_video_chats=True)
            bot.reply_to(message, f"✅ Promoted <code>{uid}</code>.")
        else:
            bot.promote_chat_member(message.chat.id, uid,
                can_change_info=False, can_delete_messages=False, can_invite_users=False,
                can_restrict_members=False, can_pin_messages=False, can_manage_video_chats=False,
                can_promote_members=False, can_manage_chat=False, can_post_messages=False,
                can_edit_messages=False)
            bot.reply_to(message, f"✅ Demoted <code>{uid}</code>.")
        log_action(message.chat.id, f"{message.text.split()[0]}: <code>{uid}</code>")
    except Exception as e:
        bot.reply_to(message, f"Could not update admin rights: <code>{esc(e)}</code>")

@bot.message_handler(commands=["warn", "warnings", "resetwarns"])
def warnings_cmd(message):
    if not admin_only(message):
        return
    cmd = message.text.split()[0][1:]
    target = target_from_message(message, message.text.partition(" ")[2].strip())
    if not target:
        return bot.reply_to(message, "Reply to a user's message or provide numeric user ID.")
    uid = target.id if hasattr(target, "id") else int(target)
    if cmd == "warn":
        count = add_warning(message.chat.id, uid)
        bot.reply_to(message, f"⚠️ Warning {count}/{WARN_LIMIT} for <code>{uid}</code>.")
        log_action(message.chat.id, f"Warning {count}/{WARN_LIMIT} for <code>{uid}</code>")
        if count >= WARN_LIMIT:
            try:
                bot.ban_chat_member(message.chat.id, uid, until_date=int(time.time()) + 3600)
                clear_warnings(message.chat.id, uid)
                bot.send_message(message.chat.id, f"⛔ User <code>{uid}</code> temporarily restricted after {WARN_LIMIT} warnings.")
            except Exception as e:
                bot.send_message(message.chat.id, f"Warning limit reached; automatic action failed: <code>{esc(e)}</code>")
    elif cmd == "warnings":
        bot.reply_to(message, f"⚠️ User <code>{uid}</code> has {warning_count(message.chat.id, uid)} warning(s).")
    else:
        clear_warnings(message.chat.id, uid)
        bot.reply_to(message, f"✅ Warnings cleared for <code>{uid}</code>.")

@bot.message_handler(commands=["purge"])
def purge(message):
    if not admin_only(message):
        return
    if not message.reply_to_message:
        return bot.reply_to(message, "Reply to the first message to delete, then use /purge.")
    start_id = message.reply_to_message.message_id
    end_id = message.message_id
    deleted = 0
    for mid in range(start_id, end_id + 1):
        try:
            bot.delete_message(message.chat.id, mid)
            deleted += 1
        except Exception:
            pass
    try:
        bot.send_message(message.chat.id, f"🧹 Deleted {deleted} message(s).")
    except Exception:
        pass

@bot.message_handler(commands=["pin", "unpin"])
def pin_cmd(message):
    if not admin_only(message):
        return
    if not message.reply_to_message:
        return bot.reply_to(message, "Reply to a message first.")
    try:
        if message.text.startswith("/pin"):
            bot.pin_chat_message(message.chat.id, message.reply_to_message.message_id)
            bot.reply_to(message, "📌 Message pinned.")
        else:
            bot.unpin_chat_message(message.chat.id, message.reply_to_message.message_id)
            bot.reply_to(message, "📍 Message unpinned.")
    except Exception as e:
        bot.reply_to(message, f"Failed: <code>{esc(e)}</code>")


# ------------------------------ SETTINGS ------------------------------

def toggle_setting(message, field, value):
    if not admin_only(message):
        return
    value = value.lower()
    if value not in ("on", "off"):
        return bot.reply_to(message, "Use: on or off")
    set_cfg(message.chat.id, field, 1 if value == "on" else 0)
    bot.reply_to(message, f"✅ {field.replace('_',' ').title()} is now <b>{value}</b>.")

@bot.message_handler(commands=["captcha", "antilink", "antiforward", "antispam"])
def toggles(message):
    cmd = message.text.split()[0].split("@")[0][1:]
    field = {"antilink": "anti_link", "antiforward": "anti_forward",
             "antispam": "anti_spam", "captcha": "captcha"}[cmd]
    toggle_setting(message, field, message.text.partition(" ")[2].strip())

@bot.message_handler(commands=["setwelcome", "setgoodbye", "setrules", "rules"])
def text_settings(message):
    if not is_group(message):
        return bot.reply_to(message, "Use inside a group.")
    cmd = message.text.split()[0][1:]
    cfg_field = {"setwelcome": "welcome", "setgoodbye": "goodbye", "setrules": "rules"}
    if cmd == "rules":
        cfg = group_cfg(message.chat.id)
        return bot.reply_to(message, f"📜 <b>Group rules</b>\n{esc(cfg.get('rules'))}")
    if not admin_only(message):
        return
    value = message.text.partition(" ")[2].strip()
    if not value:
        return bot.reply_to(message, f"Usage: /{cmd} Your text. Placeholders: {{mention}}, {{name}}, {{chat}}")
    set_cfg(message.chat.id, cfg_field[cmd], value[:3000])
    bot.reply_to(message, f"✅ {cmd[3:].title()} message updated.")

@bot.message_handler(commands=["setlog"])
def setlog(message):
    if not admin_only(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        return bot.reply_to(message, "Usage: /setlog -1001234567890")
    set_cfg(message.chat.id, "log_channel", int(parts[1]))
    bot.reply_to(message, "✅ Log destination saved. Add the bot to that channel as an admin to send logs.")

@bot.message_handler(commands=["lock", "unlock"])
def lock_cmd(message):
    if not admin_only(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or parts[1].lower() not in ("links", "media", "stickers", "all"):
        return bot.reply_to(message, "Usage: /lock links|media|stickers|all or /unlock ...")
    kind = parts[1].lower()
    current = set(filter(None, group_cfg(message.chat.id).get("locked", "").split(",")))
    if message.text.startswith("/lock"):
        current.add(kind)
    else:
        current.discard(kind)
    set_cfg(message.chat.id, "locked", ",".join(sorted(current)))
    bot.reply_to(message, f"🔒 {kind.title()} is now {'locked' if message.text.startswith('/lock') else 'unlocked'}.")

@bot.message_handler(commands=["filter", "stop", "filters"])
def filter_cmd(message):
    if not admin_only(message):
        return
    cmd = message.text.split()[0][1:]
    rest = message.text.partition(" ")[2].strip()
    if cmd == "filter":
        if "|" not in rest:
            return bot.reply_to(message, "Usage: /filter keyword | response")
        keyword, response = [x.strip() for x in rest.split("|", 1)]
        if not keyword or not response:
            return bot.reply_to(message, "Both keyword and response are required.")
        add_filter(message.chat.id, keyword, response[:2000])
        bot.reply_to(message, f"✅ Filter added for <code>{esc(keyword)}</code>.")
    elif cmd == "stop":
        if not rest:
            return bot.reply_to(message, "Usage: /stop keyword")
        remove_filter(message.chat.id, rest)
        bot.reply_to(message, "✅ Filter removed.")
    else:
        rows = get_filters(message.chat.id)
        if not rows:
            return bot.reply_to(message, "No filters saved.")
        bot.reply_to(message, "📚 <b>Filters</b>\n" + "\n".join(
            f"• <code>{esc(r['keyword'])}</code> → {esc(r['response'][:100])}" for r in rows
        ))


# ------------------------------ DASHBOARD / OWNER ------------------------------

@bot.message_handler(commands=["dashboard"])
def dashboard(message):
    if not admin_only(message):
        return
    cfg = group_cfg(message.chat.id)
    s = stats()
    text = (
        f"🌹 <b>MUZAFFAR GC MANAGER — Dashboard</b>\n"
        f"Group: {esc(message.chat.title)}\n"
        f"Anti-link: {'ON' if cfg.get('anti_link') else 'OFF'}\n"
        f"Anti-forward: {'ON' if cfg.get('anti_forward') else 'OFF'}\n"
        f"Anti-spam: {'ON' if cfg.get('anti_spam') else 'OFF'}\n"
        f"CAPTCHA: {'ON' if cfg.get('captcha') else 'OFF'}\n"
        f"Locked: {esc(cfg.get('locked') or 'none')}\n\n"
        f"Bot database: {s['users']} users · {s['groups']} groups"
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📜 Group rules", callback_data=f"rules:{message.chat.id}"))
    bot.reply_to(message, text, reply_markup=markup)

@bot.message_handler(commands=["stats"])
def owner_stats(message):
    if not is_owner(message.from_user.id):
        return
    s = stats()
    bot.reply_to(message, f"📊 <b>Bot statistics</b>\nUsers: {s['users']}\nGroups: {s['groups']}\nKnown memberships: {s['members']}")

@bot.message_handler(commands=["broadcast"])
def broadcast(message):
    if not is_owner(message.from_user.id):
        return
    body = message.text.partition(" ")[2].strip()
    if not body:
        return bot.reply_to(message, "Usage: /broadcast Your announcement")
    with DB_LOCK, db() as con:
        destinations = [r["chat_id"] for r in con.execute("SELECT chat_id FROM groups").fetchall()]
    sent, failed = 0, 0
    for chat_id in destinations:
        try:
            bot.send_message(chat_id, body[:4000])
            sent += 1
        except Exception:
            failed += 1
    bot.reply_to(message, f"📣 Broadcast finished.\nSent: {sent}\nFailed: {failed}")


# ------------------------------ CALLBACKS / CAPTCHA ------------------------------

@bot.callback_query_handler(func=lambda call: call.data.startswith("captcha:"))
def captcha_callback(call):
    try:
        _, chat_id, user_id, answer = call.data.split(":")
        chat_id, user_id = int(chat_id), int(user_id)
        if call.from_user.id != user_id:
            return bot.answer_callback_query(call.id, "This verification is not for you.", show_alert=True)
        expected = CAPTCHA_CACHE.get((chat_id, user_id))
        if expected is None or answer != str(expected):
            return bot.answer_callback_query(call.id, "Incorrect. Try again.", show_alert=True)
        perms = types.ChatPermissions(can_send_messages=True, can_send_audios=True,
            can_send_documents=True, can_send_photos=True, can_send_videos=True,
            can_send_video_notes=True, can_send_voice_notes=True, can_send_polls=True,
            can_send_other_messages=True, can_add_web_page_previews=True)
        bot.restrict_chat_member(chat_id, user_id, perms)
        CAPTCHA_CACHE.pop((chat_id, user_id), None)
        bot.answer_callback_query(call.id, "Verified!")
        bot.edit_message_text("✅ Verification complete. Welcome!", chat_id, call.message.message_id)
    except Exception as e:
        bot.answer_callback_query(call.id, "Verification could not be completed.")
        log.warning("Captcha callback error: %s", e)

@bot.callback_query_handler(func=lambda call: call.data.startswith("rules:"))
def rules_callback(call):
    chat_id = int(call.data.split(":")[1])
    cfg = group_cfg(chat_id)
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, f"📜 <b>Group rules</b>\n{esc(cfg.get('rules'))}")


# ------------------------------ MEMBERS / WELCOME ------------------------------

@bot.message_handler(content_types=["new_chat_members"])
def new_members(message):
    ensure_group(message.chat)
    cfg = group_cfg(message.chat.id)
    for user in message.new_chat_members:
        save_user(user)
        remember_member(message.chat.id, user.id)
        if user.is_bot:
            continue
        if cfg.get("captcha") and bot_can_restrict(message.chat.id):
            try:
                bot.restrict_chat_member(message.chat.id, user.id,
                    types.ChatPermissions(can_send_messages=False))
                a, b = random.randint(2, 9), random.randint(2, 9)
                answer = a + b
                CAPTCHA_CACHE[(message.chat.id, user.id)] = answer
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton(
                    f"{a} + {b} = ?", callback_data=f"captcha:{message.chat.id}:{user.id}:{answer}"
                ))
                bot.send_message(message.chat.id,
                    f"🤖 {mention(user)}, tap the correct answer to verify you're human.",
                    reply_markup=markup)
            except Exception as e:
                log.warning("Captcha setup failed: %s", e)
        welcome = cfg.get("welcome") or "Welcome {mention} to {chat}!"
        text = welcome.replace("{mention}", mention(user)).replace(
            "{name}", esc(user.first_name)).replace("{chat}", esc(message.chat.title))
        bot.send_message(message.chat.id, text)

@bot.message_handler(content_types=["left_chat_member"])
def left_member(message):
    cfg = group_cfg(message.chat.id)
    user = message.left_chat_member
    forget_member(message.chat.id, user.id)
    goodbye = (cfg.get("goodbye") or "Goodbye {name}!").replace(
        "{name}", esc(user.first_name)).replace("{chat}", esc(message.chat.title))
    bot.send_message(message.chat.id, goodbye)


# ------------------------------ MESSAGE GUARD / FILTERS ------------------------------

URL_RE = re.compile(r"(https?://|www\.|t\.me/|telegram\.me/)", re.I)

@bot.message_handler(func=lambda m: is_group(m), content_types=[
    "text", "photo", "video", "document", "audio", "voice", "sticker",
    "animation", "video_note", "contact", "location"
])
def group_message_guard(message):
    ensure_group(message.chat)
    save_user(message.from_user)
    remember_member(message.chat.id, message.from_user.id)
    if message.from_user.is_bot or is_admin(message.chat.id, message.from_user.id):
        return

    cfg = group_cfg(message.chat.id)
    text_value = message.text or message.caption or ""
    locked = set(filter(None, (cfg.get("locked") or "").split(",")))
    violation = None

    if "all" in locked:
        violation = "Group is currently locked."
    elif "links" in locked and URL_RE.search(text_value):
        violation = "Links are locked."
    elif "media" in locked and message.content_type in ("photo", "video", "document", "audio", "voice", "animation"):
        violation = "Media is locked."
    elif "stickers" in locked and message.content_type == "sticker":
        violation = "Stickers are locked."
    elif cfg.get("anti_link") and URL_RE.search(text_value):
        violation = "Links are not allowed."
    elif cfg.get("anti_forward") and getattr(message, "forward_origin", None):
        violation = "Forwarded messages are not allowed."

    if cfg.get("anti_spam"):
        now = time.time()
        key = (message.chat.id, message.from_user.id)
        recent = [t for t in FLOOD_CACHE.get(key, []) if now - t < FLOOD_WINDOW]
        recent.append(now)
        FLOOD_CACHE[key] = recent
        if len(recent) > FLOOD_MAX:
            violation = "Please slow down (anti-flood)."

    if violation:
        try:
            bot.delete_message(message.chat.id, message.message_id)
            bot.send_message(message.chat.id, f"⚠️ {mention(message.from_user)} {esc(violation)}")
        except Exception:
            pass
        return

    if text_value:
        rows = get_filters(message.chat.id)
        lowered = text_value.lower()
        for row in rows:
            if row["keyword"] in lowered:
                bot.reply_to(message, row["response"])
                break


# ------------------------------ GROUP TRACKING / ERROR HANDLING ------------------------------

@bot.message_handler(func=lambda m: is_group(m), content_types=["text"])
def group_tracker(message):
    ensure_group(message.chat)
    save_user(message.from_user)
    remember_member(message.chat.id, message.from_user.id)

def run():
    log.info("MUZAFFAR GC MANAGER is starting...")
    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
        except KeyboardInterrupt:
            log.info("Bot stopped.")
            break
        except Exception:
            log.exception("Polling error; retrying in 5 seconds.")
            time.sleep(5)

if __name__ == "__main__":
    run()
