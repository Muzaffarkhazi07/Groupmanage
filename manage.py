
"""
PREMANAGE.PY — Telegram Group Management Bot
Python: 3.10+
Install: pip install aiogram==3.18.0 aiosqlite
Run:    BOT_TOKEN="YOUR_BOT_TOKEN" python premanage.py

Owner ID is configured as requested. Keep your bot token private.
The bot must be an administrator in each group for moderation actions.
"""

import asyncio
import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType, ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ChatPermissions, BotCommand, FSInputFile
)

OWNER_ID = 5839586443
BOT_TOKEN = "9181621589:AAELQUK9MNhv-J_4C0-wIJBlhOjiD_ZngBw"
DB_PATH = Path("premanage.db")
BACKUP_DIR = Path("backups")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("premanage")

dp = Dispatcher()
bot: Optional[Bot] = None
bot_username = ""
broadcast_drafts = {}  # owner_id -> (source_chat_id, source_message_id)
mention_jobs = {}       # chat_id -> asyncio.Task
FLOOD_CACHE = {}


def main_menu(is_owner=False):
    rows = [
        [InlineKeyboardButton(text="➕ Add Me To Your Group", url=f"https://t.me/{bot_username}?startgroup=true")],
        [InlineKeyboardButton(text="📚 Help & Commands", callback_data="help"),
         InlineKeyboardButton(text="⚙️ Features", callback_data="features")],
        [InlineKeyboardButton(text="🛡️ Security", callback_data="security"),
         InlineKeyboardButton(text="📊 Statistics", callback_data="stats")],
    ]
    if is_owner:
        rows.append([InlineKeyboardButton(text="👑 Owner Panel", callback_data="owner_panel")])
    rows.append([InlineKeyboardButton(text="✖️ Close", callback_data="close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔒 Lock", callback_data="quick_lock"),
         InlineKeyboardButton(text="🔓 Unlock", callback_data="quick_unlock")],
        [InlineKeyboardButton(text="🛡️ Security", callback_data="security"),
         InlineKeyboardButton(text="⚠️ Warnings", callback_data="warn_help")],
        [InlineKeyboardButton(text="📌 Pin Help", callback_data="pin_help"),
         InlineKeyboardButton(text="📢 All Mention", callback_data="all_help")],
        [InlineKeyboardButton(text="📚 Commands", callback_data="help")],
    ])


def owner_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Broadcast Help", callback_data="broadcast_help")],
        [InlineKeyboardButton(text="📊 Bot Statistics", callback_data="stats")],
        [InlineKeyboardButton(text="💾 Database Backup", callback_data="backup_now")],
        [InlineKeyboardButton(text="🔧 Maintenance ON", callback_data="maintenance_on"),
         InlineKeyboardButton(text="✅ Maintenance OFF", callback_data="maintenance_off")],
        [InlineKeyboardButton(text="⬅️ Main Menu", callback_data="home")],
    ])


async def db():
    return await aiosqlite.connect(DB_PATH)


async def init_db():
    async with await db() as con:
        await con.executescript("""
        CREATE TABLE IF NOT EXISTS chats (
            chat_id INTEGER PRIMARY KEY, title TEXT, type TEXT, added_at TEXT
        );
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, name TEXT, username TEXT, seen_at TEXT
        );
        CREATE TABLE IF NOT EXISTS members (
            chat_id INTEGER, user_id INTEGER, name TEXT, username TEXT,
            PRIMARY KEY(chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS warnings (
            chat_id INTEGER, user_id INTEGER, count INTEGER DEFAULT 0,
            PRIMARY KEY(chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS bot_admins (user_id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS global_bans (user_id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS global_mutes (user_id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT);
        """)
        await con.commit()


async def cfg(key, default="0"):
    async with await db() as con:
        cur = await con.execute("SELECT value FROM config WHERE key=?", (key,))
        row = await cur.fetchone()
        return row[0] if row else default


async def set_cfg(key, value):
    async with await db() as con:
        await con.execute(
            "INSERT INTO config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value))
        )
        await con.commit()


async def is_owner(user_id):
    return user_id == OWNER_ID


async def is_bot_admin(user_id):
    if user_id == OWNER_ID:
        return True
    async with await db() as con:
        cur = await con.execute("SELECT 1 FROM bot_admins WHERE user_id=?", (user_id,))
        return await cur.fetchone() is not None


async def is_group_admin(message: Message):
    if not message.from_user:
        return False
    if await is_bot_admin(message.from_user.id):
        return True
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
    except Exception:
        return False


async def track(message: Message):
    now = datetime.now(timezone.utc).isoformat()
    async with await db() as con:
        if message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            await con.execute(
                "INSERT INTO chats(chat_id,title,type,added_at) VALUES(?,?,?,?) "
                "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title,type=excluded.type",
                (message.chat.id, message.chat.title or "Group", message.chat.type.value, now)
            )
        if message.from_user and not message.from_user.is_bot:
            u = message.from_user
            name = (u.full_name or "User")[:128]
            username = u.username or ""
            await con.execute(
                "INSERT INTO users(user_id,name,username,seen_at) VALUES(?,?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET name=excluded.name,username=excluded.username,seen_at=excluded.seen_at",
                (u.id, name, username, now)
            )
            if message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
                await con.execute(
                    "INSERT OR REPLACE INTO members(chat_id,user_id,name,username) VALUES(?,?,?,?)",
                    (message.chat.id, u.id, name, username)
                )
        await con.commit()


async def maintenance_enabled():
    return await cfg("maintenance", "0") == "1"


async def ensure_access(message: Message):
    if message.chat.type == ChatType.PRIVATE and await maintenance_enabled():
        if not message.from_user or not await is_bot_admin(message.from_user.id):
            await message.answer("🛠 Bot is currently under maintenance. Please try again later.")
            return False
    return True


async def require_group_admin(message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.answer("This command can only be used in a group.")
        return False
    if not await is_group_admin(message):
        await message.reply("⛔ Only group admins can use this command.")
        return False
    return True


async def require_owner(message: Message):
    if not message.from_user or not await is_owner(message.from_user.id):
        await message.reply("⛔ Owner only command.")
        return False
    return True


def target_from_message(message: Message):
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user
    return None


async def send_with_retry(chat_id, source_chat_id, source_message_id):
    while True:
        try:
            await bot.copy_message(chat_id, source_chat_id, source_message_id)
            return True
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except (TelegramForbiddenError, TelegramBadRequest):
            return False
        except Exception as e:
            log.warning("Broadcast error to %s: %s", chat_id, e)
            return False


@dp.message(CommandStart())
async def start(message: Message):
    await track(message)
    if not await ensure_access(message):
        return
    is_owner_user = bool(message.from_user and await is_owner(message.from_user.id))
    await message.answer(
        "👋 <b>Welcome to PREMANAGE</b>\n\n"
        "Your all-in-one Telegram group management bot.\n"
        "Add me to your group, promote me to admin, then use /settings.\n\n"
        "Use the buttons below to explore the bot.",
        reply_markup=main_menu(is_owner_user),
        parse_mode="HTML"
    )


@dp.message(Command("help"))
async def help_cmd(message: Message):
    await track(message)
    await message.answer(
        "<b>PREMANAGE — Commands</b>\n\n"
        "<b>Group admin:</b>\n"
        "/settings — Interactive settings\n"
        "/lock, /unlock — Lock/unlock messages\n"
        "/slowmode 10 — Set slow mode (seconds)\n"
        "/warn — Reply to a member to warn\n"
        "/warnings — Check warnings\n"
        "/resetwarn — Reset warnings\n"
        "/ban, /unban, /kick — Member moderation\n"
        "/mute, /unmute, /tmute 10m — Mute controls\n"
        "/purge 10 — Delete replied message and recent messages\n"
        "/pin, /unpin, /unpinall — Pin controls\n"
        "/setdesc text — Change group description\n"
        "/all message — Mention known group members\n"
        "/cancelall — Stop running mention task\n"
        "/report — Reply to a message to report it\n\n"
        "<b>Owner:</b>\n"
        "/broadcast — Reply to a message to broadcast to all groups\n"
        "/gban, /ungban, /gmute, /ungmute — Global moderation\n"
        "/addadmin ID, /removeadmin ID\n"
        "/maintenance on|off, /setlog CHAT_ID, /backup, /stats\n"
        "/leave — Make bot leave this group",
        parse_mode="HTML"
    )


@dp.message(Command("settings"))
async def settings_cmd(message: Message):
    await track(message)
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.answer("Add me to a group to manage its settings.")
        return
    if not await require_group_admin(message):
        return
    await message.answer("⚙️ <b>Group Settings</b>\nChoose a section:", reply_markup=settings_menu(), parse_mode="HTML")


@dp.message(Command("lock"))
async def lock_cmd(message: Message):
    await track(message)
    if not await require_group_admin(message): return
    try:
        await bot.set_chat_permissions(message.chat.id, ChatPermissions(can_send_messages=False))
        await message.reply("🔒 Group messaging locked.")
    except Exception as e:
        await message.reply(f"Could not lock group. Make sure I'm an admin with permission to restrict members.\n{e}")


@dp.message(Command("unlock"))
async def unlock_cmd(message: Message):
    await track(message)
    if not await require_group_admin(message): return
    try:
        await bot.set_chat_permissions(message.chat.id, ChatPermissions(
            can_send_messages=True, can_send_audios=True, can_send_documents=True,
            can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
            can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
            can_add_web_page_previews=True
        ))
        await message.reply("🔓 Group messaging unlocked.")
    except Exception as e:
        await message.reply(f"Could not unlock group. Check my admin permissions.\n{e}")


@dp.message(Command("slowmode"))
async def slowmode_cmd(message: Message):
    await track(message)
    if not await require_group_admin(message): return
    args = (message.text or "").split()
    if len(args) < 2 or not args[1].isdigit():
        await message.reply("Usage: /slowmode 10\nUse 0 to disable. Telegram supports slow mode in seconds.")
        return
    seconds = int(args[1])
    if seconds < 0 or seconds > 3600:
        await message.reply("Choose a value from 0 to 3600 seconds.")
        return
    try:
        await bot.set_chat_slow_mode_delay(message.chat.id, seconds)
        await message.reply(f"⚡ Slowmode set to {seconds} seconds.")
    except Exception as e:
        await message.reply(f"Could not set slowmode: {e}")


@dp.message(Command("warn"))
async def warn_cmd(message: Message):
    await track(message)
    if not await require_group_admin(message): return
    target = target_from_message(message)
    if not target:
        await message.reply("Reply to a member's message with /warn [reason].")
        return
    reason = (message.text or "").partition(" ")[2] or "No reason provided"
    async with await db() as con:
        cur = await con.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?", (message.chat.id, target.id))
        row = await cur.fetchone()
        count = (row[0] if row else 0) + 1
        await con.execute(
            "INSERT INTO warnings(chat_id,user_id,count) VALUES(?,?,?) "
            "ON CONFLICT(chat_id,user_id) DO UPDATE SET count=excluded.count",
            (message.chat.id, target.id, count)
        )
        await con.commit()
    await message.reply(f"⚠️ {target.full_name} warned.\nWarnings: {count}/3\nReason: {reason}")
    if count >= 3:
        try:
            await bot.restrict_chat_member(message.chat.id, target.id, ChatPermissions(can_send_messages=False),
                                           until_date=datetime.now(timezone.utc) + timedelta(minutes=30))
            await message.answer(f"🔇 {target.full_name} was muted for 30 minutes after 3 warnings.")
        except Exception:
            await message.answer("3 warnings reached, but automatic mute failed. Check bot admin permissions.")


@dp.message(Command("warnings"))
async def warnings_cmd(message: Message):
    await track(message)
    target = target_from_message(message) or message.from_user
    if not target:
        await message.reply("Reply to a member's message with /warnings.")
        return
    async with await db() as con:
        cur = await con.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?", (message.chat.id, target.id))
        row = await cur.fetchone()
    await message.reply(f"⚠️ {target.full_name}: {row[0] if row else 0} warning(s).")


@dp.message(Command("resetwarn"))
async def resetwarn_cmd(message: Message):
    await track(message)
    if not await require_group_admin(message): return
    target = target_from_message(message)
    if not target:
        await message.reply("Reply to a member's message with /resetwarn.")
        return
    async with await db() as con:
        await con.execute("DELETE FROM warnings WHERE chat_id=? AND user_id=?", (message.chat.id, target.id))
        await con.commit()
    await message.reply(f"✅ Warnings reset for {target.full_name}.")


@dp.message(Command("ban"))
async def ban_cmd(message: Message):
    await track(message)
    if not await require_group_admin(message): return
    target = target_from_message(message)
    if not target:
        await message.reply("Reply to a member's message with /ban.")
        return
    try:
        await bot.ban_chat_member(message.chat.id, target.id)
        await message.reply(f"🚫 {target.full_name} banned.")
    except Exception as e:
        await message.reply(f"Ban failed: {e}")


@dp.message(Command("unban"))
async def unban_cmd(message: Message):
    await track(message)
    if not await require_group_admin(message): return
    args = (message.text or "").split()
    target = target_from_message(message)
    uid = target.id if target else (int(args[1]) if len(args) > 1 and args[1].isdigit() else None)
    if not uid:
        await message.reply("Reply to a member or use /unban USER_ID.")
        return
    try:
        await bot.unban_chat_member(message.chat.id, uid, only_if_banned=True)
        await message.reply("✅ User unbanned.")
    except Exception as e:
        await message.reply(f"Unban failed: {e}")


@dp.message(Command("kick"))
async def kick_cmd(message: Message):
    await track(message)
    if not await require_group_admin(message): return
    target = target_from_message(message)
    if not target:
        await message.reply("Reply to a member's message with /kick.")
        return
    try:
        await bot.ban_chat_member(message.chat.id, target.id)
        await bot.unban_chat_member(message.chat.id, target.id)
        await message.reply(f"👢 {target.full_name} removed from group.")
    except Exception as e:
        await message.reply(f"Kick failed: {e}")


@dp.message(Command("mute"))
async def mute_cmd(message: Message):
    await track(message)
    if not await require_group_admin(message): return
    target = target_from_message(message)
    if not target:
        await message.reply("Reply to a member's message with /mute.")
        return
    try:
        await bot.restrict_chat_member(message.chat.id, target.id, ChatPermissions(can_send_messages=False))
        await message.reply(f"🔇 {target.full_name} muted.")
    except Exception as e:
        await message.reply(f"Mute failed: {e}")


def parse_duration(value):
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    try:
        return int(value[:-1]) * units[value[-1].lower()]
    except Exception:
        return None


@dp.message(Command("tmute"))
async def tmute_cmd(message: Message):
    await track(message)
    if not await require_group_admin(message): return
    target = target_from_message(message)
    args = (message.text or "").split()
    seconds = parse_duration(args[1]) if len(args) > 1 else None
    if not target or not seconds or seconds > 31_536_000:
        await message.reply("Reply to a user: /tmute 10m (s, m, h, d).")
        return
    try:
        await bot.restrict_chat_member(message.chat.id, target.id, ChatPermissions(can_send_messages=False),
                                       until_date=datetime.now(timezone.utc) + timedelta(seconds=seconds))
        await message.reply(f"🔇 {target.full_name} muted temporarily.")
    except Exception as e:
        await message.reply(f"Temporary mute failed: {e}")


@dp.message(Command("unmute"))
async def unmute_cmd(message: Message):
    await track(message)
    if not await require_group_admin(message): return
    target = target_from_message(message)
    if not target:
        await message.reply("Reply to a member's message with /unmute.")
        return
    try:
        await bot.restrict_chat_member(message.chat.id, target.id, ChatPermissions(
            can_send_messages=True, can_send_audios=True, can_send_documents=True, can_send_photos=True,
            can_send_videos=True, can_send_video_notes=True, can_send_voice_notes=True, can_send_polls=True,
            can_send_other_messages=True, can_add_web_page_previews=True
        ))
        await message.reply(f"🔊 {target.full_name} unmuted.")
    except Exception as e:
        await message.reply(f"Unmute failed: {e}")


@dp.message(Command("purge"))
async def purge_cmd(message: Message):
    await track(message)
    if not await require_group_admin(message): return
    args = (message.text or "").split()
    count = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
    count = max(1, min(count, 100))
    if not message.reply_to_message:
        await message.reply("Reply to the first message to delete, then use /purge 10.")
        return
    deleted = 0
    start_id = message.reply_to_message.message_id
      for mid in range(start_id, min(start_id + count, message.message_id)):
        try:
            await bot.delete_message(message.chat.id, mid)
            deleted += 1
        except Exception:
            pass
    try: await bot.delete_message(message.chat.id, message.message_id)
    except Exception: pass
    status = await message.answer(f"🧹 Deleted {deleted} message(s).")
    await asyncio.sleep(4)
    try: await status.delete()
    except Exception: pass


@dp.message(Command("pin"))
async def pin_cmd(message: Message):
    await track(message)
    if not await require_group_admin(message): return
    if not message.reply_to_message:
        await message.reply("Reply to a message with /pin.")
        return
    try:
        await bot.pin_chat_message(message.chat.id, message.reply_to_message.message_id)
        await message.reply("📌 Message pinned.")
    except Exception as e:
        await message.reply(f"Pin failed: {e}")


@dp.message(Command("unpin"))
async def unpin_cmd(message: Message):
    await track(message)
    if not await require_group_admin(message): return
    try:
        if message.reply_to_message:
            await bot.unpin_chat_message(message.chat.id, message.reply_to_message.message_id)
        else:
            await bot.unpin_chat_message(message.chat.id)
        await message.reply("📌 Message unpinned.")
    except Exception as e:
        await message.reply(f"Unpin failed: {e}")


@dp.message(Command("unpinall"))
async def unpinall_cmd(message: Message):
    await track(message)
    if not await require_group_admin(message): return
    try:
        await bot.unpin_all_chat_messages(message.chat.id)
        await message.reply("✅ All pinned messages were unpinned.")
    except Exception as e:
        await message.reply(f"Unpin all failed: {e}")


@dp.message(Command("setdesc"))
async def setdesc_cmd(message: Message):
    await track(message)
    if not await require_group_admin(message): return
    desc = (message.text or "").partition(" ")[2].strip()
    if not desc:
        await message.reply("Usage: /setdesc Your group description")
        return
    try:
        await bot.set_chat_description(message.chat.id, desc[:255])
        await message.reply("✅ Group description updated.")
    except Exception as e:
        await message.reply(f"Could not update description: {e}")


@dp.message(Command("leave"))
async def leave_cmd(message: Message):
    await track(message)
    if not await require_group_admin(message): return
    await message.answer("👋 Leaving this group.")
    await bot.leave_chat(message.chat.id)


@dp.message(Command("all"))
async def all_cmd(message: Message):
    await track(message)
    if not await require_group_admin(message): return
    text = (message.text or "").partition(" ")[2].strip()
    if not text:
        await message.reply("Usage: /all Your announcement")
        return
    if message.chat.id in mention_jobs and not mention_jobs[message.chat.id].done():
        await message.reply("A mention task is already running. Use /cancelall.")
        return
    async with await db() as con:
        cur = await con.execute("SELECT user_id,name,username FROM members WHERE chat_id=?", (message.chat.id,))
        rows = await cur.fetchall()
    if not rows:
        await message.reply("I haven't seen any members yet. I can only mention members whose messages I have observed.")
        return

    async def mention_task():
        try:
            chunks = []
            current = text + "\n\n"
            for uid, name, username in rows:
                label = ("@" + username) if username else name
                tag = f'<a href="tg://user?id={uid}">{html_escape(label)}</a>'
                if len(current) + len(tag) + 3 > 3500:
                    chunks.append(current)
                    current = ""
                current += tag + " · "
            if current.strip():
                chunks.append(current)
            for chunk in chunks:
                await bot.send_message(message.chat.id, chunk, parse_mode="HTML", disable_web_page_preview=True)
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            await bot.send_message(message.chat.id, "⛔ Mention task cancelled.")
        finally:
            mention_jobs.pop(message.chat.id, None)

    import html
    def html_escape(value):
        return html.escape(value)
    mention_jobs[message.chat.id] = asyncio.create_task(mention_task())
    await message.reply(f"📢 Mention started for {len(rows)} known member(s).")


@dp.message(Command("cancelall"))
async def cancelall_cmd(message: Message):
    await track(message)
    if not await require_group_admin(message): return
    task = mention_jobs.get(message.chat.id)
    if task and not task.done():
        task.cancel()
        await message.reply("Stopping mention task…")
    else:
        await message.reply("No mention task is running.")


@dp.message(Command("report"))
async def report_cmd(message: Message):
    await track(message)
    if not message.reply_to_message:
        await message.reply("Reply to a message with /report.")
        return
    log_chat = await cfg("log_chat", "")
    if not log_chat:
        await message.reply("No log channel configured. Ask the bot owner to set it.")
        return
    try:
        await bot.send_message(int(log_chat),
            f"🚨 <b>Group report</b>\nGroup: {message.chat.title}\nReported by: {message.from_user.full_name} ({message.from_user.id})",
            parse_mode="HTML")
        await bot.forward_message(int(log_chat), message.chat.id, message.reply_to_message.message_id)
        await message.reply("✅ Report sent to admins.")
    except Exception as e:
        await message.reply(f"Report failed: {e}")


@dp.message(Command("broadcast", "gbroadcast"))
async def broadcast_cmd(message: Message):
    await track(message)
    if not await require_owner(message): return
    if not message.reply_to_message:
        await message.reply("Reply to the message you want to broadcast, then send /broadcast.")
        return
    broadcast_drafts[OWNER_ID] = (message.chat.id, message.reply_to_message.message_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Send to all groups", callback_data="broadcast_confirm"),
        InlineKeyboardButton(text="❌ Cancel", callback_data="broadcast_cancel")
    ]])
    async with await db() as con:
        cur = await con.execute("SELECT COUNT(*) FROM chats")
        total = (await cur.fetchone())[0]
    await message.reply(f"📢 <b>Confirm group broadcast</b>\nTarget groups: {total}\n\nThe replied message will be copied to all registered groups.", reply_markup=kb, parse_mode="HTML")


@dp.message(Command("gban"))
async def gban_cmd(message: Message):
    await track(message)
    if not await require_owner(message): return
    args = (message.text or "").split()
    target = target_from_message(message)
    uid = target.id if target else (int(args[1]) if len(args)>1 and args[1].isdigit() else None)
    if not uid:
        await message.reply("Reply to a user or use /gban USER_ID.")
        return
    async with await db() as con:
        await con.execute("INSERT OR IGNORE INTO global_bans(user_id) VALUES(?)", (uid,))
        await con.commit()
        cur = await con.execute("SELECT chat_id FROM chats")
        chats = await cur.fetchall()
    successes = 0
    for (cid,) in chats:
        try:
            await bot.ban_chat_member(cid, uid)
            successes += 1
        except Exception:
            pass
    await message.reply(f"🌐 Global ban saved for {uid}. Applied in {successes}/{len(chats)} groups.")


@dp.message(Command("ungban"))
async def ungban_cmd(message: Message):    await track(message)
    if not await require_owner(message): return
    args = (message.text or "").split()
    target = target_from_message(message)
    uid = target.id if target else (int(args[1]) if len(args)>1 and args[1].isdigit() else None)
    if not uid:
        await message.reply("Use /ungban USER_ID or reply to a user.")
        return
    async with await db() as con:
        await con.execute("DELETE FROM global_bans WHERE user_id=?", (uid,))
        await con.commit()
        cur = await con.execute("SELECT chat_id FROM chats")
        chats = await cur.fetchall()
    for (cid,) in chats:
        try: await bot.unban_chat_member(cid, uid, only_if_banned=True)
        except Exception: pass
    await message.reply(f"✅ Global ban removed for {uid}.")


@dp.message(Command("gmute"))
async def gmute_cmd(message: Message):
    await track(message)
    if not await require_owner(message): return
    args = (message.text or "").split()
    target = target_from_message(message)
    uid = target.id if target else (int(args[1]) if len(args)>1 and args[1].isdigit() else None)
    if not uid:
        await message.reply("Use /gmute USER_ID or reply to a user.")
        return
    async with await db() as con:
        await con.execute("INSERT OR IGNORE INTO global_mutes(user_id) VALUES(?)", (uid,))
        await con.commit()
        cur = await con.execute("SELECT chat_id FROM chats")
        chats = await cur.fetchall()
    successes = 0
    for (cid,) in chats:
        try:
            await bot.restrict_chat_member(cid, uid, ChatPermissions(can_send_messages=False))
            successes += 1
        except Exception: pass
    await message.reply(f"🌐 Global mute saved for {uid}. Applied in {successes}/{len(chats)} groups.")


@dp.message(Command("ungmute"))
async def ungmute_cmd(message: Message):
    await track(message)
    if not await require_owner(message): return
    args = (message.text or "").split()
    target = target_from_message(message)
    uid = target.id if target else (int(args[1]) if len(args)>1 and args[1].isdigit() else None)
    if not uid:
        await message.reply("Use /ungmute USER_ID or reply to a user.")
        return
    async with await db() as con:
        await con.execute("DELETE FROM global_mutes WHERE user_id=?", (uid,))
        await con.commit()
        cur = await con.execute("SELECT chat_id FROM chats")
        chats = await cur.fetchall()
    for (cid,) in chats:
        try:
            await bot.restrict_chat_member(cid, uid, ChatPermissions(
                can_send_messages=True, can_send_audios=True, can_send_documents=True,
                can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
                can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
                can_add_web_page_previews=True))
        except Exception: pass
    await message.reply(f"✅ Global mute removed for {uid}.")


@dp.message(Command("addadmin"))
async def addadmin_cmd(message: Message):
    await track(message)
    if not await require_owner(message): return
    args = (message.text or "").split()
    if len(args) < 2 or not args[1].isdigit():
        await message.reply("Usage: /addadmin USER_ID")
        return
    async with await db() as con:
        await con.execute("INSERT OR IGNORE INTO bot_admins(user_id) VALUES(?)", (int(args[1]),))
        await con.commit()
    await message.reply(f"✅ Bot admin added: {args[1]}")


@dp.message(Command("removeadmin"))
async def removeadmin_cmd(message: Message):
    await track(message)
    if not await require_owner(message): return
    args = (message.text or "").split()
    if len(args) < 2 or not args[1].isdigit():
        await message.reply("Usage: /removeadmin USER_ID")
        return
    async with await db() as con:
        await con.execute("DELETE FROM bot_admins WHERE user_id=?", (int(args[1]),))
        await con.commit()
    await message.reply(f"✅ Bot admin removed: {args[1]}")


@dp.message(Command("maintenance"))
async def maintenance_cmd(message: Message):
    await track(message)
    if not await require_owner(message): return
    args = (message.text or "").split()
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        await message.reply("Usage: /maintenance on OR /maintenance off")
        return
    await set_cfg("maintenance", "1" if args[1].lower() == "on" else "0")
    await message.reply(f"🛠 Maintenance {'enabled' if args[1].lower() == 'on' else 'disabled'}.")


@dp.message(Command("setlog"))
async def setlog_cmd(message: Message):
    await track(message)
    if not await require_owner(message): return
    args = (message.text or "").split()
    if len(args) < 2:
        await message.reply("Usage: /setlog CHAT_ID")
        return
    await set_cfg("log_chat", args[1])
    await message.reply("✅ Log destination saved. Make sure the bot can post there.")


@dp.message(Command("backup"))
async def backup_cmd(message: Message):
    await track(message)
    if not await require_owner(message): return
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"premanage_{stamp}.db"
    if DB_PATH.exists():
        shutil.copy2(DB_PATH, dest)
        await message.answer_document(FSInputFile(dest), caption="💾 PREMANAGE database backup")
    else:
        await message.reply("Database file is not ready yet.")


@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    await track(message)
    if not (await is_owner(message.from_user.id) or await is_group_admin(message)):
        await message.reply("⛔ Admin only.")
        return
    async with await db() as con:
        cur = await con.execute("SELECT COUNT(*) FROM chats")
        groups = (await cur.fetchone())[0]
        cur = await con.execute("SELECT COUNT(*) FROM users")
        users = (await cur.fetchone())[0]
    await message.reply(f"📊 <b>PREMANAGE Stats</b>\nGroups: {groups}\nKnown users: {users}", parse_mode="HTML")


@dp.callback_query(F.data == "home")
async def cb_home(call: CallbackQuery):
    await call.message.edit_text("PREMANAGE main menu:", reply_markup=main_menu(call.from_user.id == OWNER_ID))
    await call.answer()


@dp.callback_query(F.data == "help")
async def cb_help(call: CallbackQuery):
    await call.message.edit_text(
        "📚 <b>Help</b>\nUse /help for the full command list.\nGroup commands require group admin rights and bot permissions.",
          reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back", callback_data="home")]]),
        parse_mode="HTML")
    await call.answer()


@dp.callback_query(F.data == "features")
async def cb_features(call: CallbackQuery):
    await call.message.edit_text(
        "⚙️ <b>Features</b>\nModeration • Warnings • Slowmode • Pin controls • Global moderation • Group broadcast • Backup",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back", callback_data="home")]]),
        parse_mode="HTML")
    await call.answer()


@dp.callback_query(F.data == "security")
async def cb_security(call: CallbackQuery):
    await call.message.edit_text(
        "🛡️ <b>Security</b>\nUse /lock to stop member messages, /slowmode N to slow chat, and /warn by replying to a member. Anti-spam and CAPTCHA are not enabled in this starter build.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back", callback_data="home")]]),
        parse_mode="HTML")
    await call.answer()


@dp.callback_query(F.data == "stats")
async def cb_stats(call: CallbackQuery):
    async with await db() as con:
        cur = await con.execute("SELECT COUNT(*) FROM chats")
        groups = (await cur.fetchone())[0]
        cur = await con.execute("SELECT COUNT(*) FROM users")
        users = (await cur.fetchone())[0]
    await call.message.edit_text(f"📊 <b>Statistics</b>\nGroups: {groups}\nKnown users: {users}",
                                 reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back", callback_data="home")]]),
                                 parse_mode="HTML")
    await call.answer()


@dp.callback_query(F.data == "owner_panel")
async def cb_owner(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("Owner only.", show_alert=True)
        return
    await call.message.edit_text("👑 <b>Owner Dashboard</b>", reply_markup=owner_menu(), parse_mode="HTML")
    await call.answer()


@dp.callback_query(F.data == "backup_now")
async def cb_backup(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("Owner only.", show_alert=True)
        return
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"premanage_{stamp}.db"
    shutil.copy2(DB_PATH, dest)
    await call.message.answer_document(FSInputFile(dest), caption="💾 Database backup")
    await call.answer("Backup created.")


@dp.callback_query(F.data.in_({"maintenance_on", "maintenance_off"}))
async def cb_maintenance(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("Owner only.", show_alert=True)
        return
    enabled = call.data == "maintenance_on"
    await set_cfg("maintenance", "1" if enabled else "0")
    await call.answer("Maintenance " + ("ON" if enabled else "OFF"))


@dp.callback_query(F.data == "broadcast_help")
async def cb_broadcast_help(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("Owner only.", show_alert=True)
        return
    await call.message.edit_text(
        "📢 <b>Group Broadcast</b>\nIn a private chat with the bot, send the message you want to broadcast, then reply to it with /broadcast. Confirm to copy it to all registered groups.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back", callback_data="owner_panel")]]),
        parse_mode="HTML")
    await call.answer()


@dp.callback_query(F.data == "broadcast_cancel")
async def cb_broadcast_cancel(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("Owner only.", show_alert=True)
        return
    broadcast_drafts.pop(OWNER_ID, None)
    await call.message.edit_text("❌ Broadcast cancelled.")
    await call.answer()


@dp.callback_query(F.data == "broadcast_confirm")
async def cb_broadcast_confirm(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("Owner only.", show_alert=True)
        return
    draft = broadcast_drafts.pop(OWNER_ID, None)
    if not draft:
        await call.answer("No pending broadcast.", show_alert=True)
        return
    source_chat, source_msg = draft
    async with await db() as con:
        cur = await con.execute("SELECT chat_id FROM chats")
        targets = [r[0] for r in await cur.fetchall()]
    await call.message.edit_text(f"📢 Broadcast started for {len(targets)} registered groups…")
    sent = failed = 0
    for cid in targets:
        if await send_with_retry(cid, source_chat, source_msg):
            sent += 1
        else:
            failed += 1
        await asyncio.sleep(0.08)
    await call.message.answer(f"✅ <b>Broadcast completed</b>\nSuccessful: {sent}\nFailed/skipped: {failed}\nTotal: {len(targets)}", parse_mode="HTML")
    await call.answer("Broadcast finished.")


@dp.callback_query(F.data == "quick_lock")
async def cb_lock(call: CallbackQuery):
    if call.message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await call.answer("Use this in a group.", show_alert=True); return
    try:
        member = await bot.get_chat_member(call.message.chat.id, call.from_user.id)
        if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR) and call.from_user.id != OWNER_ID:
            await call.answer("Group admins only.", show_alert=True); return
        await bot.set_chat_permissions(call.message.chat.id, ChatPermissions(can_send_messages=False))
        await call.answer("Group locked.")
    except Exception as e:
        await call.answer("Could not lock: check bot admin rights.", show_alert=True)


@dp.callback_query(F.data == "quick_unlock")
async def cb_unlock(call: CallbackQuery):
    if call.message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await call.answer("Use this in a group.", show_alert=True); return
    try:
        member = await bot.get_chat_member(call.message.chat.id, call.from_user.id)
        if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR) and call.from_user.id != OWNER_ID:
            await call.answer("Group admins only.", show_alert=True); return
        await bot.set_chat_permissions(call.message.chat.id, ChatPermissions(
            can_send_messages=True, can_send_audios=True, can_send_documents=True, can_send_photos=True,
            can_send_videos=True, can_send_video_notes=True, can_send_voice_notes=True, can_send_polls=True,
            can_send_other_messages=True, can_add_web_page_previews=True))
        await call.answer("Group unlocked.")
    except Exception:
        await call.answer("Could not unlock: check bot admin rights.", show_alert=True)


@dp.callback_query(F.data == "warn_help")
async def cb_warn_help(call: CallbackQuery):
    await call.answer("Reply to a member with /warn, /warnings or /resetwarn.", show_alert=True)


@dp.callback_query(F.data == "pin_help")
async def cb_pin_help(call: CallbackQuery):
    await call.answer("Reply to a message with /pin or /unpin. Use /unpinall to clear all pins.", show_alert=True)


@dp.callback_query(F.data == "all_help")
async def cb_all_help(call: CallbackQuery):
    await call.answer("Use /all Your message. Only members the bot has seen can be mentioned.", show_alert=True)


@dp.callback_query(F.data == "close")
async def cb_close(call: CallbackQuery):
    try: await call.message.delete()
    except Exception: pass
    await call.answer()


@dp.message()
async def observe_messages(message: Message):
    # Track chats and members. This is also used by /all and global-ban checks.
    await track(message)
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP) or not message.from_user:
        return
    async with await db() as con:
        cur = await con.execute("SELECT 1 FROM global_bans WHERE user_id=?", (message.from_user.id,))
        banned = await cur.fetchone()
        cur = await con.execute("SELECT 1 FROM global_mutes WHERE user_id=?", (message.from_user.id,))
        muted = await cur.fetchone()
    if banned:
        try: await bot.ban_chat_member(message.chat.id, message.from_user.id)
        except Exception: pass
        return
    if muted:
        try: await bot.restrict_chat_member(message.chat.id, message.from_user.id, ChatPermissions(can_send_messages=False))
        except Exception: pass
        return
    # Lightweight flood limiter: repeated bursts are removed; admins are exempt.
    if not await is_group_admin(message):
        now = asyncio.get_running_loop().time()
        key = (message.chat.id, message.from_user.id)
        history = [t for t in FLOOD_CACHE.get(key, []) if now - t < 5]
        history.append(now)
        FLOOD_CACHE[key] = history
        if len(history) > 7:
            try: await message.delete()
            except Exception: pass


async def main():
    global bot, bot_username
    if not BOT_TOKEN:
        raise SystemExit("ERROR: Set BOT_TOKEN environment variable before running the bot.")
    await init_db()
    bot = Bot(BOT_TOKEN)
    me = await bot.get_me()
    bot_username = me.username or ""
    commands = [
        BotCommand(command="start", description="Start bot"),
        BotCommand(command="help", description="Show all commands"),
        BotCommand(command="settings", description="Open group settings"),
        BotCommand(command="lock", description="Lock group messages"),
        BotCommand(command="unlock", description="Unlock group messages"),
        BotCommand(command="warn", description="Warn a member"),
        BotCommand(command="pin", description="Pin replied message"),
        BotCommand(command="unpin", description="Unpin message"),
        BotCommand(command="all", description="Mention known group members"),
        BotCommand(command="broadcast", description="Owner: broadcast to all groups"),
    ]
    await bot.set_my_commands(commands)
    log.info("PREMANAGE started as @%s | Owner ID: %s", bot_username, OWNER_ID)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
  
  
   
