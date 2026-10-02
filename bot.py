import os
import io
import secrets
from decimal import Decimal
from datetime import datetime, timedelta, timezone

import httpx
import qrcode

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)

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


# =========================================================
# ENV
# =========================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
PUBLIC_CHANNEL_URL = os.environ["PUBLIC_CHANNEL_URL"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"].rstrip("/")

ADMIN_ID = int(os.environ["ADMIN_ID"])
VIP_CHANNEL_ID = int(os.environ["VIP_CHANNEL_ID"])

VIP_PRICE_USDT = Decimal(
    os.environ.get("VIP_PRICE_USDT", "10")
)

USDT_TRC20_ADDRESS = os.environ["USDT_TRC20_ADDRESS"]


telegram_app = (
    Application.builder()
    .token(BOT_TOKEN)
    .build()
)


# =========================================================
# 工具：訂單編號
# =========================================================

def create_order_id():
    now = datetime.now(timezone.utc)

    date_text = now.strftime("%Y%m%d")

    random_text = secrets.token_hex(3).upper()

    return f"VIP{date_text}{random_text}"


# =========================================================
# 工具：USDT QR Code
# =========================================================

def create_usdt_qr(address):
    qr = qrcode.QRCode(
        version=1,
        box_size=10,
        border=3
    )

    qr.add_data(address)
    qr.make(fit=True)

    image = qr.make_image(
        fill_color="black",
        back_color="white"
    )

    output = io.BytesIO()

    image.save(
        output,
        format="PNG"
    )

    output.seek(0)
    output.name = "usdt_trc20.png"

    return output


# =========================================================
# 工具：取得 USDT/TWD 即時匯率
# CoinGecko，自動重試 3 次
# =========================================================

async def get_usdt_twd_rate():
    """
    取得 1 USDT 約等於多少 TWD

    優先：
    1. CoinGecko USDT/TWD
    2. Coinbase USDT/USD × Frankfurter USD/TWD

    匯率僅供顯示，
    實際付款固定 10 USDT。
    """

    timeout = httpx.Timeout(10.0)

    # =====================================================
    # 1. CoinGecko
    # =====================================================

    coingecko_url = (
        "https://api.coingecko.com/api/v3/simple/price"
    )

    coingecko_params = {
        "ids": "tether",
        "vs_currencies": "twd"
    }

    for attempt in range(2):
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                headers={
                    "User-Agent": "77VIPBot/1.0"
                }
            ) as client:

                response = await client.get(
                    coingecko_url,
                    params=coingecko_params
                )

                response.raise_for_status()

                data = response.json()

            rate = (
                data.get("tether", {})
                .get("twd")
            )

            if rate is None:
                raise ValueError(
                    "CoinGecko 沒有回傳 TWD"
                )

            rate = Decimal(str(rate))

            if rate <= 0:
                raise ValueError(
                    "CoinGecko 匯率異常"
                )

            print(
                "Rate source: CoinGecko | "
                f"1 USDT = {rate} TWD"
            )

            return rate

        except Exception as e:
            print(
                f"CoinGecko attempt "
                f"{attempt + 1} failed: {e}"
            )


    # =====================================================
    # 2. 備援
    # Coinbase USDT/USD
    # ×
    # Frankfurter USD/TWD
    # =====================================================

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            headers={
                "User-Agent": "77VIPBot/1.0"
            }
        ) as client:

            # ---------------------------------------------
            # USDT -> USD
            # ---------------------------------------------

            coinbase_response = await client.get(
                "https://api.coinbase.com/v2/exchange-rates",
                params={
                    "currency": "USDT"
                }
            )

            coinbase_response.raise_for_status()

            coinbase_data = (
                coinbase_response.json()
            )

            usdt_usd = Decimal(
                str(
                    coinbase_data[
                        "data"
                    ][
                        "rates"
                    ][
                        "USD"
                    ]
                )
            )


            # ---------------------------------------------
            # USD -> TWD
            # ---------------------------------------------

            fx_response = await client.get(
                "https://api.frankfurter.app/latest",
                params={
                    "from": "USD",
                    "to": "TWD"
                }
            )

            fx_response.raise_for_status()

            fx_data = (
                fx_response.json()
            )

            usd_twd = Decimal(
                str(
                    fx_data[
                        "rates"
                    ][
                        "TWD"
                    ]
                )
            )


        # ---------------------------------------------
        # USDT/TWD
        # ---------------------------------------------

        rate = (
            usdt_usd
            * usd_twd
        ).quantize(
            Decimal("0.01")
        )

        if rate <= 0:
            raise ValueError(
                "備援匯率異常"
            )

        print(
            "Rate source: Backup | "
            f"USDT/USD={usdt_usd} | "
            f"USD/TWD={usd_twd} | "
            f"1 USDT={rate} TWD"
        )

        return rate


    except Exception as e:
        print(
            f"Backup rate failed: {e}"
        )


    # =====================================================
    # 所有來源都失敗
    # =====================================================

    return None


# =========================================================
# /start
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    keyboard = [
        [
            InlineKeyboardButton(
                "🔥 購買永久 VIP",
                callback_data="buy_vip"
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
        "🔐 77VIP 專屬入口\n\n"
        f"🔥 永久 VIP｜{VIP_PRICE_USDT} USDT\n\n"
        "💵 USDT（TRC20）付款\n\n"
        "請選擇下方功能 👇",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================================================
# /myid
# =========================================================

async def myid(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user

    await update.message.reply_text(
        "🆔 你的 Telegram ID：\n\n"
        f"{user.id}"
    )


# =========================================================
# 購買 VIP
# =========================================================

async def buy_vip(
    query,
    context
):
    user = query.from_user

    # 已經是 VIP
    try:
        member = (
            await context.bot.get_chat_member(
                chat_id=VIP_CHANNEL_ID,
                user_id=user.id
            )
        )

        if member.status in [
            "member",
            "administrator",
            "creator"
        ]:
            await query.edit_message_text(
                "✨ 你已經是 77VIP 會員囉！\n\n"
                "不需要再次購買 🔐"
            )

            return

    except Exception:
        pass

    # 只有 USDT，所以直接建立付款訂單
    await pay_usdt(
        query,
        context
    )


# =========================================================
# 建立 USDT 訂單
# =========================================================

async def pay_usdt(
    query,
    context
):
    user = query.from_user

    try:
        await query.edit_message_text(
            "⏳ 正在取得 USDT / TWD 即時匯率..."
        )

    except Exception:
        pass

    # 固定收 10 USDT
    usdt_amount = VIP_PRICE_USDT

    # 匯率只供顯示
    rate = await get_usdt_twd_rate()

    if rate is not None:
        twd_value = (
            usdt_amount
            * rate
        ).quantize(
            Decimal("0.01")
        )

    else:
        twd_value = None

    order_id = create_order_id()

    expire_time = (
        datetime.now(timezone.utc)
        + timedelta(minutes=30)
    )

    # 暫存訂單
    context.user_data["payment"] = {
        "order_id": order_id,
        "amount": str(usdt_amount),
        "rate": (
            str(rate)
            if rate is not None
            else "N/A"
        ),
        "twd_value": (
            str(twd_value)
            if twd_value is not None
            else "N/A"
        ),
        "expire_timestamp": (
            expire_time.timestamp()
        )
    }

    qr_image = create_usdt_qr(
        USDT_TRC20_ADDRESS
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ 我已支付",
                callback_data="payment_done"
            )
        ],
        [
            InlineKeyboardButton(
                "❌ 取消支付",
                callback_data="cancel_payment"
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 首頁",
                callback_data="home"
            )
        ]
    ])

    if rate is not None:
        rate_text = (
            f"💱 即時匯率："
            f"1 USDT ≈ NT${rate}\n"
            f"🇹🇼 約等值："
            f"NT${twd_value}\n"
        )

    else:
        rate_text = (
            "💱 即時匯率："
            "暫時無法取得\n"
        )

    text = (
        "💵 USDT 付款\n\n"

        f"📄 訂單號：{order_id}\n"

        f"💰 支付金額："
        f"{usdt_amount} USDT\n"

        f"{rate_text}\n"

        "📥 收款地址（TRC20）：\n"
        f"{USDT_TRC20_ADDRESS}\n\n"

        "━━━━━━━━━━━━━━\n"

        "⏰ 請在 30 分鐘內完成轉帳\n\n"

        f"✅ 實際應付金額固定為 "
        f"{usdt_amount} USDT\n"

        "ℹ️ 台幣換算僅供參考\n"

        "⚠️ 請務必使用 TRC20 網路\n"

        "⚠️ 請再次確認收款地址\n"

        "⚠️ 交易所手續費由付款方承擔\n\n"

        "完成轉帳後請按：\n"

        "✅ 我已支付"
    )

    await context.bot.send_photo(
        chat_id=user.id,
        photo=qr_image,
        caption=text,
        reply_markup=keyboard
    )


# =========================================================
# 使用者按「我已支付」
# =========================================================

async def payment_done(
    query,
    context
):
    user = query.from_user

    payment = (
        context.user_data.get(
            "payment"
        )
    )

    if not payment:

        await query.answer(
            "找不到有效訂單，請重新建立。",
            show_alert=True
        )

        return

    now = datetime.now(
        timezone.utc
    ).timestamp()

    if now > payment["expire_timestamp"]:

        await query.answer(
            "此訂單已超過 30 分鐘，"
            "請重新建立訂單。",
            show_alert=True
        )

        return

    order_id = payment["order_id"]
    amount = payment["amount"]
    rate = payment["rate"]
    twd_value = payment["twd_value"]

    username = (
        f"@{user.username}"
        if user.username
        else "未設定"
    )

    admin_keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ 確認收款",
                callback_data=(
                    f"paidok:"
                    f"{user.id}:"
                    f"{order_id}"
                )
            )
        ],
        [
            InlineKeyboardButton(
                "❌ 未收到款",
                callback_data=(
                    f"paidno:"
                    f"{user.id}:"
                    f"{order_id}"
                )
            )
        ]
    ])

    admin_text = (
        "💰 新的 VIP 付款確認\n\n"

        f"📄 訂單：{order_id}\n"

        f"💵 應付金額："
        f"{amount} USDT\n"
    )

    if rate != "N/A":
        admin_text += (
            f"💱 建單匯率："
            f"1 USDT ≈ NT${rate}\n"

            f"🇹🇼 約等值："
            f"NT${twd_value}\n"
        )

    admin_text += (
        "\n"

        f"👤 名稱："
        f"{user.full_name}\n"

        f"🔗 Username："
        f"{username}\n"

        f"🆔 User ID："
        f"{user.id}\n\n"

        "⚠️ 請先確認自己的錢包／"
        "交易紀錄。\n"

        f"確認實際收到 "
        f"{amount} USDT "
        "後再批准。"
    )

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=admin_text,
        reply_markup=admin_keyboard
    )

    await query.edit_message_caption(
        caption=(
            "✅ 已提交付款確認\n\n"

            f"📄 訂單："
            f"{order_id}\n"

            f"💵 金額："
            f"{amount} USDT\n\n"

            "正在等待管理員確認收款。\n"

            "確認完成後 Bot 會通知你 🔔"
        )
    )


# =========================================================
# 管理員確認收款
# =========================================================

async def payment_approve(
    query,
    context,
    user_id,
    order_id
):
    if query.from_user.id != ADMIN_ID:

        await query.answer(
            "你沒有管理員權限。",
            show_alert=True
        )

        return

    try:

        # 曾被移除 / 封鎖
        # 批准前自動解除
        try:

            await context.bot.unban_chat_member(
                chat_id=VIP_CHANNEL_ID,
                user_id=user_id,
                only_if_banned=True
            )

        except Exception as e:

            print(
                f"Unban error: {e}"
            )

        # VIP 邀請 30 分鐘有效
        expire_time = (
            datetime.now(timezone.utc)
            + timedelta(minutes=30)
        )

        invite = (
            await context.bot
            .create_chat_invite_link(
                chat_id=VIP_CHANNEL_ID,
                expire_date=expire_time,
                name=f"VIP-{user_id}"
            )
        )

        vip_keyboard = InlineKeyboardMarkup([
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
                "🎉 付款確認完成！\n\n"

                f"📄 訂單："
                f"{order_id}\n"

                "✅ VIP 已開通\n\n"

                "下方是你的專屬加入連結 🔐\n\n"

                "⚠️ 邀請連結 "
                "30 分鐘後失效。\n"

                "成功加入後，"
                "邀請連結會立即撤銷。"
            ),
            reply_markup=vip_keyboard
        )

        await query.edit_message_text(
            query.message.text
            + "\n\n"
            + "✅ 已確認收款\n"
            + "✅ VIP 邀請已傳送"
        )

    except Exception as e:

        print(
            f"Payment approve error: {e}"
        )

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "❌ VIP 開通失敗\n\n"

                f"訂單："
                f"{order_id}\n"

                f"User ID："
                f"{user_id}\n"

                f"錯誤："
                f"{e}"
            )
        )


# =========================================================
# 管理員：未收到款
# =========================================================

async def payment_reject(
    query,
    context,
    user_id,
    order_id
):
    if query.from_user.id != ADMIN_ID:

        await query.answer(
            "你沒有管理員權限。",
            show_alert=True
        )

        return

    try:

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "⚠️ 目前尚未確認收到款項。\n\n"

                f"📄 訂單："
                f"{order_id}\n\n"

                "請再次確認：\n"

                "• 是否使用 TRC20 網路\n"

                "• 收款地址是否正確\n"

                f"• 是否實際轉出 "
                f"{VIP_PRICE_USDT} USDT\n"

                "• 交易是否已完成\n\n"

                "如已付款，可稍後再聯絡"
                "管理員確認。"
            )
        )

        await query.edit_message_text(
            query.message.text
            + "\n\n"
            + "❌ 尚未確認收到款"
        )

    except Exception as e:

        print(
            f"Payment reject error: {e}"
        )


# =========================================================
# 取消訂單
# =========================================================

async def cancel_payment(
    query,
    context
):
    context.user_data.pop(
        "payment",
        None
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔥 重新購買 VIP",
                callback_data="buy_vip"
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 首頁",
                callback_data="home"
            )
        ]
    ])

    try:

        await query.edit_message_caption(
            caption="❌ 訂單已取消。",
            reply_markup=keyboard
        )

    except Exception:

        await query.edit_message_text(
            "❌ 訂單已取消。",
            reply_markup=keyboard
        )


# =========================================================
# VIP 加入成功
# 自動撤銷邀請連結
# =========================================================

async def vip_member_update(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    member_update = update.chat_member

    if not member_update:
        return

    if member_update.chat.id != VIP_CHANNEL_ID:
        return

    new_member = (
        member_update.new_chat_member
    )

    if new_member.status not in [
        "member",
        "administrator",
        "creator"
    ]:
        return

    user = new_member.user

    invite_link = (
        member_update.invite_link
    )

    if not invite_link:
        return

    expected_name = (
        f"VIP-{user.id}"
    )

    if invite_link.name != expected_name:
        return

    try:

        await context.bot.revoke_chat_invite_link(
            chat_id=VIP_CHANNEL_ID,
            invite_link=(
                invite_link.invite_link
            )
        )

        await context.bot.send_message(
            chat_id=user.id,
            text=(
                "✅ 已成功加入 77VIP！\n\n"

                "🔒 你的專屬邀請連結"
                "已自動失效。"
            )
        )

        username = (
            f"@{user.username}"
            if user.username
            else "未設定"
        )

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "✅ VIP 會員已成功加入\n\n"

                f"👤 名稱："
                f"{user.full_name}\n"

                f"🔗 Username："
                f"{username}\n"

                f"🆔 User ID："
                f"{user.id}\n\n"

                "🔒 專屬邀請連結已撤銷。"
            )
        )

    except Exception as e:

        print(
            f"Revoke invite error: {e}"
        )


# =========================================================
# 首頁
# =========================================================

async def show_home(
    query,
    context
):
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔥 購買永久 VIP",
                callback_data="buy_vip"
            )
        ],
        [
            InlineKeyboardButton(
                "📢 返回 77限定",
                url=PUBLIC_CHANNEL_URL
            )
        ]
    ])

    home_text = (
        "✨ 歡迎來到 77VIP\n\n"

        "🔐 77VIP 專屬入口\n\n"

        f"🔥 永久 VIP｜"
        f"{VIP_PRICE_USDT} USDT\n\n"

        "💵 USDT（TRC20）付款"
    )

    try:

        await query.edit_message_caption(
            caption=home_text,
            reply_markup=keyboard
        )

    except Exception:

        await query.edit_message_text(
            home_text,
            reply_markup=keyboard
        )


# =========================================================
# Button Router
# =========================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    await query.answer()

    data = query.data

    if data == "buy_vip":

        await buy_vip(
            query,
            context
        )

        return

    if data == "payment_done":

        await payment_done(
            query,
            context
        )

        return

    if data == "cancel_payment":

        await cancel_payment(
            query,
            context
        )

        return

    if data == "home":

        await show_home(
            query,
            context
        )

        return

    if data.startswith("paidok:"):

        parts = data.split(":")

        user_id = int(parts[1])
        order_id = parts[2]

        await payment_approve(
            query,
            context,
            user_id,
            order_id
        )

        return

    if data.startswith("paidno:"):

        parts = data.split(":")

        user_id = int(parts[1])
        order_id = parts[2]

        await payment_reject(
            query,
            context,
            user_id,
            order_id
        )

        return


# =========================================================
# Telegram Handlers
# =========================================================

telegram_app.add_handler(
    CommandHandler(
        "start",
        start
    )
)

telegram_app.add_handler(
    CommandHandler(
        "myid",
        myid
    )
)

telegram_app.add_handler(
    CallbackQueryHandler(
        button_handler
    )
)

telegram_app.add_handler(
    ChatMemberHandler(
        vip_member_update,
        ChatMemberHandler.CHAT_MEMBER
    )
)


# =========================================================
# Render 首頁
# =========================================================

async def homepage(
    request: Request
):
    return PlainTextResponse(
        "77 VIP Bot FINAL is running!"
    )


# =========================================================
# Telegram Webhook
# =========================================================

async def webhook(
    request: Request
):
    data = await request.json()

    update = Update.de_json(
        data=data,
        bot=telegram_app.bot
    )

    await telegram_app.process_update(
        update
    )

    return PlainTextResponse(
        "OK"
    )


# =========================================================
# 啟動
# =========================================================

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


# =========================================================
# 關閉
# =========================================================

async def shutdown():

    await telegram_app.shutdown()


# =========================================================
# Starlette App
# =========================================================

app = Starlette(
    routes=[
        Route(
            "/",
            homepage,
            methods=[
                "GET",
                "HEAD"
            ]
        ),

        Route(
            "/webhook",
            webhook,
            methods=[
                "POST"
            ]
        ),
    ],
    on_startup=[
        startup
    ],
    on_shutdown=[
        shutdown
    ],
)
