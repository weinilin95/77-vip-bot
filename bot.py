import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
PUBLIC_CHANNEL_URL = os.environ["PUBLIC_CHANNEL_URL"]

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

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("77 VIP Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
