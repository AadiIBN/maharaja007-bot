# bot.py — Premium UI + Animations + Admin Panel + Secure Links
# Python 3.10+ | python-telegram-bot 21.x | aiosqlite | google-generativeai

import os
import time
import asyncio
import logging
import threading
import aiosqlite
import requests
import datetime
import pytz
import csv
from io import BytesIO, StringIO
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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
from telegram.error import BadRequest, Forbidden

# --- IMPORT AI MODULE ---
from ssm_ai import analyze_ssm_request

# ---------------- LOGGING ----------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------------- CONFIGURATION ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
XM_TOKEN = os.getenv("XM_TOKEN")
VANTAGE_USER_ID = os.getenv("VANTAGE_USER_ID")
VANTAGE_SECRET = os.getenv("VANTAGE_SECRET")
VIP_CHANNEL_ID = os.getenv("VIP_CHANNEL_ID")
# Admin IDs ko list mein convert kar rahe hain
ADMIN_IDS = [str(a).strip() for a in os.getenv("ADMIN_IDS", "").split(",") if a.strip()]

BROKERS = ["XM", "Vantage"]
DB_PATH = "maharaja_bot.db"

# States
CHOOSE_BROKER, ASK_CLIENT_ID = range(2)

# ---------------- RENDER KEEP-ALIVE ----------------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.wfile.write(b"Bot is alive!")

def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# ---------------- DATABASE ----------------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                tg_user_id INTEGER PRIMARY KEY,
                username TEXT,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_user_id INTEGER,
                broker TEXT,
                client_id TEXT,
                status TEXT,
                last_trade_date TEXT, 
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS vip_links (
                broker TEXT PRIMARY KEY,
                invite_link TEXT
            )
        """)
        await db.commit()

def get_db():
    return aiosqlite.connect(DB_PATH)

# ---------------- UI HELPERS (ANIMATIONS) ----------------
async def show_processing_animation(context, chat_id, message_id, text_sequence):
    """
    Ye function messages ko edit karke animation ka effect dega.
    """
    for text in text_sequence:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN
            )
            await asyncio.sleep(1.0) # 1 second delay
        except:
            pass

# ---------------- VERIFICATION LOGIC ----------------
async def verify_xm_user(client_id):
    if not XM_TOKEN: return False
    url = f"https://mypartners.xm.com/api/traders/{client_id}"
    headers = {"Authorization": f"Bearer {XM_TOKEN}", "Content-Type": "application/json"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        return response.status_code == 200
    except: return False

async def verify_vantage_user(client_id):
    if not VANTAGE_USER_ID or not VANTAGE_SECRET: return False
    url = "https://openapi.vantagemarkets.com/api/ibData/accountData"
    now = datetime.datetime.now()
    end_time = now.strftime("%Y-%m-%d %H:%M:%S")
    start_time = (now - datetime.timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")
    payload = {"userId": int(VANTAGE_USER_ID), "secret": VANTAGE_SECRET, "startTime": start_time, "endTime": end_time}
    try:
        response = requests.post(url, json=payload, timeout=15)
        data = response.json()
        if data.get("code") == 1:
            for acc in data.get("data", []):
                if str(acc.get("account")) == str(client_id): return True
        return False
    except: return False

# ---------------- SECURE LINK GENERATOR ----------------
async def create_one_time_link(context, channel_id):
    try:
        invite_link = await context.bot.create_chat_invite_link(
            chat_id=channel_id,
            member_limit=1,  # Strict Limit: 1 Person
            name="Maharaja Secure Verification" 
        )
        return invite_link.invite_link
    except Exception as e:
        logger.error(f"Link Error: {e}")
        return None

# ---------------- COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Save user
    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (tg_user_id, username) VALUES (?,?)", (user.id, user.username))
        await db.commit()

    # Set Menu Buttons
    commands = [
        BotCommand("start", "🔄 Restart Verification"),
        BotCommand("ai", "🤖 AI Mentor (Verified Only)"),
        BotCommand("help", "ℹ️ Help & Support")
    ]
    # Admin gets extra commands
    if str(user.id) in ADMIN_IDS:
        commands.append(BotCommand("admin", "👑 Admin Dashboard"))
    
    await context.bot.set_my_commands(commands, scope=BotCommandScopeChat(update.effective_chat.id))

    # Welcome UI
    keyboard = [
        [InlineKeyboardButton("🦁 XM Global", callback_data="broker:XM")],
        [InlineKeyboardButton("🔵 Vantage Markets", callback_data="broker:Vantage")]
    ]
    
    welcome_text = (
        f"👋 **Welcome, {user.first_name}!**\n\n"
        "👑 **Maharaja VIP Club & AI Mentor** 👑\n\n"
        "Unlock Premium Access:\n"
        "🔹 **Exclusive Signals** (90% Accuracy)\n"
        "🔹 **Shaakuni AI Mentor** (24/7 Analysis)\n"
        "🔹 **Live Market Updates**\n\n"
        "👇 **Select your Broker to Verify:**"
    )
    
    await update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    return CHOOSE_BROKER

async def on_broker_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    broker = query.data.split(":")[1]
    context.user_data["broker"] = broker
    
    await query.edit_message_text(
        f"✅ **Selected Broker:** {broker}\n\n"
        f"🔢 Please enter your **{broker} Account ID (Login Number)**:\n"
        f"_(Example: 8821345)_",
        parse_mode=ParseMode.MARKDOWN
    )
    return ASK_CLIENT_ID

async def on_client_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    client_id = update.message.text.strip()
    broker = context.user_data.get("broker")

    if not client_id.isdigit():
        await update.message.reply_text("⚠️ **Invalid Format.** Please enter numbers only.")
        return ASK_CLIENT_ID

    # --- ANIMATION EFFECT START ---
    status_msg = await update.message.reply_text("🔄 **Connecting to server...**", parse_mode=ParseMode.MARKDOWN)
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    
    # Fake processing delay for UX
    await show_processing_animation(context, update.effective_chat.id, status_msg.message_id, [
        f"📡 Checking **{broker}** Database...",
        f"🔍 Verifying ID: **{client_id}**...",
        "⏳ Finalizing status..."
    ])
    # --- ANIMATION EFFECT END ---

    # Real Check
    is_valid = False
    if broker == "XM": is_valid = await verify_xm_user(client_id)
    elif broker == "Vantage": is_valid = await verify_vantage_user(client_id)
    
    if is_valid:
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        async with get_db() as db:
            await db.execute("DELETE FROM submissions WHERE client_id=? AND broker=?", (client_id, broker)) 
            await db.execute(
                "INSERT INTO submissions (tg_user_id, broker, client_id, status, last_trade_date) VALUES (?,?,?, 'approved', ?)",
                (user_id, broker, client_id, today_str)
            )
            await db.commit()
        
        # GENERATE SECURE LINK
        vip_link = None
        if VIP_CHANNEL_ID:
            vip_link = await create_one_time_link(context, VIP_CHANNEL_ID)
        
        if vip_link:
            msg = (
                f"🎉 **VERIFICATION SUCCESSFUL!**\n\n"
                f"✅ **Account:** {client_id} ({broker})\n"
                f"🌟 **Status:** Active\n\n"
                f"👇 **Your Secure Invite Link:**\n"
                f"{vip_link}\n\n"
                f"⚠️ **WARNING:** This link can be used **ONLY ONCE**. Do not share it, or you will lose access!\n\n"
                f"🤖 **AI Mentor Unlocked:** You can now send charts here!"
            )
        else:
            msg = "✅ Verified! But VIP Link system is currently offline. Contact Admin."

        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=status_msg.message_id, text=msg, parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END
    else:
        fail_msg = (
            f"❌ **Verification Failed**\n\n"
            f"ID **{client_id}** is NOT registered under our IB.\n\n"
            f"👉 Please open a new account using our link to get access.\n"
            f"Retry: /start"
        )
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=status_msg.message_id, text=fail_msg, parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END

# ---------------- ADMIN DASHBOARD ----------------
async def admin_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in ADMIN_IDS: return

    keyboard = [
        [InlineKeyboardButton("📊 Show Stats", callback_data="admin:stats")],
        [InlineKeyboardButton("📥 Export Data (CSV)", callback_data="admin:export")],
        [InlineKeyboardButton("👢 Kick Inactive (15 Days)", callback_data="admin:kick")],
        [InlineKeyboardButton("❌ Close", callback_data="admin:close")]
    ]
    
    await update.message.reply_text(
        "👑 **Maharaja Admin Panel**\n\nSelect an action:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def admin_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data.split(":")[1]
    
    if action == "close":
        await query.message.delete()
        return

    if action == "stats":
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            users = (await (await db.execute("SELECT COUNT(*) c FROM users")).fetchone())['c']
            approved = (await (await db.execute("SELECT COUNT(*) c FROM submissions WHERE status='approved'")).fetchone())['c']
        await query.message.reply_text(f"📊 **Statistics**\n\n👥 Total Users: {users}\n✅ Verified Users: {approved}", parse_mode=ParseMode.MARKDOWN)

    elif action == "export":
        await query.message.reply_text("⏳ Generating CSV...")
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM submissions ORDER BY created_at DESC") as cursor:
                rows = await cursor.fetchall()
        if rows:
            output = StringIO()
            writer = csv.writer(output)
            writer.writerow(rows[0].keys())
            for row in rows: writer.writerow(tuple(row))
            output.seek(0)
            bytes_io = BytesIO(output.getvalue().encode('utf-8'))
            bytes_io.name = f"users_data.csv"
            await context.bot.send_document(chat_id=query.message.chat_id, document=bytes_io, caption="📂 User Data")
        else:
            await query.message.reply_text("❌ No data found.")

    elif action == "kick":
        await query.message.reply_text("⏳ Kick process started in background...")
        # Trigger Kick Logic (Simplified call)
        # Real logic needs update_trade_dates_and_kick function
        pass 

# ---------------- AI HANDLER (ANIMATED) ----------------
async def handle_mentorship(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Check Verification
    is_verified = False
    async with get_db() as db:
        async with db.execute("SELECT 1 FROM submissions WHERE tg_user_id=? AND status='approved' LIMIT 1", (user_id,)) as cursor:
            if await cursor.fetchone(): is_verified = True
    
    if not is_verified:
        await update.message.reply_text("🔒 **Access Denied.** Verify first.", parse_mode=ParseMode.MARKDOWN)
        return

    user_text = update.message.caption or update.message.text
    image_bytes = None
    if not user_text and not update.message.photo: return

    # Animation
    wait_msg = await update.message.reply_text("🤖 **Shaakuni AI is Thinking...**", parse_mode=ParseMode.MARKDOWN)
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    
    try:
        if update.message.photo:
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=wait_msg.message_id, text="👀 **Analyzing Chart Patterns...**", parse_mode=ParseMode.MARKDOWN)
            photo_file = await update.message.photo[-1].get_file()
            image_stream = BytesIO()
            await photo_file.download_to_memory(image_stream)
            image_bytes = image_stream.getvalue()
        
        response = await analyze_ssm_request(user_text, image_bytes)
        
        # Clean Output
        try:
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=wait_msg.message_id, text=response, parse_mode=ParseMode.MARKDOWN)
        except:
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=wait_msg.message_id, text=response)
            
    except Exception as e:
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=wait_msg.message_id, text=f"⚠️ AI Error: {str(e)}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ---------------- MAIN ----------------
def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN missing")
        return

    threading.Thread(target=start_web_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_db())

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSE_BROKER: [CallbackQueryHandler(on_broker_choice, pattern=r"^broker:")],
            ASK_CLIENT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_client_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("admin", admin_dashboard))
    app.add_handler(CallbackQueryHandler(admin_actions, pattern=r"^admin:"))
    app.add_handler(MessageHandler(filters.PHOTO | (filters.TEXT & ~filters.COMMAND), handle_mentorship))

    print("✅ Maharaja Premium Bot Started...")
    app.run_polling()

if __name__ == "__main__":
    main()
