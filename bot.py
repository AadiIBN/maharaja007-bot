# bot.py — Auto-Approval + Auto-Kick + Data Export + Admin Link Management
# Python 3.10+ | python-telegram-bot 21.x | aiosqlite | google-generativeai

import os
import re
import time
import asyncio
import logging
import threading
import aiosqlite
import requests
import datetime
import pytz
import csv
from http.server import HTTPServer, BaseHTTPRequestHandler
from io import BytesIO, StringIO

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
ADMIN_IDS = [a.strip() for a in os.getenv("ADMIN_IDS", "").split(",") if a.strip()] if os.getenv("ADMIN_IDS") else []

BROKERS = ["XM", "Vantage"]
DB_PATH = "maharaja_bot.db"

# States
CHOOSE_BROKER, ASK_CLIENT_ID = range(2)

# Commands
USER_COMMANDS = [
    ("start", "Start verification"),
    ("ai", "Use AI Mentor"),
    ("help", "Show help"),
]
ADMIN_COMMANDS = [
    ("setgroup", "Set VIP Link: /setgroup <Broker> <Link>"),
    ("kick_inactive", "Run 15-day kick check"),
    ("export_data", "Download User Data (CSV)"),
    ("stats", "Show stats"),
    ("broadcast", "Msg to all"),
]

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
        # last_trade_date format: YYYY-MM-DD
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

# ---------------- VERIFICATION LOGIC ----------------

async def verify_xm_user(client_id):
    if not XM_TOKEN: return False
    url = f"https://mypartners.xm.com/api/traders/{client_id}"
    headers = {"Authorization": f"Bearer {XM_TOKEN}", "Content-Type": "application/json"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"XM Error: {e}")
        return False

async def verify_vantage_user(client_id):
    if not VANTAGE_USER_ID or not VANTAGE_SECRET: return False
    url = "https://openapi.vantagemarkets.com/api/ibData/accountData"
    now = datetime.datetime.now()
    end_time = now.strftime("%Y-%m-%d %H:%M:%S")
    start_time = (now - datetime.timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")
    
    payload = {
        "userId": int(VANTAGE_USER_ID),
        "secret": VANTAGE_SECRET,
        "startTime": start_time,
        "endTime": end_time
    }
    try:
        response = requests.post(url, json=payload, timeout=15)
        data = response.json()
        if data.get("code") == 1:
            for acc in data.get("data", []):
                if str(acc.get("account")) == str(client_id):
                    return True
        return False
    except Exception as e:
        logger.error(f"Vantage Error: {e}")
        return False

# ---------------- INACTIVITY & KICK LOGIC ----------------

async def update_trade_dates_and_kick(context: ContextTypes.DEFAULT_TYPE):
    if not VIP_CHANNEL_ID:
        logger.warning("VIP_CHANNEL_ID not set. Cannot kick users.")
        return

    logger.info("⏳ Starting Inactivity Check...")
    
    active_clients_map = {} 
    
    if VANTAGE_USER_ID and VANTAGE_SECRET:
        url = "https://openapi.vantagemarkets.com/api/ibData/commissionData"
        try:
            payload = {"userId": int(VANTAGE_USER_ID), "secret": VANTAGE_SECRET}
            response = requests.post(url, json=payload, timeout=20)
            data = response.json()
            
            if data.get("code") == 1:
                for record in data.get("data", []):
                    c_id = str(record.get("account"))
                    trade_time_str = record.get("last Trade Time")
                    
                    if trade_time_str:
                        try:
                            t_date = datetime.datetime.strptime(str(trade_time_str), "%Y-%m-%d %H:%M:%S").date()
                            active_clients_map[c_id] = t_date
                        except:
                            pass 
        except Exception as e:
            logger.error(f"Failed to fetch Vantage Trade Data: {e}")

    async with get_db() as db:
        async with db.execute("SELECT tg_user_id, client_id, last_trade_date FROM submissions WHERE broker='Vantage' AND status='approved'") as cursor:
            users = await cursor.fetchall()
            
        today = datetime.date.today()
        
        for u in users:
            tg_id, client_id, db_last_trade = u
            latest_trade_date = None
            
            if client_id in active_clients_map:
                latest_trade_date = active_clients_map[client_id]
                await db.execute("UPDATE submissions SET last_trade_date=? WHERE client_id=?", (str(latest_trade_date), client_id))
            elif db_last_trade:
                try:
                    latest_trade_date = datetime.datetime.strptime(db_last_trade, "%Y-%m-%d").date()
                except: pass

            if latest_trade_date:
                days_inactive = (today - latest_trade_date).days
                if days_inactive > 15:
                    try:
                        await context.bot.ban_chat_member(chat_id=VIP_CHANNEL_ID, user_id=tg_id)
                        await context.bot.unban_chat_member(chat_id=VIP_CHANNEL_ID, user_id=tg_id)
                        await db.execute("UPDATE submissions SET status='kicked_inactive' WHERE client_id=?", (client_id,))
                        try:
                            await context.bot.send_message(
                                chat_id=tg_id, 
                                text=f"⚠️ **Alert:** Aapko VIP Group se remove kar diya gaya hai kyunki aapne pichle **{days_inactive} dinon** se trade nahi kiya.\n\nDobara join karne ke liye trading start karein aur `/start` dabayein."
                            )
                        except: pass
                        logger.info(f"Kicked User {tg_id} (Inactive {days_inactive} days)")
                    except Exception as e:
                        logger.error(f"Cannot kick {tg_id}: {e}")
        
        await db.commit()
    logger.info("Inactivity Check Complete.")

# ---------------- COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    async with get_db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (tg_user_id, username) VALUES (?,?)",
            (user.id, user.username)
        )
        await db.commit()

    keyboard = [[InlineKeyboardButton(b, callback_data=f"broker:{b}")] for b in BROKERS]
    
    await update.message.reply_text(
        f"👋 **Namaste {user.first_name}!**\n\n"
        "Maharaja VIP Club Access & AI Mentor.\n"
        "Select your Broker to verify:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )
    return CHOOSE_BROKER

async def on_broker_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    broker = query.data.split(":")[1]
    context.user_data["broker"] = broker
    await query.message.reply_text(f"👉 Enter your **{broker} Account ID**:", parse_mode=ParseMode.MARKDOWN)
    return ASK_CLIENT_ID

async def on_client_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    client_id = update.message.text.strip()
    broker = context.user_data.get("broker")

    if not client_id.isdigit():
        await update.message.reply_text("❌ Numbers only.")
        return ASK_CLIENT_ID

    status_msg = await update.message.reply_text("🔄 Verifying...", parse_mode=ParseMode.MARKDOWN)
    
    is_valid = False
    if broker == "XM":
        is_valid = await verify_xm_user(client_id)
    elif broker == "Vantage":
        is_valid = await verify_vantage_user(client_id)
    
    if is_valid:
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        async with get_db() as db:
            await db.execute("DELETE FROM submissions WHERE client_id=? AND broker=?", (client_id, broker)) 
            await db.execute(
                "INSERT INTO submissions (tg_user_id, broker, client_id, status, last_trade_date) VALUES (?,?,?, 'approved', ?)",
                (user_id, broker, client_id, today_str)
            )
            await db.commit()
        
        vip_link = None
        async with get_db() as db:
            async with db.execute("SELECT invite_link FROM vip_links WHERE broker=?", (broker,)) as cursor:
                row = await cursor.fetchone()
                if row: vip_link = row[0]
        
        msg = (
            f"🎉 **Verified!**\n"
            f"✅ Broker: {broker}\n\n"
            f"🔗 **VIP Link:**\n{vip_link if vip_link else 'Link coming soon...'}\n\n"
            f"⚠️ **Note:** Active trading is required. **15 days inactivity = Auto Kick.**\n\n"
            f"🔓 **AI Mentor Unlocked!** Send me charts anytime."
        )
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=status_msg.message_id, text=msg, parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END
    else:
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=status_msg.message_id, text="❌ Verification Failed. ID not found under our IB.", parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END

# ---------------- AI HANDLER ----------------
async def handle_mentorship(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_verified = False
    async with get_db() as db:
        async with db.execute("SELECT 1 FROM submissions WHERE tg_user_id=? AND status='approved' LIMIT 1", (user_id,)) as cursor:
            if await cursor.fetchone(): is_verified = True
            
    if not is_verified:
        await update.message.reply_text("🔒 Locked. Verify first.", parse_mode=ParseMode.MARKDOWN)
        return

    image_bytes = None
    user_text = update.message.caption or update.message.text
    if not user_text and not image_bytes and not update.message.photo: return

    status_msg = await update.message.reply_text("🧠 Analyzing...")
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    try:
        if update.message.photo:
            photo_file = await update.message.photo[-1].get_file()
            image_stream = BytesIO()
            await photo_file.download_to_memory(image_stream)
            image_bytes = image_stream.getvalue()
        
        response = await analyze_ssm_request(user_text, image_bytes)
        try:
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=status_msg.message_id, text=response, parse_mode=ParseMode.MARKDOWN)
        except:
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=status_msg.message_id, text=response)
    except Exception as e:
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=status_msg.message_id, text=f"⚠️ Error: {str(e)}")

# ---------------- ADMIN COMMANDS (DATA EXPORT & LINK MANAGEMENT) ----------------

async def cmd_setgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in ADMIN_IDS: return
    try:
        broker = context.args[0]
        link = context.args[1]
        async with get_db() as db:
            await db.execute("INSERT OR REPLACE INTO vip_links (broker, invite_link) VALUES (?,?)", (broker, link))
            await db.commit()
        await update.message.reply_text(f"✅ Link set for {broker}")
    except:
        await update.message.reply_text("Usage: /setgroup <XM/Vantage> <Link>")

async def cmd_export_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Exports all submissions data to a CSV file and sends it to the admin.
    """
    user_id = str(update.effective_user.id)
    if user_id not in ADMIN_IDS: return

    await update.message.reply_text("⏳ Generating CSV file...")

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM submissions ORDER BY created_at DESC") as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await update.message.reply_text("❌ No data found.")
        return

    # Create CSV in memory
    output = StringIO()
    writer = csv.writer(output)
    
    # Write Header (Column Names)
    writer.writerow(rows[0].keys())
    
    # Write Data
    for row in rows:
        writer.writerow(tuple(row))
    
    output.seek(0)
    
    # Convert StringIO to BytesIO for sending as file
    bytes_io = BytesIO(output.getvalue().encode('utf-8'))
    bytes_io.name = f"maharaja_users_{int(time.time())}.csv"

    await context.bot.send_document(
        chat_id=update.effective_chat.id, 
        document=bytes_io, 
        caption="✅ Here is the complete user data."
    )

async def cmd_kick_inactive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in ADMIN_IDS: return
    await update.message.reply_text("⏳ Running inactivity check...")
    await update_trade_dates_and_kick(context)
    await update.message.reply_text("✅ Check complete.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.", reply_markup=ReplyKeyboardRemove())
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

    if app.job_queue:
        app.job_queue.run_daily(update_trade_dates_and_kick, time=datetime.time(hour=12, minute=0, tzinfo=pytz.UTC))
        print("✅ Auto-Kick Job Scheduled.")

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSE_BROKER: [CallbackQueryHandler(on_broker_choice, pattern=r"^broker:")],
            ASK_CLIENT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_client_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("setgroup", cmd_setgroup))
    app.add_handler(CommandHandler("kick_inactive", cmd_kick_inactive))
    app.add_handler(CommandHandler("export_data", cmd_export_data))
    app.add_handler(MessageHandler(filters.PHOTO | (filters.TEXT & ~filters.COMMAND), handle_mentorship))

    print("✅ Maharaja Auto-Bot Started...")
    app.run_polling()

if __name__ == "__main__":
    main()
