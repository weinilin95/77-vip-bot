import os
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route


BOT_TOKEN = os.environ["BOT_TOKEN"]
PUBLIC_CHANNEL_URL = os.environ["PUBLIC_CHANNEL_URL"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"].rstrip("/")
ADMIN_ID = int(os.environ["ADMIN_ID"])
VIP_CHANNEL_ID = int(os.environ["VIP_CHANNEL_ID"])

telegram_app = Application.builder().token(BOT_TOKEN).build()


# =========================
# /start
# =========================
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


# =========================
# /myid
# =========================
async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    await update.message.reply_text(
        f"🆔 你的 Telegram ID：\n\n{user.id}"
    )


# =========================
# VIP 申請
# =========================
async def apply_vip(query, context):
    user = query.from_user

    # 先檢查是否已經是 VIP
    try:
        member = await context.bot.get_chat_member(
            chat_id=VIP_CHANNEL_ID,
            user_id=user.id
        )

        if member.status in [
            "member",
            "administrator",
            "creator"
        ]:
            await query.edit_message_text(
                "✨ 你已經是 77VIP 會員囉！\n\n"
                "不需要再次申請 🔐"
            )
            return

    except Exception:
        pass

    username = (
        f"@{user.username}"
        if user.username
        else "未設定"
    )

    admin_keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ 批准",
                callback_data=f"approve:{user.id}"
            ),
            InlineKeyboardButton(
                "❌ 拒絕",
                callback_data=f"reject:{user.id}"
            )
        ]
    ])

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            "🔥 新的 VIP 加入申請\n\n"
            f"👤 名稱：{user.full_name}\n"
            f"🔗 Username：{username}\n"
            f"🆔 User ID：{user.id}\n\n"
            "請選擇是否批准："
        ),
        reply_markup=admin_keyboard
    )

    await query.edit_message_text(
        "✅ VIP 申請已送出\n\n"
        "管理員收到你的申請了。\n"
        "審核完成後，Bot 會直接通知你 🔔"
    )


# =========================
# 管理員批准
# =========================
async def approve_vip(query, context, user_id):
    if query.from_user.id != ADMIN_ID:
        return

    try:
        expire_time = (
            datetime.now(timezone.utc)
            + timedelta(minutes=30)
        )

        invite = await context.bot.create_chat_invite_link(
            chat_id=VIP_CHANNEL_ID,
            expire_date=expire_time,
            name=f"VIP-{user_id}"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔐 進入 77VIP",
                    url=invite.invite_link
                )
            ]
        ])

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "🎉 你的 VIP 申請已通過！\n\n"
                "下方是你的專屬加入連結 🔐\n\n"
                "⚠️ 此專屬連結將於 30 分鐘後失效。\n"
                "成功加入後，此邀請連結會立即失效。"
            ),
            reply_markup=keyboard
        )

        await query.edit_message_text(
            query.message.text
            + "\n\n"
            + "✅ 已批准\n"
            + "專屬邀請連結已傳送給會員。"
        )

    except Exception as e:
        print(f"Approve error: {e}")

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"❌ 建立 VIP 邀請連結失敗：\n{e}"
        )


# =========================
# 管理員拒絕
# =========================
async def reject_vip(query, context, user_id):
    if query.from_user.id != ADMIN_ID:
        return

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "❌ 你的 VIP 申請目前未通過。\n\n"
                "如有疑問，請聯絡管理員。"
            )
        )

        await query.edit_message_text(
            query.message.text
            + "\n\n"
            + "❌ 已拒絕"
        )

    except Exception as e:
        print(f"Reject error: {e}")


# =========================
# VIP 加入成功
# 自動撤銷專屬邀請連結
# =========================
async def vip_member_update(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    chat_member_update = update.chat_member

    if not chat_member_update:
        return

    # 只處理 77VIP 頻道
    if chat_member_update.chat.id != VIP_CHANNEL_ID:
        return

    new_member = chat_member_update.new_chat_member

    # 確認會員真的已經加入
    if new_member.status not in [
        "member",
        "administrator",
        "creator"
    ]:
        return

    user = new_member.user

    # Telegram 會告訴 Bot 這次加入使用的是哪條邀請連結
    invite_link = chat_member_update.invite_link

    if not invite_link:
        return

    # 只處理由我們 Bot 建立的 VIP 專屬連結
    expected_name = f"VIP-{user.id}"

    if invite_link.name != expected_name:
        return

    try:
        # 成功加入後立即撤銷連結
        await context.bot.revoke_chat_invite_link(
            chat_id=VIP_CHANNEL_ID,
            invite_link=invite_link.invite_link
        )

        print(
            f"VIP joined: {user.id} "
            f"- invite link revoked"
        )

        # 通知會員
        try:
            await context.bot.send_message(
                chat_id=user.id,
                text=(
                    "✅ 已成功加入 77VIP！\n\n"
                    "🔐 你的專屬邀請連結已自動失效。"
                )
            )
        except Exception as e:
            print(f"Member notification error: {e}")

        # 通知管理員
        try:
            username = (
                f"@{user.username}"
                if user.username
                else "未設定"
            )

            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "✅ VIP 會員已成功加入\n\n"
                    f"👤 名稱：{user.full_name}\n"
                    f"🔗 Username：{username}\n"
                    f"🆔 User ID：{user.id}\n\n"
                    "🔒 專屬邀請連結已自動撤銷。"
                )
            )

        except Exception as e:
            print(f"Admin notification error: {e}")

    except Exception as e:
        print(f"Revoke invite error: {e}")

        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "⚠️ 會員已加入，但邀請連結自動撤銷失敗。\n\n"
                    f"User ID：{user.id}\n"
                    f"錯誤：{e}"
                )
            )
        except Exception:
            pass


# =========================
# Inline Button
# =========================
async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "apply_vip":
        await apply_vip(query, context)
        return

    if data.startswith("approve:"):
        user_id = int(data.split(":")[1])

        await approve_vip(
            query,
            context,
            user_id
        )
        return

    if data.startswith("reject:"):
        user_id = int(data.split(":")[1])

        await reject_vip(
            query,
            context,
            user_id
        )
        return


# =========================
# Telegram handlers
# =========================
telegram_app.add_handler(
    CommandHandler("start", start)
)

telegram_app.add_handler(
    CommandHandler("myid", myid)
)

telegram_app.add_handler(
    CallbackQueryHandler(button_handler)
)

# 偵測會員加入/退出 VIP
telegram_app.add_handler(
    ChatMemberHandler(
        vip_member_update,
        ChatMemberHandler.CHAT_MEMBER
    )
)


# =========================
# Render 首頁
# =========================
async def homepage(request: Request):
    return PlainTextResponse(
        "77 VIP Bot V2.1 is running!"
    )


# =========================
# Telegram Webhook
# =========================
async def webhook(request: Request):
    data = await request.json()

    update = Update.de_json(
        data=data,
        bot=telegram_app.bot
    )

    await telegram_app.process_update(update)

    return PlainTextResponse("OK")


# =========================
# 啟動
# =========================
async def startup():
    await telegram_app.initialize()

    await telegram_app.bot.set_webhook(
        url=f"{WEBHOOK_URL}/webhook",
        allowed_updates=[
            "message",
            "callback_query",
            "chat_member",
        ]
    )


# =========================
# 關閉
# =========================
async def shutdown():
    await telegram_app.shutdown()


app = Starlette(
    routes=[
        Route(
            "/",
            homepage,
            methods=["GET", "HEAD"]
        ),
        Route(
            "/webhook",
            webhook,
            methods=["POST"]
        ),
    ],
    on_startup=[startup],
    on_shutdown=[shutdown],
)
