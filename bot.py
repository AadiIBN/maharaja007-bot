# bot.py — Verified Users Only for AI + Render Fix (English Version)
# Python 3.10+ | python-telegram-bot 21.x | aiosqlite | openai

import os
import re
import time
import asyncio
import logging
import csv
import threading
import aiosqlite
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional
from io import BytesIO

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    BotCommand,
    BotCommandScopeChat,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from telegram.error import Forbidden

# --- IMPORT AI MODULE ---
from ssm_ai import analyze_ssm_request

# ---------------- LOGGING ----------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------------- CONFIG ----------------
BROKERS = ["Exness", "IC Markets", "FBS"]
DB_PATH = "maharaja_bot.db"

# Conversation States
CHOOSE_BROKER, ASK_CLIENT_ID, ASK_SCREENSHOT = range(3)
COOLDOWN_SECONDS = 120
ONE_TIME_VERIFICATION = False

# Commands List
USER_COMMANDS = [
    ("start", "Start verification flow"),
    ("help", "Show help / command list"),
    ("cancel", "Cancel verification"),
]
ADMIN_COMMANDS = [
    ("setgroup", "Set VIP link: /setgroup <broker> <link>"),
    ("brokers", "List brokers & links"),
    ("users", "Show users summary"),
    ("pending", "List pending submissions"),
    ("find", "Find by user/client ID"),
    ("stats", "Show stats"),
    ("ban", "Ban user"),
    ("unban", "Unban user"),
    ("broadcast", "Send msg to all users"),
    ("exportcsv", "Export Data (Lightweight)"),
    ("refreshcommands", "Refresh menu"),
]

# ---------------- RENDER KEEP-ALIVE ----------------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive and running!")

def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    print(f"🌍 Dummy Web Server started on port {port}")
    server.serve_forever()

# ---------------- ASYNC DB MANAGER ----------------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                tg_user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_submit_ts INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_user_id INTEGER NOT NULL,
                broker TEXT NOT NULL,
                client_id TEXT NOT NULL,
                screenshot_file_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS vip_links (
                broker TEXT PRIMARY KEY,
                invite_link TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bans (
                tg_user_id INTEGER PRIMARY KEY,
                banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reason TEXT
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_sub_user ON submissions(tg_user_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_sub_status ON submissions(status);")
        for b in BROKERS:
            await db.execute("INSERT OR IGNORE INTO vip_links (broker, invite_link) VALUES (?, NULL)", (b,))
        await db.commit()

def get_db():
    return aiosqlite.connect(DB_PATH)

# ---------------- HELPERS ----------------
def is_admin(user_id: int) -> bool:
    admins = [a.strip() for a in os.getenv("ADMIN_IDS", "").split(",") if a.strip()]
    return str(user_id) in admins

async def is_banned(user_id: int) -> bool:
    async with get_db() as db:
        async with db.execute("SELECT 1 FROM bans WHERE tg_user_id=?", (user_id,)) as cursor:
            return bool(await cursor.fetchone())

# Check if user is verified (Has ANY approved submission)
async def is_verified_user(user_id: int) -> bool:
    async with get_db() as db:
        async with db.execute("SELECT 1 FROM submissions WHERE tg_user_id=? AND status='approved' LIMIT 1", (user_id,)) as cursor:
            return bool(await cursor.fetchone())

def broker_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(b, callback_data=f"broker:{b}")] for b in BROKERS])

def approval_keyboard(sid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"approve:{sid}"),
        InlineKeyboardButton("⛔ Reject",  callback_data=f"reject:{sid}")
    ]])

async def show_typing(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int):
    try:
        await ctx.bot.send_chat_action(chat_id, ChatAction.TYPING)
    except Exception: pass

async def notify_admins(ctx: ContextTypes.DEFAULT_TYPE, text: str, markup=None):
    admins = [x.strip() for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
    for a in admins:
        try:
            await ctx.bot.send_message(int(a), text, reply_markup=markup)
        except Exception: pass

async def user_has_approved(user_id: int, broker: Optional[str] = None) -> bool:
    async with get_db() as db:
        if ONE_TIME_VERIFICATION:
            async with db.execute("SELECT 1 FROM submissions WHERE tg_user_id=? AND status='approved' LIMIT 1", (user_id,)) as cursor:
                return bool(await cursor.fetchone())
        if broker is None:
            return False
        async with db.execute("SELECT 1 FROM submissions WHERE tg_user_id=? AND broker=? AND status='approved' LIMIT 1", (user_id, broker)) as cursor:
            return bool(await cursor.fetchone())

async def send_vip_link(ctx: ContextTypes.DEFAULT_TYPE, user_id: int, broker: str):
    link = None
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT invite_link FROM vip_links WHERE broker=?", (broker,)) as cursor:
            row = await cursor.fetchone()
            if row: link = row["invite_link"]
    
    # --- UPDATED SUCCESS MESSAGE (ENGLISH) ---
    if link:
        msg = (
            f"🎉 *Congratulations! You are verified.*\n"
            f"Broker: {broker}\n\n"
            f"✅ **VIP Access Granted:**\n"
            f"[Join VIP Channel]({link})\n\n"
            f"🔓 **BONUS UNLOCKED:**\n"
            f"**Shaakuni AI Mentor** is now active! 🤖\n"
            f"You can send me any **Chart (Photo)** or **Question**, and I will analyze it using deep SSM logic.\n\n"
            f"Try it now: Send a chart!"
        )
    else:
        msg = (
            f"🎉 *Congratulations! You are verified.*\n"
            f"Broker: {broker}\n\n"
            f"(VIP link is being updated by admin. Please wait.)\n\n"
            f"🔓 **BONUS UNLOCKED:**\n"
            f"**Shaakuni AI Mentor** is now active!\n"
            f"You can start sending charts for analysis."
        )
    
    try:
        await ctx.bot.send_message(user_id, msg, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Error sending link to {user_id}: {e}")

def normalize_client_id(broker: str, cid: str) -> Optional[str]:
    cid = (cid or "").strip()
    if not cid: return None
    if broker in ("Exness", "FBS"):
        return cid if re.fullmatch(r"\d{6,12}", cid) else None
    if broker == "IC Markets":
        return cid if re.fullmatch(r"[A-Za-z0-9_-]{5,16}", cid) else None
    return cid

# ---------------- USER FLOW (VERIFICATION) ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    chat_id = update.effective_chat.id

    if await is_banned(u.id): return 

    async with get_db() as db:
        await db.execute(
            "INSERT INTO users (tg_user_id, username, first_name, last_name) VALUES (?,?,?,?) "
            "ON CONFLICT(tg_user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name, last_name=excluded.last_name",
            (u.id, u.username, u.first_name, u.last_name),
        )
        await db.commit()

    try:
        cmds = [BotCommand(n, d) for n, d in USER_COMMANDS]
        if is_admin(u.id):
            cmds += [BotCommand(n, d) for n, d in ADMIN_COMMANDS]
        await context.bot.set_my_commands(cmds, scope=BotCommandScopeChat(chat_id))
    except Exception: pass

    await update.message.reply_text("Choose your Forex broker:", reply_markup=broker_keyboard())
    return CHOOSE_BROKER

async def on_broker_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, broker = q.data.split(":", 1)
    context.user_data["broker"] = broker
    u = update.effective_user

    if await is_banned(u.id): return await q.message.reply_text("🚫 Banned.")

    if await user_has_approved(u.id, broker):
        return await q.message.reply_text(f"✅ You are already verified for {broker}.")

    async with get_db() as db:
        async with db.execute("SELECT 1 FROM submissions WHERE tg_user_id=? AND broker=? AND status='pending'", (u.id, broker)) as cursor:
            if await cursor.fetchone():
                return await q.message.reply_text(f"⏳ You already have a pending request for {broker}.")

    await q.message.reply_text(f"You selected: **{broker}**\n👉 Enter your Client ID / Account ID:", parse_mode=ParseMode.MARKDOWN)
    return ASK_CLIENT_ID

async def on_client_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if await is_banned(u.id): return

    broker = context.user_data.get("broker")
    client_id = normalize_client_id(broker, (update.message.text or "").strip())
    
    if not client_id:
        return await update.message.reply_text(f"❌ Invalid ID format for {broker}. Try again.")

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT last_submit_ts FROM users WHERE tg_user_id=?", (u.id,)) as cursor:
            row = await cursor.fetchone()
            last_ts = row["last_submit_ts"] if row else 0

    now = int(time.time())
    if now - last_ts < COOLDOWN_SECONDS:
        return await update.message.reply_text(f"⏳ Please wait {COOLDOWN_SECONDS - (now - last_ts)}s.")

    context.user_data["client_id"] = client_id
    await update.message.reply_text(
        "📸 (Optional) Send deposit screenshot now, or tap 'Skip'.",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Skip")]], resize_keyboard=True, one_time_keyboard=True)
    )
    return ASK_SCREENSHOT

async def on_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if await is_banned(u.id): return

    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.text and update.message.text.lower() != "skip":
        return await update.message.reply_text("Please send a photo or tap Skip.")

    broker = context.user_data.get("broker")
    client_id = context.user_data.get("client_id")

    async with get_db() as db:
        await db.execute(
            "INSERT INTO submissions (tg_user_id, broker, client_id, screenshot_file_id, status) VALUES (?,?,?,?, 'pending')",
            (u.id, broker, client_id, file_id),
        )
        await db.execute("UPDATE users SET last_submit_ts=? WHERE tg_user_id=?", (int(time.time()), u.id))
        async with db.execute("SELECT last_insert_rowid()") as cursor:
            sub_id = (await cursor.fetchone())[0]
        await db.commit()

    await update.message.reply_text(
        "✅ **Submitted!**\nWe will notify you after admin review.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove()
    )

    admin_text = (
        f"🔔 **New Request #{sub_id}**\n"
        f"User: {u.full_name} [ID: {u.id}]\n"
        f"Broker: {broker}\n"
        f"Client ID: `{client_id}`\n"
    )
    await notify_admins(context, admin_text, approval_keyboard(sub_id))
    
    if file_id:
        admins = [x.strip() for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
        for a in admins:
            try:
                await context.bot.send_photo(int(a), file_id, caption=f"Screenshot for #{sub_id}")
            except Exception: pass

    return ConversationHandler.END

# ---------------- NEW AI MENTOR HANDLER (LOCKED FOR NON-VERIFIED) ----------------
async def handle_mentorship(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles Photos (Charts) and Text questions.
    LOCKS feature if user is not verified.
    """
    u = update.effective_user
    if await is_banned(u.id): return

    # --- 1. CHECK VERIFICATION STATUS ---
    is_verified = await is_verified_user(u.id)

    if not is_verified:
        # USER NOT VERIFIED -> REJECT REQUEST (ENGLISH)
        msg = (
            "🔒 **Premium Feature Locked**\n\n"
            "Sorry, **Shaakuni AI Mentor** is exclusively for verified VIP members.\n\n"
            "👉 **How to unlock?**\n"
            "1. Tap /start.\n"
            "2. Select your Broker and verify your account.\n"
            "3. Once approved by Admin, this feature will unlock automatically."
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        return

    # --- 2. USER IS VERIFIED -> PROCEED WITH AI ---
    # Check if we have a photo or text
    image_bytes = None
    user_text = update.message.caption or update.message.text

    # Notify user we are thinking (English)
    status_msg = await update.message.reply_text("🧠 Analyzing Shaakuni Strategy... Please wait.")
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    try:
        # If user sent a photo
        if update.message.photo:
            photo_file = await update.message.photo[-1].get_file()
            # Download to memory
            image_stream = BytesIO()
            await photo_file.download_to_memory(image_stream)
            image_bytes = image_stream.getvalue()
        
        # Call AI
        response = await analyze_ssm_request(user_text, image_bytes)

        # Reply (Use Markdown for formatting)
        try:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=status_msg.message_id,
                text=response,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            # Fallback if Markdown fails
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=status_msg.message_id,
                text=response
            )

    except Exception as e:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=status_msg.message_id,
            text=f"❌ Error during analysis: {str(e)}"
        )

# ---------------- ADMIN ACTIONS ----------------
async def on_decide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(update.effective_user.id): return
        
    action, sid = q.data.split(":", 1)
    sid = int(sid)
    
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM submissions WHERE id=?", (sid,)) as cursor:
            sub = await cursor.fetchone()
            
        if not sub: return await q.edit_message_text("❌ Submission not found.")
            
        if action == "approve":
            await db.execute("UPDATE submissions SET status='approved', updated_at=CURRENT_TIMESTAMP WHERE id=?", (sid,))
            await db.commit()
            await q.edit_message_text(f"✅ Approved #{sid} (User notified).")
            # Send VIP link + Unlock Notification
            await send_vip_link(context, sub["tg_user_id"], sub["broker"])
        else:
            await db.execute("UPDATE submissions SET status='rejected', updated_at=CURRENT_TIMESTAMP WHERE id=?", (sid,))
            await db.commit()
            await q.edit_message_text(f"⛔ Rejected #{sid}.")
            try:
                await context.bot.send_message(sub["tg_user_id"], "❌ Your verification was rejected.")
            except Exception: pass

# ---------------- ADMIN COMMANDS ----------------
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    msg = " ".join(context.args).strip()
    if not msg: return await update.message.reply_text("Usage: /broadcast <message>")
    
    await update.message.reply_text(f"📢 Starting broadcast in background...")
    asyncio.create_task(run_broadcast(context, msg, update.effective_user.id))

async def run_broadcast(context, msg, admin_id):
    sent, failed, blocked = 0, 0, 0
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT tg_user_id FROM users") as cursor:
            async for row in cursor:
                try:
                    await context.bot.send_message(row["tg_user_id"], msg)
                    sent += 1
                    await asyncio.sleep(0.05) 
                except Forbidden: blocked += 1
                except Exception: failed += 1
    
    try:
        await context.bot.send_message(admin_id, f"📢 **Broadcast Done**\n✅ Sent: {sent}\n🚫 Blocked: {blocked}\n❌ Failed: {failed}", parse_mode=ParseMode.MARKDOWN)
    except: pass

async def cmd_exportcsv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await show_typing(context, update.effective_chat.id)
    filename = "export_users.csv"
    
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["ID", "User ID", "Broker", "Client ID", "Status", "Created At"])
            async with db.execute("SELECT id, tg_user_id, broker, client_id, status, created_at FROM submissions ORDER BY created_at DESC") as cursor:
                async for row in cursor:
                    writer.writerow([row['id'], row['tg_user_id'], row['broker'], row['client_id'], row['status'], row['created_at']])

    await context.bot.send_document(update.effective_chat.id, document=open(filename, 'rb'), caption="✅ Data Export (CSV)")
    os.remove(filename)

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        users = (await (await db.execute("SELECT COUNT(*) c FROM users")).fetchone())['c']
        pending = (await (await db.execute("SELECT COUNT(*) c FROM submissions WHERE status='pending'")).fetchone())['c']
    await update.message.reply_text(f"📊 **Stats**\nUsers: {users}\nPending Requests: {pending}", parse_mode=ParseMode.MARKDOWN)

async def cmd_setgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or len(context.args) < 2: return
    broker = context.args[0]
    link = context.args[1]
    if broker not in BROKERS: return await update.message.reply_text(f"Invalid broker. Use: {BROKERS}")
    async with get_db() as db:
        await db.execute("INSERT INTO vip_links (broker, invite_link) VALUES (?,?) ON CONFLICT(broker) DO UPDATE SET invite_link=excluded.invite_link", (broker, link))
        await db.commit()
    await update.message.reply_text(f"✅ Link set for {broker}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ---------------- BOOT ----------------
def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        print("❌ Error: BOT_TOKEN is missing.")
        return

    # Start Dummy Web Server in Background Thread
    threading.Thread(target=start_web_server, daemon=True).start()

    app = Application.builder().token(token).build()

    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_db())

    # 1. Verification Conversation (Iska priority high hona chahiye)
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSE_BROKER: [CallbackQueryHandler(on_broker_choice, pattern=r"^broker:")],
            ASK_CLIENT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_client_id)],
            ASK_SCREENSHOT: [MessageHandler(filters.ALL, on_screenshot)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv)

    # 2. Admin Handlers
    app.add_handler(CallbackQueryHandler(on_decide, pattern=r"^(approve|reject):"))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("exportcsv", cmd_exportcsv))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("setgroup", cmd_setgroup))

    # 3. AI MENTOR HANDLER (Lowest Priority - For random chats/photos)
    # Ye handler tabhi chalega jab upar wala Conversation active nahi hoga
    app.add_handler(MessageHandler(filters.PHOTO | (filters.TEXT & ~filters.COMMAND), handle_mentorship))

    print("✅ Bot is running with Shaakuni AI (Verified Only Mode)...")
    app.run_polling()

if __name__ == "__main__":
    main()
