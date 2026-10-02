import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route


BOT_TOKEN = os.environ["BOT_TOKEN"]
PUBLIC_CHANNEL_URL = os.environ["PUBLIC_CHANNEL_URL"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"].rstrip("/")
ADMIN_ID = int(os.environ["ADMIN_ID"])


telegram_app = Application.builder().token(BOT_TOKEN).build()


# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton(
                "🔥 申請加入 VIP",
                callback_data="apply_vip"
            )
        ],
        [
            InlineKeyboardButton(
                "📢 返回 77限定",
                url=PUBLIC_CHANNEL_URL
            )
        ],
    ]

    await update.message.reply_text(
        "✨ 歡迎來到 77VIP\n\n"
        "這裡是 77限定的 VIP 專屬入口 🔐\n"
        "請選擇下方功能 👇",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# /myid
async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    await update.message.reply_text(
        f"🆔 你的 Telegram ID：\n\n{user.id}"
    )


# 偵測頻道貼文，將 Channel ID 私訊給管理員
async def channel_post_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    channel_post = update.channel_post

    if not channel_post:
        return

    chat = channel_post.chat

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            "📡 偵測到頻道\n\n"
            f"名稱：{chat.title}\n"
            f"Channel ID：\n`{chat.id}`"
        ),
        parse_mode="Markdown"
    )


# 按鈕
async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    if query.data == "apply_vip":
        await query.edit_message_text(
            "🔐 VIP 加入申請\n\n"
            "你的申請流程已開始。\n"
            "請等待管理員進行審核。"
        )


telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("myid", myid))

telegram_app.add_handler(
    MessageHandler(
        filters.UpdateType.CHANNEL_POST,
        channel_post_handler
    )
)

telegram_app.add_handler(
    CallbackQueryHandler(button_handler)
)


async def homepage(request: Request):
    return PlainTextResponse("77 VIP Bot is running!")


async def webhook(request: Request):
    data = await request.json()

    update = Update.de_json(
        data=data,
        bot=telegram_app.bot
    )

    await telegram_app.process_update(update)

    return PlainTextResponse("OK")


async def startup():
    await telegram_app.initialize()

    await telegram_app.bot.set_webhook(
        url=f"{WEBHOOK_URL}/webhook",
        allowed_updates=[
            "message",
            "callback_query",
            "channel_post",
        ]
    )


async def shutdown():
    await telegram_app.shutdown()


app = Starlette(
    routes=[
        Route("/", homepage, methods=["GET", "HEAD"]),
        Route("/webhook", webhook, methods=["POST"]),
    ],
    on_startup=[startup],
    on_shutdown=[shutdown],
)
