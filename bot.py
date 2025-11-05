# bot.py — maharaja007 / @usrmaharaja007_bot
# Python 3.10+ | python-telegram-bot 22.x

import os
import re
import time
import csv
import sqlite3
import asyncio
from typing import Optional, List

from openpyxl import Workbook
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InputFile,
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ---------------- CONFIG ----------------
BROKERS = ["Exness", "IC Markets", "FBS"]
DB_PATH = "maharaja_bot.db"

CHOOSE_BROKER, ASK_CLIENT_ID, ASK_SCREENSHOT = range(3)
COOLDOWN_SECONDS = 120

# If True -> user jaisi hi kahin bhi approved ho, phir kisi broker pe dubara request na kar sake (global lock)
ONE_TIME_VERIFICATION = False

# ---------------- DB ----------------
SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
  tg_user_id INTEGER PRIMARY KEY,
  username TEXT,
  first_name TEXT,
  last_name TEXT,
  joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_submit_ts INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS submissions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tg_user_id INTEGER NOT NULL,
  broker TEXT NOT NULL,
  client_id TEXT NOT NULL,
  screenshot_file_id TEXT,
  status TEXT NOT NULL DEFAULT 'pending', -- pending|approved|rejected
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS vip_links (
  broker TEXT PRIMARY KEY,
  invite_link TEXT
);

CREATE TABLE IF NOT EXISTS bans (
  tg_user_id INTEGER PRIMARY KEY,
  banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_sub_user ON submissions(tg_user_id);
CREATE INDEX IF NOT EXISTS idx_sub_status ON submissions(status);
CREATE INDEX IF NOT EXISTS idx_sub_broker ON submissions(broker);
CREATE INDEX IF NOT EXISTS idx_sub_client ON submissions(client_id);
"""

def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with db() as con:
        con.executescript(SCHEMA_SQL)
        for b in BROKERS:
            con.execute("INSERT OR IGNORE INTO vip_links (broker, invite_link) VALUES (?, NULL)", (b,))
        con.commit()

# ---------------- HELPERS ----------------
def is_admin(user_id: int) -> bool:
    admins = [a.strip() for a in os.getenv("ADMIN_IDS", "").split(",") if a.strip()]
    return str(user_id) in admins

def is_banned(user_id: int) -> bool:
    with db() as con:
        row = con.execute("SELECT 1 FROM bans WHERE tg_user_id=?", (user_id,)).fetchone()
        return bool(row)

def broker_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(b, callback_data=f"broker:{b}")] for b in BROKERS])

def approval_keyboard(sid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"approve:{sid}"),
        InlineKeyboardButton("⛔ Reject",  callback_data=f"reject:{sid}")
    ]])

async def show_typing(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, seconds: float = 0.6):
    try:
        await ctx.bot.send_chat_action(chat_id, ChatAction.TYPING)
    except Exception:
        pass
    await asyncio.sleep(seconds)

async def notify_admins(ctx: ContextTypes.DEFAULT_TYPE, text: str, markup=None):
    for a in [x.strip() for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]:
        try:
            await show_typing(ctx, int(a), 0.3)
            await ctx.bot.send_message(int(a), text, reply_markup=markup)
        except Exception:
            pass

def user_has_approved(user_id: int, broker: Optional[str] = None) -> bool:
    with db() as con:
        if ONE_TIME_VERIFICATION:
            row = con.execute(
                "SELECT 1 FROM submissions WHERE tg_user_id=? AND status='approved' LIMIT 1",
                (user_id,)
            ).fetchone()
            return bool(row)
        if broker is None:
            return False
        row = con.execute(
            "SELECT 1 FROM submissions WHERE tg_user_id=? AND broker=? AND status='approved' LIMIT 1",
            (user_id, broker)
        ).fetchone()
        return bool(row)

async def send_vip_link(ctx: ContextTypes.DEFAULT_TYPE, user_id: int, broker: str):
    with db() as con:
        row = con.execute("SELECT invite_link FROM vip_links WHERE broker=?", (broker,)).fetchone()
    link = row["invite_link"] if row and row["invite_link"] else None
    if link:
        msg = f"🎉 Congratulations! You are verified.\nBroker: {broker}\n\nVIP link: {link}"
    else:
        msg = f"🎉 Congratulations! You are verified.\nBroker: {broker}\n\n(But VIP link is not set yet. Admin will share soon.)"
    await show_typing(ctx, user_id, 0.5)
    await ctx.bot.send_message(user_id, msg)

def normalize_client_id(broker: str, cid: str) -> Optional[str]:
    cid = (cid or "").strip()
    if not cid:
        return None
    if broker in ("Exness", "FBS"):
        return cid if re.fullmatch(r"\d{6,12}", cid) else None
    if broker == "IC Markets":
        return cid if re.fullmatch(r"[A-Za-z0-9_-]{5,16}", cid) else None
    return cid

# ---------------- USER FLOW ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    # ban check
    if is_banned(u.id):
        return await (update.message or update.callback_query.message).reply_text("You are banned from using this bot.")

    with db() as con:
        con.execute(
            "INSERT INTO users (tg_user_id, username, first_name, last_name) VALUES (?,?,?,?) "
            "ON CONFLICT(tg_user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name, last_name=excluded.last_name",
            (u.id, u.username, u.first_name, u.last_name),
        )
        con.commit()

    await show_typing(context, update.effective_chat.id, 0.4)
    await (update.message or update.callback_query.message).reply_text(
        "Choose your Forex broker:", reply_markup=broker_keyboard()
    )
    return CHOOSE_BROKER

async def on_broker_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, broker = q.data.split(":", 1)
    context.user_data["broker"] = broker

    u = update.effective_user
    if is_banned(u.id):
        return await q.message.reply_text("You are banned from using this bot.")

    # lock checks
    if user_has_approved(u.id, broker):
        if ONE_TIME_VERIFICATION:
            return await q.message.reply_text("You are already verified. Further requests are not allowed.")
        return await q.message.reply_text(f"You are already verified for {broker}. You cannot resubmit.")
    # pending?
    with db() as con:
        row = con.execute(
            "SELECT 1 FROM submissions WHERE tg_user_id=? AND broker=? AND status='pending'",
            (u.id, broker),
        ).fetchone()
    if row:
        return await q.message.reply_text(f"You already have a pending request for {broker}. Please wait.")

    await show_typing(context, q.message.chat_id, 0.4)
    await q.message.reply_text(
        f"You selected: {broker}\nPlease enter your Client ID / Account ID:",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ASK_CLIENT_ID

async def on_client_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_banned(update.effective_user.id):
        return await update.message.reply_text("You are banned from using this bot.")

    broker = context.user_data.get("broker")
    client_id = normalize_client_id(broker, (update.message.text or "").strip())
    if not client_id:
        examples = {
            "Exness": "e.g. 8–12 digits",
            "IC Markets": "e.g. 5–16 letters/numbers (_ or - allowed)",
            "FBS": "e.g. 6–12 digits",
        }
        return await update.message.reply_text(
            f"Invalid Client ID for {broker}. {examples.get(broker,'')}\nPlease enter again:"
        )

    # cooldown
    with db() as con:
        row = con.execute("SELECT last_submit_ts FROM users WHERE tg_user_id=?", (update.effective_user.id,)).fetchone()
    now = int(time.time())
    last_ts = int(row["last_submit_ts"]) if row else 0
    if now - last_ts < COOLDOWN_SECONDS:
        wait = COOLDOWN_SECONDS - (now - last_ts)
        return await update.message.reply_text(f"Please wait {wait} seconds before submitting again.")

    context.user_data["client_id"] = client_id
    await update.message.reply_text(
        "(Optional) Send your deposit screenshot now, or tap Skip.",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Skip")]], resize_keyboard=True, one_time_keyboard=True)
    )
    return ASK_SCREENSHOT

async def on_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_banned(update.effective_user.id):
        return await update.message.reply_text("You are banned from using this bot.")

    file_id = None
    if update.message.text and update.message.text.lower() == "skip":
        pass
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
    else:
        return await update.message.reply_text("Please send a photo or tap Skip.")

    broker = context.user_data.get("broker")
    client_id = context.user_data.get("client_id")
    user = update.effective_user

    # final double-check
    if user_has_approved(user.id, broker):
        if ONE_TIME_VERIFICATION:
            return await update.message.reply_text("You are already verified. Further requests are not allowed.")
        return await update.message.reply_text(f"You are already verified for {broker}.")

    with db() as con:
        con.execute(
            "INSERT INTO submissions (tg_user_id, broker, client_id, screenshot_file_id, status) VALUES (?,?,?,?, 'pending')",
            (user.id, broker, client_id, file_id),
        )
        con.execute("UPDATE users SET last_submit_ts=? WHERE tg_user_id=?", (int(time.time()), user.id))
        sub_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.commit()

    await update.message.reply_text(
        "Thanks! Your details are submitted for verification. Minimum $100 deposit is required for approval.\nYou'll be notified after admin review.",
        reply_markup=ReplyKeyboardRemove(),
    )

    admin_text = (
        "🔔 New verification request\n\n"
        f"Submission ID: {sub_id}\n"
        f"User: {user.full_name} (@{user.username or '—'}) [ID: {user.id}]\n"
        f"Broker: {broker}\n"
        f"Client ID: {client_id}\n"
        "Requirement: Min $100 deposit\n\nPlease review:"
    )
    await notify_admins(context, admin_text, approval_keyboard(sub_id))
    if file_id:
        for a in [x.strip() for x in os.getenv("ADMIN_IDS","").split(",") if x.strip()]:
            try:
                await context.bot.send_photo(int(a), file_id, caption=f"Submission #{sub_id} — Deposit screenshot")
            except Exception:
                pass

    return ConversationHandler.END

# ---------------- ADMIN ACTIONS ----------------
async def on_decide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(update.effective_user.id):
        return await q.edit_message_text("You are not authorized.")
    action, sid = q.data.split(":", 1)
    sid = int(sid)
    with db() as con:
        sub = con.execute("SELECT * FROM submissions WHERE id=?", (sid,)).fetchone()
        if not sub:
            return await q.edit_message_text("Submission not found.")
        if action == "approve":
            con.execute("UPDATE submissions SET status='approved', updated_at=CURRENT_TIMESTAMP WHERE id=?", (sid,))
            con.commit()
            await q.edit_message_text(f"✅ Approved submission #{sid}.")
            await send_vip_link(context, sub["tg_user_id"], sub["broker"])
        else:
            con.execute("UPDATE submissions SET status='rejected', updated_at=CURRENT_TIMESTAMP WHERE id=?", (sid,))
            con.commit()
            await q.edit_message_text(f"⛔ Rejected submission #{sid}.")
            try:
                await context.bot.send_message(sub["tg_user_id"], "Sorry, your verification was rejected. You may resubmit with correct details.")
            except Exception:
                pass

# ---------------- ADMIN COMMANDS ----------------
async def cmd_setgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("You are not authorized.")
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /setgroup <broker> <invite_link>")
    tokens = context.args
    link_i = None
    for i, t in enumerate(tokens):
        if t.startswith("http://") or t.startswith("https://") or "t.me" in t:
            link_i = i; break
    if link_i is None:
        return await update.message.reply_text("Provide a valid link.")
    broker = " ".join(tokens[:link_i]).strip()
    link = " ".join(tokens[link_i:]).strip()
    if broker not in BROKERS:
        return await update.message.reply_text(f"Unknown broker. Use one of: {', '.join(BROKERS)}")
    with db() as con:
        con.execute(
            "INSERT INTO vip_links (broker, invite_link) VALUES (?,?) "
            "ON CONFLICT(broker) DO UPDATE SET invite_link=excluded.invite_link",
            (broker, link)
        ); con.commit()
    await update.message.reply_text(f"VIP link updated for {broker}.")

async def cmd_brokers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("You are not authorized.")
    with db() as con:
        rows = con.execute("SELECT broker, COALESCE(invite_link,'(not set)') AS link FROM vip_links ORDER BY broker").fetchall()
    txt = "Current brokers & VIP links:\n\n" + "\n".join([f"• {r['broker']}: {r['link']}" for r in rows])
    await update.message.reply_text(txt)

async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("You are not authorized.")
    with db() as con:
        total = con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        last = con.execute("SELECT tg_user_id, username, first_name, last_name, joined_at FROM users ORDER BY joined_at DESC LIMIT 15").fetchall()
    lines = [f"{r['joined_at']} — {r['tg_user_id']} — @{r['username'] or '—'} — {r['first_name'] or ''} {r['last_name'] or ''}".strip() for r in last]
    await update.message.reply_text(f"👥 Total users: {total}\n\nLast 15:\n" + ("\n".join(lines) if lines else "(none)"))

async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("You are not authorized.")
    with db() as con:
        rows = con.execute("SELECT id, tg_user_id, broker, client_id, created_at FROM submissions WHERE status='pending' ORDER BY created_at DESC LIMIT 30").fetchall()
    if not rows:
        return await update.message.reply_text("No pending submissions.")
    lines = [f"#{r['id']} — {r['tg_user_id']} — {r['broker']} — {r['client_id']} — {r['created_at']}" for r in rows]
    await update.message.reply_text("Pending:\n" + "\n".join(lines))

async def cmd_find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("You are not authorized.")
    if not context.args:
        return await update.message.reply_text("Usage: /find <user_id|client_id>")
    key = " ".join(context.args).strip()
    with db() as con:
        rows = con.execute(
            "SELECT id, tg_user_id, broker, client_id, status, created_at FROM submissions "
            "WHERE tg_user_id = ? OR client_id = ? ORDER BY created_at DESC",
            (key, key)
        ).fetchall()
    if not rows:
        return await update.message.reply_text("No records found.")
    lines = [f"#{r['id']} — user {r['tg_user_id']} — {r['broker']} — {r['client_id']} — {r['status']} — {r['created_at']}" for r in rows]
    await update.message.reply_text("Search results:\n" + "\n".join(lines))

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("You are not authorized.")
    with db() as con:
        users = con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        approved = con.execute("SELECT COUNT(*) c FROM submissions WHERE status='approved'").fetchone()["c"]
        pending = con.execute("SELECT COUNT(*) c FROM submissions WHERE status='pending'").fetchone()["c"]
        rejected = con.execute("SELECT COUNT(*) c FROM submissions WHERE status='rejected'").fetchone()["c"]
        bans = con.execute("SELECT COUNT(*) c FROM bans").fetchone()["c"]
    await update.message.reply_text(
        f"📊 Stats\nUsers: {users}\nApproved: {approved}\nPending: {pending}\nRejected: {rejected}\nBanned: {bans}"
    )

# --- Ban / Unban ---
async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("You are not authorized.")
    if not context.args:
        return await update.message.reply_text("Usage: /ban <user_id> [reason]")
    uid = context.args[0]
    reason = " ".join(context.args[1:]).strip() or None
    with db() as con:
        con.execute("INSERT OR REPLACE INTO bans (tg_user_id, reason) VALUES (?, ?)", (uid, reason))
        con.commit()
    await update.message.reply_text(f"🚫 Banned user {uid}.")

async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("You are not authorized.")
    if not context.args:
        return await update.message.reply_text("Usage: /unban <user_id>")
    uid = context.args[0]
    with db() as con:
        con.execute("DELETE FROM bans WHERE tg_user_id=?", (uid,))
        con.commit()
    await update.message.reply_text(f"✅ Unbanned user {uid}.")

# --- Broadcast ---
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("You are not authorized.")
    if not context.args:
        return await update.message.reply_text("Usage: /broadcast <message>")
    message = " ".join(context.args).strip()
    await update.message.reply_text("Broadcast started…")
    sent = 0; failed = 0
    with db() as con:
        rows = con.execute("SELECT tg_user_id FROM users ORDER BY tg_user_id").fetchall()
    for r in rows:
        uid = r["tg_user_id"]
        if is_banned(uid):  # skip banned
            continue
        try:
            await context.bot.send_message(uid, message)
            sent += 1
            await asyncio.sleep(0.03)  # be gentle to Telegram
        except Exception:
            failed += 1
    await update.message.reply_text(f"📢 Broadcast finished. Sent: {sent} | Failed: {failed}")

# --- Excel Export ---
async def cmd_exportxlsx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("You are not authorized.")
    xlsx_path = "export_maharaja.xlsx"
    wb = Workbook()
    ws_users = wb.active
    ws_users.title = "users"
    ws_users.append(["tg_user_id", "username", "first_name", "last_name", "joined_at", "last_submit_ts"])

    ws_sub = wb.create_sheet("submissions")
    ws_sub.append(["id", "tg_user_id", "broker", "client_id", "screenshot_file_id", "status", "created_at", "updated_at"])

    with db() as con:
        for r in con.execute("SELECT tg_user_id, username, first_name, last_name, joined_at, last_submit_ts FROM users ORDER BY joined_at"):
            ws_users.append([r["tg_user_id"], r["username"], r["first_name"], r["last_name"], r["joined_at"], r["last_submit_ts"]])
        for r in con.execute("SELECT id, tg_user_id, broker, client_id, screenshot_file_id, status, created_at, updated_at FROM submissions ORDER BY created_at DESC"):
            ws_sub.append([r["id"], r["tg_user_id"], r["broker"], r["client_id"], r["screenshot_file_id"], r["status"], r["created_at"], r["updated_at"]])

    wb.save(xlsx_path)
try:
    # Proper filename + MIME hint so Telegram shows it as a real Excel file
    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=InputFile(xlsx_path, filename="export_maharaja.xlsx"),
        filename="export_maharaja.xlsx",
    )
except Exception as e:
    return await update.message.reply_text(f"Export failed: {e}")
await update.message.reply_text("Excel export sent ✅")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ---------------- BOOT ----------------
def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN not set")

    init_db()
    app = Application.builder().token(token).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start),
                      MessageHandler(filters.Regex(re.compile(r"^(hi|hello)$", re.IGNORECASE)), start)],
        states={
            CHOOSE_BROKER: [CallbackQueryHandler(on_broker_choice, pattern=r"^broker:")],
            ASK_CLIENT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_client_id)],
            ASK_SCREENSHOT: [MessageHandler(filters.ALL, on_screenshot)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="main_conv",
        persistent=False,
    )
    app.add_handler(conv)

    # Admin commands
    app.add_handler(CallbackQueryHandler(on_decide, pattern=r"^(approve|reject):"))
    app.add_handler(CommandHandler("setgroup", cmd_setgroup))
    app.add_handler(CommandHandler("brokers", cmd_brokers))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("find", cmd_find))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("exportxlsx", cmd_exportxlsx))

    print("Bot maharaja007 is running…")
    app.run_polling()

if __name__ == "__main__":
    main()

