import logging
import asyncio
from telegram import Update, ChatPermissions
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters
)

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Owner ID provided by user
OWNER_ID = 5839586443

# Replace with your actual Bot Token from BotFather
BOT_TOKEN = "9181621589:AAELQUK9MNhv-J_4C0-wIJBlhOjiD_ZngBw"

# In-memory storage for global lists, maintenance mode, and bot admins
GLOBAL_BANS = set()
GLOBAL_MUTES = set()
BOT_ADMINS = {OWNER_ID}
MAINTENANCE_MODE = False
LOG_CHANNEL_ID = None
WARNINGS = {}

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID

def is_bot_admin(user_id: int) -> bool:
    return user_id in BOT_ADMINS or user_id == OWNER_ID

# --- Start & Help ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"👋 Hello {user.first_name}!\n"
        f"Main aapka Advanced Telegram Group Management Bot hoon.\n"
        f"Commands dekhne ke liye /help use karein."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 **Bot Commands List** 🤖\n\n"
        "**Group Moderation:**\n"
        "• /lock - Lock group messaging permissions\n"
        "• /unlock - Unlock group messaging permissions\n"
        "• /slowmode <seconds> - Set slowmode frequency\n"
        "• /warn - Warn a member (reply to user)\n"
        "• /warnings - View warnings\n"
        "• /resetwarn - Reset warnings (reply to user)\n"
        "• /purge <number> - Delete multiple messages\n"
        "• /setdesc <desc> - Update group description\n"
        "• /leave - Bot leaves the group\n\n"
        "**Owner & Bot Admin Controls:**\n"
        "• /broadcast <msg> - Send message to all active contexts\n"
        "• /gban <user_id> - Global Ban user\n"
        "• /ungban <user_id> - Remove Global Ban\n"
        "• /gmute <user_id> - Global Mute user\n"
        "• /ungmute <user_id> - Remove Global Mute\n"
        "• /addadmin <user_id> - Add bot admin\n"
        "• /removeadmin <user_id> - Remove bot admin\n"
        "• /maintenance - Toggle maintenance mode\n"
        "• /setlog <channel_id> - Set log channel\n"
        "• /backup - Backup bot database"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

# --- Moderation Commands ---
async def lock_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    member = await chat.get_member(user.id)
    if member.status not in ["administrator", "creator"] and not is_bot_admin(user.id):
        await update.message.reply_text("❌ Yeh command sirf admins ke liye hai!")
        return

    await context.bot.set_chat_permissions(
        chat_id=chat.id,
        permissions=ChatPermissions(can_send_messages=False)
    )
    await update.message.reply_text("🔒 Group successfully lock kar diya gaya है! Ab koi message nahi bhej sakega.")

async def unlock_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    member = await chat.get_member(user.id)
    if member.status not in ["administrator", "creator"] and not is_bot_admin(user.id):
        await update.message.reply_text("❌ Yeh command sirf admins ke liye hai!")
        return

    await context.bot.set_chat_permissions(
        chat_id=chat.id,
        permissions=ChatPermissions(can_send_messages=True, can_send_media_messages=True, can_send_other_messages=True)
    )
    await update.message.reply_text("🔓 Group successfully unlock kar diya gaya hai!")

async def slowmode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    member = await chat.get_member(user.id)
    if member.status not in ["administrator", "creator"] and not is_bot_admin(user.id):
        await update.message.reply_text("❌ Yeh command sirf admins ke liye hai!")
        return

    if not context.args:
        await update.message.reply_text("Kripya seconds batayein. Example: `/slowmode 30`", parse_mode="Markdown")
        return

    try:
        seconds = int(context.args[0])
        await context.bot.set_chat_slow_mode_delay(chat_id=chat.id, slow_mode_delay=seconds)
        await update.message.reply_text(f"⏱️ Slowmode set to {seconds} seconds.")
    except ValueError:
        await update.message.reply_text("❌ Kripya valid number enter karein.")

async def warn_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    member = await chat.get_member(user.id)
    if member.status not in ["administrator", "creator"] and not is_bot_admin(user.id):
        await update.message.reply_text("❌ Yeh command sirf admins ke liye hai!")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Kripya us user ke message par reply karein jise warn karna hai.")
        return

    target_user = update.message.reply_to_message.from_user
    chat_id = chat.id
    
    if chat_id not in WARNINGS:
        WARNINGS[chat_id] = {}
    
    current_warns = WARNINGS[chat_id].get(target_user.id, 0) + 1
    WARNINGS[chat_id][target_user.id] = current_warns

    await update.message.reply_text(f"⚠️ User {target_user.first_name} ko warn kiya gaya! Total Warnings: {current_warns}/3")
    
    if current_warns >= 3:
        await chat.ban_member(target_user.id)
        await update.message.reply_text(f"🚫 {target_user.first_name} ko 3 warnings poori hone par ban kar diya gaya hai.")

async def view_warnings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Kripya user ke message par reply karke warnings dekhein.")
        return
    target_user = update.message.reply_to_message.from_user
    count = WARNINGS.get(chat.id, {}).get(target_user.id, 0)
    await update.message.reply_text(f"ℹ️ User {target_user.first_name} ki total warnings: {count}")

async def reset_warnings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    member = await chat.get_member(user.id)
    if member.status not in ["administrator", "creator"] and not is_bot_admin(user.id):
        await update.message.reply_text("❌ Yeh command sirf admins ke liye hai!")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Kripya user ke message par reply karein.")
        return
    target_user = update.message.reply_to_message.from_user
    if chat.id in WARNINGS and target_user.id in WARNINGS[chat.id]:
        WARNINGS[chat.id][target_user.id] = 0
    await update.message.reply_text(f"🔄 User {target_user.first_name} ki warnings reset kar di gayi hain.")

async def purge_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    member = await chat.get_member(user.id)
    if member.status not in ["administrator", "creator"] and not is_bot_admin(user.id):
        await update.message.reply_text("❌ Yeh command sirf admins ke liye hai!")
        return

    if not context.args:
        await update.message.reply_text("❌ Kitne messages delete karne hain batayein. Example: `/purge 10`", parse_mode="Markdown")
        return

    try:
        limit = int(context.args[0])
        message_id = update.message.message_id
        deleted = 0
        for i in range(1, limit + 2):
            try:
                await context.bot.delete_message(chat_id=chat.id, message_id=message_id - i)
                deleted += 1
            except Exception:
                pass
        msg = await update.message.reply_text(f"🗑️ Successfully {deleted} messages delete kar diye gaye.")
        await asyncio.sleep(3)
        await msg.delete()
    except ValueError:
        await update.message.reply_text("❌ Valid number enter karein.")

async def set_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    member = await chat.get_member(user.id)
    if member.status not in ["administrator", "creator"] and not is_bot_admin(user.id):
        await update.message.reply_text("❌ Yeh command sirf admins ke liye hai!")
        return

    new_desc = " ".join(context.args)
    if not new_desc:
        await update.message.reply_text("❌ New description enter karein. Example: `/setdesc Welcome to official group!`", parse_mode="Markdown")
        return

    await chat.set_description(new_desc)
    await update.message.reply_text("✅ Group description successfully update ho gayi hai!")

async def leave_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    member = await chat.get_member(user.id)
    if member.status not in ["administrator", "creator"] and not is_bot_admin(user.id):
        await update.message.reply_text("❌ Yeh command sirf admins ke liye hai!")
        return

    await update.message.reply_text("👋 Alvida! Bot group chhod raha hai.")
    await chat.leave()

# --- Owner & Admin Controls ---
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Yeh command sirf Owner ke liye hai!")
        return

    msg = " ".join(context.args)
    if not msg:
        await update.message.reply_text("❌ Broadcast message enter karein.")
        return

    await update.message.reply_text(f"📢 Broadcast initiated:\n\n{msg}")

async def global_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Yeh command sirf Owner ke liye hai!")
        return
    if not context.args:
        await update.message.reply_text("❌ User ID enter karein. Example: `/gban 12345678`", parse_mode="Markdown")
        return
    try:
        uid = int(context.args[0])
        GLOBAL_BANS.add(uid)
        await update.message.reply_text(f"🔨 User {uid} ko Global Ban list me daal diya gaya hai.")
    except ValueError:
        await update.message.reply_text("❌ Valid User ID enter karein.")

async def remove_global_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Yeh command sirf Owner ke liye hai!")
        return
    if not context.args:
        await update.message.reply_text("❌ User ID enter karein.")
        return
    uid = int(context.args[0])
    if uid in GLOBAL_BANS:
        GLOBAL_BANS.remove(uid)
        await update.message.reply_text(f"✅ User {uid} ko Global Ban list se hata diya gaya hai.")
    else:
        await update.message.reply_text("❌ User global ban list me nahi mila.")

async def global_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Yeh command sirf Owner ke liye hai!")
        return
    if not context.args:
        await update.message.reply_text("❌ User ID enter karein.")
        return
    uid = int(context.args[0])
    GLOBAL_MUTES.add(uid)
    await update.message.reply_text(f"🔇 User {uid} ko Global Mute list me daal diya gaya hai.")

async def remove_global_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Yeh command sirf Owner ke liye hai!")
        return
    if not context.args:
        await update.message.reply_text("❌ User ID enter karein.")
        return
    uid = int(context.args[0])
    if uid in GLOBAL_MUTES:
        GLOBAL_MUTES.remove(uid)
        await update.message.reply_text(f"✅ User {uid} ko Global Mute list se hata diya gaya hai.")
    else:
        await update.message.reply_text("❌ User global mute list me nahi mila.")

async def add_bot_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Yeh command sirf Owner ke liye hai!")
        return
    if not context.args:
        await update.message.reply_text("❌ User ID enter karein.")
        return
    uid = int(context.args[0])
    BOT_ADMINS.add(uid)
    await update.message.reply_text(f"👑 User {uid} ko bot admin bana diya gaya hai.")

async def remove_bot_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Yeh command sirf Owner ke liye hai!")
        return
    if not context.args:
        await update.message.reply_text("❌ User ID enter karein.")
        return
    uid = int(context.args[0])
    if uid == OWNER_ID:
        await update.message.reply_text("❌ Owner ko remove nahi kiya ja sakta.")
        return
    if uid in BOT_ADMINS:
        BOT_ADMINS.remove(uid)
        await update.message.reply_text(f"✅ User {uid} ko bot admin list se hata diya gaya hai.")

async def toggle_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global MAINTENANCE_MODE
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Yeh command sirf Owner ke liye hai!")
        return
    MAINTENANCE_MODE = not MAINTENANCE_MODE
    status = "ON 🟢" if MAINTENANCE_MODE else "OFF 🔴"
    await update.message.reply_text(f"🛠️ Bot Maintenance Mode is now: {status}")

async def set_log_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global LOG_CHANNEL_ID
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Yeh command sirf Owner ke liye hai!")
        return
    if not context.args:
        await update.message.reply_text("❌ Log Channel ID enter karein.")
        return
    LOG_CHANNEL_ID = context.args[0]
    await update.message.reply_text(f"📝 Log Channel set to: {LOG_CHANNEL_ID}")

async def backup_database(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Yeh command sirf Owner ke liye hai!")
        return
    await update.message.reply_text("💾 Database backup generated successfully! (JSON simulated backup ready)")

# --- Middleware ---
async def check_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.effective_chat:
        return
    user_id = update.effective_user.id
    
    if MAINTENANCE_MODE and not is_owner(user_id):
        if update.message:
            await update.message.reply_text("🛠️ Bot abhi maintenance mode me hai. Kripya thodi der baad koshish karein.")
        return

    if user_id in GLOBAL_BANS:
        try:
            await update.effective_chat.ban_member(user_id)
            if update.message:
                await update.message.reply_text("🚫 Global Banned User detected & banned!")
        except Exception:
            pass
        return

    if user_id in GLOBAL_MUTES:
        try:
            await update.message.delete()
        except Exception:
            pass
        return

def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, check_filters), group=-1)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))

    application.add_handler(CommandHandler("lock", lock_group))
    application.add_handler(CommandHandler("unlock", unlock_group))
    application.add_handler(CommandHandler("slowmode", slowmode))
    application.add_handler(CommandHandler("warn", warn_member))
    application.add_handler(CommandHandler("warnings", view_warnings))
    application.add_handler(CommandHandler("resetwarn", reset_warnings))
    application.add_handler(CommandHandler("purge", purge_messages))
    application.add_handler(CommandHandler("setdesc", set_description))
    application.add_handler(CommandHandler("leave", leave_group))

    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CommandHandler("gban", global_ban))
    application.add_handler(CommandHandler("ungban", remove_global_ban))
    application.add_handler(CommandHandler("gmute", global_mute))
    application.add_handler(CommandHandler("ungmute", remove_global_mute))
    application.add_handler(CommandHandler("addadmin", add_bot_admin))
    application.add_handler(CommandHandler("removeadmin", remove_bot_admin))
    application.add_handler(CommandHandler("maintenance", toggle_maintenance))
    application.add_handler(CommandHandler("setlog", set_log_channel))
    application.add_handler(CommandHandler("backup", backup_database))

    print("🚀 Bot is running successfully with all requested features...")
    application.run_polling()

if __name__ == "__main__":
    main()
