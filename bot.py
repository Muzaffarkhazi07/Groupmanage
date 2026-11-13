import os
import asyncio
import logging
import sqlite3
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ChatMemberUpdated, FSInputFile
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "5839586443"))
DB_PATH = os.getenv("DB_PATH", "muza4r_manage.db")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. Add it in Railway Variables.")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("muza4r-manage")

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute("""CREATE TABLE IF NOT EXISTS members (
    chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, username TEXT,
    first_name TEXT, active INTEGER DEFAULT 1,
    PRIMARY KEY(chat_id, user_id)
)""")
db.execute("""CREATE TABLE IF NOT EXISTS settings (
    chat_id INTEGER PRIMARY KEY, welcome_text TEXT, welcome_file_id TEXT,
    welcome_type TEXT, welcome_enabled INTEGER DEFAULT 1,
    rules TEXT DEFAULT 'Please follow the group rules.'
)""")
db.execute("""CREATE TABLE IF NOT EXISTS warnings (
    chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, count INTEGER DEFAULT 0,
    PRIMARY KEY(chat_id, user_id)
)""")
db.execute("""CREATE TABLE IF NOT EXISTS known_chats (
    chat_id INTEGER PRIMARY KEY, title TEXT, chat_type TEXT
)""")
db.commit()

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

def save_member(chat_id, user):
    if user.is_bot:
        return
    db.execute(
        "INSERT OR REPLACE INTO members(chat_id,user_id,username,first_name,active) VALUES(?,?,?,?,1)",
        (chat_id, user.id, user.username, user.first_name)
    )
    db.execute("INSERT OR REPLACE INTO known_chats(chat_id,title,chat_type) VALUES(?,?,?)",
               (chat_id, str(chat_id), "group"))
    db.commit()

def is_owner(user_id):
    return user_id == OWNER_ID

async def is_admin(message: Message):
    if not message.from_user:
        return False
    if is_owner(message.from_user.id):
        return True
    if message.chat.type == ChatType.PRIVATE:
        return False
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False

def main_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="📢 Mention System", callback_data="menu_mention")
    kb.button(text="🛡️ Moderation", callback_data="menu_moderation")
    kb.button(text="👋 Welcome Settings", callback_data="menu_welcome")
    kb.button(text="📜 Group Rules", callback_data="menu_rules")
    kb.button(text="ℹ️ Help & Commands", callback_data="menu_help")
    kb.adjust(2, 2, 1)
    return kb.as_markup()

def admin_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Statistics", callback_data="adm_stats")
    kb.button(text="📢 Broadcast", callback_data="adm_broadcast")
    kb.button(text="👥 Known Groups", callback_data="adm_groups")
    kb.button(text="⚙️ Bot Status", callback_data="adm_status")
    kb.adjust(2, 2)
    return kb.as_markup()

@dp.message(CommandStart())
async def start(message: Message):
    if message.from_user:
        save_member(message.chat.id, message.from_user)
    await message.answer(
        " <b>MUZA4R MANAGE</b>\n\n"
        "Your all-in-one Telegram group management assistant.\n\n"
        "Add me to your group and promote me with the permissions needed for moderation.\n"
        "Use /help to see commands.",
        reply_markup=main_menu()
    )

@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "<b>MUZA4R MANAGE — Commands</b>\n\n"
        "<b>Mentions</b>\n"
        "• @all — mention tracked members (admins only)\n"
        "• @admin — mention group admins (admins only)\n"
        "• /cancel — stop your current mention\n\n"
        "<b>Moderation</b>\n"
        "• /ban, /unban, /kick\n"
        "• /mute, /unmute\n"
        "• /promote, /demote\n"
        "• /warn, /unwarn, /warnings\n"
        "• /pin, /unpin, /purge\n"
        "• /lock, /unlock\n\n"
        "<b>Group</b>\n"
        "• /id, /info, /rules, /setrules\n"
        "• /setwelcome — reply to a photo/video to set it as welcome media\n"
        "• /welcomeon, /welcomeoff\n"
        "• /setwelcometext — set welcome caption/text\n\n"
        "<b>Owner</b>\n"
        "• /admin — private owner panel\n"
        "• /broadcast — reply to a message in private chat to broadcast to users who started the bot\n\n"
        "Commands that change group settings require admin permissions.",
        reply_markup=main_menu()
    )

@dp.callback_query(F.data.startswith("menu_"))
async def menu_callbacks(call: CallbackQuery):
    pages = {
        "menu_mention": "<b>📢 Mention System</b>\n@all and @admin are available to group admins. Only members the bot has observed can be included.",
        "menu_moderation": "<b>🛡️ Moderation</b>\nUse /ban, /mute, /warn, /promote, /pin and related commands by replying to a member's message.",
        "menu_welcome": "<b>👋 Welcome Settings</b>\nReply to a photo or video with /setwelcome. Use /setwelcometext to customize the caption.",
        "menu_rules": "<b>📜 Group Rules</b>\nUse /rules to view rules and /setrules followed by your text (admins only).",
        "menu_help": "Use /help to view all commands."
    }
    await call.message.edit_text(pages.get(call.data, "Menu"), reply_markup=main_menu())
    await call.answer()

@dp.message(F.new_chat_members)
async def new_members(message: Message):
    for user in message.new_chat_members:
        save_member(message.chat.id, user)
        row = db.execute("SELECT welcome_text,welcome_file_id,welcome_type,welcome_enabled FROM settings WHERE chat_id=?",
                         (message.chat.id,)).fetchone()
        if row and row[3] == 0:
            continue
        text = (row[0].replace("{first_name}", user.first_name or "there").replace("{group_name}", message.chat.title or "the group") if row and row[0] else
                f"Welcome <a href='tg://user?id={user.id}'>{user.first_name}</a> to <b>{message.chat.title}</b>!")
        try:
            if row and row[1] and row[2] == "photo":
                await message.answer_photo(row[1], caption=text)
            elif row and row[1] and row[2] == "video":
                await message.answer_video(row[1], caption=text)
            else:
                await message.answer(text)
        except Exception as e:
            log.warning("Welcome send failed: %s", e)


async def target_user(message: Message):
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user
    if message.entities:
        for entity in message.entities:
            if entity.type == "text_mention" and entity.user:
                return entity.user
    return None

async def require_admin(message):
    if not await is_admin(message):
        await message.reply("You need group admin permissions to use this command.")
        return False
    return True

@dp.message(Command("id"))
async def id_cmd(message: Message):
    who = message.reply_to_message.from_user if message.reply_to_message else message.from_user
    await message.reply(f"User ID: <code>{who.id}</code>\nChat ID: <code>{message.chat.id}</code>")

@dp.message(Command("info"))
async def info_cmd(message: Message):
    who = await target_user(message) or message.from_user
    await message.reply(f"<b>User Info</b>\nName: {who.full_name}\nID: <code>{who.id}</code>\nUsername: @{who.username or 'Not set'}")

@dp.message(Command("ban"))
async def ban_cmd(message: Message):
    if not await require_admin(message): return
    who = await target_user(message)
    if not who:
        await message.reply("Reply to the member's message with /ban.")
        return
    try:
        await bot.ban_chat_member(message.chat.id, who.id)
        await message.reply(f"User {who.full_name} has been banned.")
    except Exception as e: await message.reply(f"Could not ban user: {e}")

@dp.message(Command("unban"))
async def unban_cmd(message: Message):
    if not await require_admin(message): return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.reply("Usage: /unban USER_ID")
        return
    try:
        await bot.unban_chat_member(message.chat.id, int(parts[1]), only_if_banned=True)
        await message.reply("Ban removed.")
    except Exception as e: await message.reply(f"Could not unban: {e}")

@dp.message(Command("kick"))
async def kick_cmd(message: Message):
    if not await require_admin(message): return
    who = await target_user(message)
    if not who: await message.reply("Reply to a member with /kick."); return
    try:
        await bot.ban_chat_member(message.chat.id, who.id)
        await bot.unban_chat_member(message.chat.id, who.id)
        await message.reply("Member removed.")
    except Exception as e: await message.reply(f"Could not kick: {e}")

@dp.message(Command("mute"))
async def mute_cmd(message: Message):
    if not await require_admin(message): return
    who = await target_user(message)
    if not who: await message.reply("Reply to a member with /mute."); return
    from aiogram.types import ChatPermissions
    try:
        await bot.restrict_chat_member(message.chat.id, who.id,
            ChatPermissions(can_send_messages=False, can_send_audios=False, can_send_documents=False,
                            can_send_photos=False, can_send_videos=False, can_send_video_notes=False,
                            can_send_voice_notes=False, can_send_polls=False, can_send_other_messages=False,
                            can_add_web_page_previews=False))
        await message.reply("Member muted.")
    except Exception as e: await message.reply(f"Could not mute: {e}")

@dp.message(Command("unmute"))
async def unmute_cmd(message: Message):
    if not await require_admin(message): return
    who = await target_user(message)
    if not who: await message.reply("Reply to a member with /unmute."); return
    from aiogram.types import ChatPermissions
    try:
        await bot.restrict_chat_member(message.chat.id, who.id,
            ChatPermissions(can_send_messages=True, can_send_audios=True, can_send_documents=True,
                            can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
                            can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
                            can_add_web_page_previews=True))
        await message.reply("Member unmuted.")
    except Exception as e: await message.reply(f"Could not unmute: {e}")

@dp.message(Command("promote"))
async def promote_cmd(message: Message):
    if not await require_admin(message): return
    who = await target_user(message)
    if not who: await message.reply("Reply to a member with /promote."); return
    try:
        await bot.promote_chat_member(message.chat.id, who.id, can_manage_chat=True,
            can_delete_messages=True, can_manage_video_chats=True, can_restrict_members=True,
            can_promote_members=False, can_change_info=False, can_invite_users=True, can_pin_messages=True)
        await message.reply("Member promoted with selected admin permissions.")
    except Exception as e: await message.reply(f"Could not promote: {e}")

@dp.message(Command("demote"))
async def demote_cmd(message: Message):
    if not await require_admin(message): return
    who = await target_user(message)
    if not who: await message.reply("Reply to an admin's message with /demote."); return
    try:
        await bot.promote_chat_member(message.chat.id, who.id, can_manage_chat=False,
            can_delete_messages=False, can_manage_video_chats=False, can_restrict_members=False,
            can_promote_members=False, can_change_info=False, can_invite_users=False, can_pin_messages=False)
        await message.reply("Admin permissions removed.")
    except Exception as e: await message.reply(f"Could not demote: {e}")

@dp.message(Command("warn"))
async def warn_cmd(message: Message):
    if not await require_admin(message): return
    who = await target_user(message)
    if not who: await message.reply("Reply to a member with /warn."); return
    db.execute("INSERT INTO warnings(chat_id,user_id,count) VALUES(?,?,1) ON CONFLICT(chat_id,user_id) DO UPDATE SET count=count+1",
               (message.chat.id, who.id)); db.commit()
    count = db.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?", (message.chat.id, who.id)).fetchone()[0]
    await message.reply(f"{who.full_name} has {count} warning(s).")

@dp.message(Command("warnings"))
async def warnings_cmd(message: Message):
    who = await target_user(message) or message.from_user
    row = db.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?", (message.chat.id, who.id)).fetchone()
    await message.reply(f"{who.full_name}: {row[0] if row else 0} warning(s).")

@dp.message(Command("unwarn"))
async def unwarn_cmd(message: Message):
    if not await require_admin(message): return
    who = await target_user(message)
    if not who: await message.reply("Reply to a member with /unwarn."); return
    db.execute("UPDATE warnings SET count=MAX(0,count-1) WHERE chat_id=? AND user_id=?", (message.chat.id, who.id)); db.commit()
    await message.reply("One warning removed.")

@dp.message(Command("pin"))
async def pin_cmd(message: Message):
    if not await require_admin(message): return
    if not message.reply_to_message: await message.reply("Reply to a message with /pin."); return
    try:
        await bot.pin_chat_message(message.chat.id, message.reply_to_message.message_id)
        await message.reply("Message pinned.")
    except Exception as e: await message.reply(f"Could not pin: {e}")

@dp.message(Command("unpin"))
async def unpin_cmd(message: Message):
    if not await require_admin(message): return
    try:
        if message.reply_to_message:
            await bot.unpin_chat_message(message.chat.id, message.reply_to_message.message_id)
        else:
            await bot.unpin_chat_message(message.chat.id)
        await message.reply("Message unpinned.")
    except Exception as e: await message.reply(f"Could not unpin: {e}")

@dp.message(Command("purge"))
async def purge_cmd(message: Message):
    if not await require_admin(message): return
    if not message.reply_to_message:
        await message.reply("Reply to the first message to delete, then use /purge.")
        return
    start_id = message.reply_to_message.message_id
    end_id = message.message_id
    deleted = 0
    for mid in range(start_id, end_id + 1):
        try:
            await bot.delete_message(message.chat.id, mid); deleted += 1
        except Exception:
            pass
    try: await message.answer(f"Deleted {deleted} message(s).")
    except Exception: pass

@dp.message(Command("lock"))
async def lock_cmd(message: Message):
    if not await require_admin(message): return
    from aiogram.types import ChatPermissions
    try:
        await bot.set_chat_permissions(message.chat.id, ChatPermissions(can_send_messages=False))
        await message.reply("Group locked for regular members.")
    except Exception as e: await message.reply(f"Could not lock: {e}")

@dp.message(Command("unlock"))
async def unlock_cmd(message: Message):
    if not await require_admin(message): return
    from aiogram.types import ChatPermissions
    try:
        await bot.set_chat_permissions(message.chat.id, ChatPermissions(can_send_messages=True,
            can_send_audios=True, can_send_documents=True, can_send_photos=True, can_send_videos=True,
            can_send_video_notes=True, can_send_voice_notes=True, can_send_polls=True,
            can_send_other_messages=True, can_add_web_page_previews=True))
        await message.reply("Group unlocked.")
    except Exception as e: await message.reply(f"Could not unlock: {e}")

@dp.message(Command("rules"))
async def rules_cmd(message: Message):
    row = db.execute("SELECT rules FROM settings WHERE chat_id=?", (message.chat.id,)).fetchone()
    await message.reply(row[0] if row else "No group rules have been set yet.")

@dp.message(Command("setrules"))
async def setrules_cmd(message: Message):
    if not await require_admin(message): return
    rules = (message.text or "").partition(" ")[2].strip()
    if not rules: await message.reply("Usage: /setrules Your group rules"); return
    db.execute("INSERT INTO settings(chat_id,rules) VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET rules=excluded.rules",
               (message.chat.id, rules)); db.commit()
    await message.reply("Group rules updated.")

@dp.message(Command("setwelcome"))
async def setwelcome_cmd(message: Message):
    if not await require_admin(message): return
    replied = message.reply_to_message
    if not replied or not (replied.photo or replied.video):
        await message.reply("Reply to a photo or video with /setwelcome.")
        return
    file_id = replied.photo[-1].file_id if replied.photo else replied.video.file_id
    kind = "photo" if replied.photo else "video"
    db.execute("""INSERT INTO settings(chat_id,welcome_file_id,welcome_type) VALUES(?,?,?)
        ON CONFLICT(chat_id) DO UPDATE SET welcome_file_id=excluded.welcome_file_id,welcome_type=excluded.welcome_type""",
        (message.chat.id, file_id, kind)); db.commit()
    await message.reply(f"{kind.title()} welcome media saved.")

@dp.message(Command("setwelcometext"))
async def setwelcometext_cmd(message: Message):
    if not await require_admin(message): return
    value = (message.text or "").partition(" ")[2].strip()
    if not value: await message.reply("Usage: /setwelcometext Welcome {first_name} to {group_name}!"); return
    value = value.replace("{first_name}", "{first_name}").replace("{group_name}", "{group_name}")
    db.execute("INSERT INTO settings(chat_id,welcome_text) VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET welcome_text=excluded.welcome_text",
               (message.chat.id, value)); db.commit()
    await message.reply("Welcome text saved. Available placeholders: {first_name}, {group_name}.")

@dp.message(Command("welcomeoff"))
async def welcomeoff_cmd(message: Message):
    if not await require_admin(message): return
    db.execute("INSERT INTO settings(chat_id,welcome_enabled) VALUES(?,0) ON CONFLICT(chat_id) DO UPDATE SET welcome_enabled=0",
               (message.chat.id,)); db.commit()
    await message.reply("Welcome messages disabled.")

@dp.message(Command("welcomeon"))
async def welcomeon_cmd(message: Message):
    if not await require_admin(message): return
    db.execute("INSERT INTO settings(chat_id,welcome_enabled) VALUES(?,1) ON CONFLICT(chat_id) DO UPDATE SET welcome_enabled=1",
               (message.chat.id,)); db.commit()
    await message.reply("Welcome messages enabled.")

@dp.message(Command("cancel"))
async def cancel_cmd(message: Message):
    await message.reply("There is no active mention task to cancel in this basic version.")

@dp.message(Command("admin"))
async def owner_admin(message: Message):
    if not is_owner(message.from_user.id):
        await message.reply("This panel is available to the bot owner only.")
        return
    await message.answer("<b>MUZA4R MANAGE — OWNER PANEL</b>", reply_markup=admin_menu())

@dp.callback_query(F.data.startswith("adm_"))
async def admin_callbacks(call: CallbackQuery):
    if not call.from_user or not is_owner(call.from_user.id):
        await call.answer("Not authorized.", show_alert=True); return
    if call.data == "adm_stats":
        users = db.execute("SELECT COUNT(DISTINCT user_id) FROM members").fetchone()[0]
        groups = db.execute("SELECT COUNT(*) FROM known_chats").fetchone()[0]
        await call.message.answer(f"Users tracked: {users}\nGroups seen: {groups}")
    elif call.data == "adm_groups":
        rows = db.execute("SELECT chat_id,title FROM known_chats ORDER BY chat_id DESC LIMIT 30").fetchall()
        await call.message.answer("Known groups:\n" + ("\n".join(f"{t} — <code>{c}</code>" for c,t in rows) if rows else "None yet."))
    elif call.data == "adm_status":
        await call.message.answer("Bot status: online")
    elif call.data == "adm_broadcast":
        await call.message.answer("Reply to a message with /broadcast in this private chat to send it to users who have started the bot.")
    await call.answer()

@dp.message(Command("broadcast"))
async def broadcast_cmd(message: Message):
    if not is_owner(message.from_user.id) or message.chat.type != ChatType.PRIVATE:
        await message.reply("Owner-only command. Use it in the bot's private chat.")
        return
    if not message.reply_to_message:
        await message.reply("Reply to the message you want to broadcast, then send /broadcast.")
        return
    targets = db.execute("SELECT DISTINCT user_id FROM members WHERE chat_id=user_id").fetchall()
    sent = failed = 0
    status = await message.reply("Broadcast started...")
    for (uid,) in targets:
        try:
            await message.reply_to_message.copy_to(uid)
            sent += 1
            await asyncio.sleep(0.05)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1); failed += 1
        except (TelegramForbiddenError, TelegramBadRequest):
            failed += 1
        except Exception:
            failed += 1
    await status.edit_text(f"Broadcast finished.\nSent: {sent}\nFailed: {failed}")

@dp.my_chat_member()
async def bot_added(event: ChatMemberUpdated):
    chat = event.chat
    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        db.execute("INSERT OR REPLACE INTO known_chats(chat_id,title,chat_type) VALUES(?,?,?)",
                   (chat.id, chat.title or "Group", chat.type)); db.commit()


@dp.message()
async def track_and_mentions(message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if message.from_user:
        save_member(message.chat.id, message.from_user)
    raw = (message.text or message.caption or "").strip().lower()
    if raw not in ("@all", "@admin") and not raw.startswith("@all ") and not raw.startswith("@admin "):
        return
    if not await is_admin(message):
        await message.reply("Only group admins can use mention commands.")
        return
    members = db.execute("SELECT user_id, first_name FROM members WHERE chat_id=? AND active=1",
                         (message.chat.id,)).fetchall()
    if raw.startswith("@admin"):
        admins = await bot.get_chat_administrators(message.chat.id)
        allowed = {a.user.id for a in admins}
        members = [(uid, name) for uid, name in members if uid in allowed]
    if not members:
        await message.reply("No tracked members found yet. Members become tracked as they interact with the group.")
        return
    extra = raw.split(" ", 1)[1] if " " in raw else "Announcement"
    # Small batches to keep messages readable and reduce notification bursts.
    for start in range(0, len(members), 8):
        batch = members[start:start+8]
        mentions = " ".join(f"<a href='tg://user?id={uid}'>{(name or 'Member')}</a>" for uid, name in batch)
        try:
            await message.answer(f"{extra}\n{mentions}", disable_web_page_preview=True)
            await asyncio.sleep(2)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramBadRequest as e:
            log.warning("Mention batch failed: %s", e)
            break

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())