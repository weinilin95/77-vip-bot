import os
import asyncio
import threading

from flask import Flask, request
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
PUBLIC_CHANNEL_URL = os.environ["PUBLIC_CHANNEL_URL"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"]

telegram_app = Application.builder().token(BOT_TOKEN).build()
flask_app = Flask(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🔥 申請加入 VIP", callback_data="apply_vip")],
        [InlineKeyboardButton("📢 返回 77限定", url=PUBLIC_CHANNEL_URL)],
    ]

    await update.message.reply_text(
        "✨ 歡迎來到 77VIP\n\n"
        "這裡是 77限定的 VIP 專屬入口 🔐\n"
        "請選擇下方功能 👇",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "apply_vip":
        await query.edit_message_text(
            "🔐 VIP 加入申請\n\n"
            "你的申請流程已開始。\n"
            "請等待管理員進行審核。"
        )

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CallbackQueryHandler(button_handler))

loop = asyncio.new_event_loop()

def run_loop():
    asyncio.set_event_loop(loop)
    loop.run_forever()

threading.Thread(target=run_loop, daemon=True).start()

asyncio.run_coroutine_threadsafe(
    telegram_app.initialize(), loop
).result()

asyncio.run_coroutine_threadsafe(
    telegram_app.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook"), loop
).result()

@flask_app.route("/")
def home():
    return "77 VIP Bot is running!"

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), telegram_app.bot)

    future = asyncio.run_coroutine_threadsafe(
        telegram_app.process_update(update), loop
    )
    future.result()

    return "OK"
