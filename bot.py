import os
import io
import secrets
import asyncio
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import qrcode

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from sqlalchemy import (
    BigInteger,
    DateTime,
    Numeric,
    String,
    Text,
    select,
    update,
)
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
)


# =========================================================
# ENV
# =========================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
PUBLIC_CHANNEL_URL = os.environ["PUBLIC_CHANNEL_URL"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"].rstrip("/")

ADMIN_ID = int(os.environ["ADMIN_ID"])
VIP_CHANNEL_ID = int(os.environ["VIP_CHANNEL_ID"])

VIP_PRICE_USDT = Decimal(os.environ.get("VIP_PRICE_USDT", "10"))
USDT_TRC20_ADDRESS = os.environ["USDT_TRC20_ADDRESS"]

# Render 正式環境請設定 DATABASE_URL 為 Render PostgreSQL Internal Database URL。
# 未設定時只作為本機測試，使用 SQLite。
RAW_DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./vipbot.db",
)

if RAW_DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = (
        "postgresql+asyncpg://"
        + RAW_DATABASE_URL[len("postgres://"):]
    )
elif RAW_DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = (
        "postgresql+asyncpg://"
        + RAW_DATABASE_URL[len("postgresql://"):]
    )
else:
    DATABASE_URL = RAW_DATABASE_URL


# =========================================================
# DATABASE
# =========================================================

class Base(AsyncAttrs, DeclarativeBase):
    pass


class Order(Base):
    __tablename__ = "orders"

    order_id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        index=True,
        nullable=False,
    )

    username: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )

    full_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    amount_usdt: Mapped[Decimal] = mapped_column(
        Numeric(20, 8),
        nullable=False,
    )

    rate_twd: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 8),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        index=True,
        nullable=False,
        default="pending",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    claimed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    rejected_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    cancelled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    joined_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    invite_link: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    invite_revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
)


# =========================================================
# STATUS
# =========================================================

STATUS_LABELS = {
    "pending": "🕒 待付款",
    "awaiting_review": "🔎 待管理員確認",
    "processing": "⚙️ 開通處理中",
    "approved": "✅ 已確認收款",
    "joined": "🌟 已加入 VIP",
    "rejected": "❌ 未收到款",
    "cancelled": "🚫 已取消",
    "expired": "⏰ 已逾期",
}


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


# =========================================================
# HELPERS
# =========================================================

TAIWAN_TZ = timezone(timedelta(hours=8))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def fmt_time(value: Optional[datetime]) -> str:
    if value is None:
        return "-"
    return value.astimezone(TAIWAN_TZ).strftime("%Y-%m-%d %H:%M")


def create_order_id() -> str:
    now = now_utc()
    date_text = now.strftime("%Y%m%d")
    random_text = secrets.token_hex(3).upper()
    return f"VIP{date_text}{random_text}"


def create_usdt_qr(address: str):
    qr = qrcode.QRCode(
        version=1,
        box_size=10,
        border=3,
    )
    qr.add_data(address)
    qr.make(fit=True)

    image = qr.make_image(
        fill_color="black",
        back_color="white",
    )

    output = io.BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    output.name = "usdt_trc20.png"
    return output


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["🏠 首頁", "📋 我的訂單"],
            ["🌟 我的購買", "🎟 我的優惠"],
            ["💰 邀請返利"],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def home_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔥 購買永久 VIP",
                    callback_data="buy_vip",
                )
            ],
            [
                InlineKeyboardButton(
                    "📢 返回 77限定",
                    url=PUBLIC_CHANNEL_URL,
                )
            ],
        ]
    )


async def expire_due_orders(user_id: Optional[int] = None):
    """
    將過期、尚未送審的 pending 訂單改成 expired。
    awaiting_review 不自動過期，避免會員已付款卻因管理員晚處理而失效。
    """
    async with SessionLocal() as session:
        stmt = (
            update(Order)
            .where(
                Order.status == "pending",
                Order.expires_at < now_utc(),
            )
            .values(status="expired")
        )

        if user_id is not None:
            stmt = stmt.where(Order.user_id == user_id)

        await session.execute(stmt)
        await session.commit()


async def get_order(order_id: str) -> Optional[Order]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(Order).where(Order.order_id == order_id)
        )
        return result.scalar_one_or_none()


async def get_recent_orders(
    user_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 10,
):
    async with SessionLocal() as session:
        stmt = select(Order)

        if user_id is not None:
            stmt = stmt.where(Order.user_id == user_id)

        if status is not None:
            stmt = stmt.where(Order.status == status)

        stmt = stmt.order_by(Order.created_at.desc()).limit(limit)

        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_active_order(user_id: int) -> Optional[Order]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(Order)
            .where(
                Order.user_id == user_id,
                (
                    (
                        (Order.status == "pending")
                        & (Order.expires_at >= now_utc())
                    )
                    | Order.status.in_(
                        [
                            "awaiting_review",
                            "processing",
                        ]
                    )
                ),
            )
            .order_by(Order.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


# =========================================================
# COINMARKETCAP USDT/TWD
# =========================================================

async def get_usdt_twd_rate() -> Optional[Decimal]:
    """
    匯率只作畫面參考；付款金額固定 VIP_PRICE_USDT。
    """
    url = (
        "https://pro-api.coinmarketcap.com"
        "/public-api/v2/simple/price"
    )

    params = {
        "symbol": "USDT",
        "convert": "TWD",
        "precision": 4,
    }

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "User-Agent": "77VIPBot/1.0",
            },
        ) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

        assets = data.get("data", [])
        if not assets:
            raise ValueError("CoinMarketCap 沒有回傳 USDT 資料")

        # 容錯：data 可能為 list；quotes 可能為 list 或 dict。
        usdt_data = assets[0] if isinstance(assets, list) else assets

        quotes = usdt_data.get("quotes", [])

        raw_price = None

        if isinstance(quotes, list):
            for quote in quotes:
                if quote.get("symbol") == "TWD":
                    raw_price = quote.get("price")
                    break

        elif isinstance(quotes, dict):
            twd_quote = quotes.get("TWD", {})
            if isinstance(twd_quote, dict):
                raw_price = twd_quote.get("price")
            elif twd_quote is not None:
                raw_price = twd_quote

        if raw_price is None:
            raise ValueError("CoinMarketCap 沒有回傳 TWD price")

        rate = Decimal(str(raw_price))
        if rate <= 0:
            raise ValueError("USDT/TWD 匯率異常")

        print(f"Rate source: CoinMarketCap | 1 USDT = {rate} TWD")
        return rate

    except Exception as e:
        print(f"CoinMarketCap rate error: {e}")
        return None



# =========================================================
# AUTOMATIC ORDER EXPIRY NOTIFICATION
# =========================================================

async def notify_expired_orders():
    """
    將已超過 30 分鐘、仍為 pending 的訂單標記為 expired，
    並主動通知會員一次。

    使用 status 的 atomic transition 防止同一筆訂單重複通知：
    pending -> expired
    """
    now = now_utc()
    notify_list = []

    async with SessionLocal() as session:
        result = await session.execute(
            select(Order)
            .where(
                Order.status == "pending",
                Order.expires_at < now,
            )
            .order_by(Order.expires_at.asc())
            .limit(100)
        )

        expired_candidates = list(result.scalars().all())

        for order in expired_candidates:
            changed = await session.execute(
                update(Order)
                .where(
                    Order.order_id == order.order_id,
                    Order.status == "pending",
                    Order.expires_at < now,
                )
                .values(status="expired")
            )

            if changed.rowcount == 1:
                notify_list.append(
                    (
                        order.order_id,
                        order.user_id,
                    )
                )

        await session.commit()

    for order_id, user_id in notify_list:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔥 重新購買 VIP",
                        callback_data="buy_vip",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🏠 首頁",
                        callback_data="home",
                    )
                ],
            ]
        )

        try:
            await telegram_app.bot.send_message(
                chat_id=user_id,
                text=(
                    "⏰ 訂單已超時\n\n"
                    f"📄 訂單號：{order_id}\n\n"
                    "此訂單已失效，請重新建立付款訂單。"
                ),
                reply_markup=keyboard,
            )

            print(
                f"Expired order notified: "
                f"{order_id} -> {user_id}"
            )

        except Exception as e:
            print(
                f"Expired order notification failed "
                f"{order_id}: {e}"
            )


async def order_expiry_worker():
    """
    每 30 秒掃描一次過期訂單。
    Render 重新啟動時也會立即先掃描一次。
    """
    while True:
        try:
            await notify_expired_orders()

        except asyncio.CancelledError:
            raise

        except Exception as e:
            print(
                f"Order expiry worker error: {e}"
            )

        await asyncio.sleep(30)


# =========================================================
# ORDER CREATION
# =========================================================

async def create_order_for_user(user) -> Order:
    """
    一位使用者同時間只允許一筆 active 訂單。
    pending 若建立新單會取消舊 pending；
    awaiting_review / processing 則不允許建立新單。
    """
    await notify_expired_orders()

    async with SessionLocal() as session:
        result = await session.execute(
            select(Order)
            .where(
                Order.user_id == user.id,
                Order.status.in_(["awaiting_review", "processing"]),
            )
            .order_by(Order.created_at.desc())
            .limit(1)
        )

        locked_order = result.scalar_one_or_none()

        if locked_order:
            return locked_order

        # 舊的 pending 改為取消，避免同時有多筆待付款訂單。
        await session.execute(
            update(Order)
            .where(
                Order.user_id == user.id,
                Order.status == "pending",
            )
            .values(
                status="cancelled",
                cancelled_at=now_utc(),
            )
        )

        rate = await get_usdt_twd_rate()

        order = Order(
            order_id=create_order_id(),
            user_id=user.id,
            username=user.username,
            full_name=user.full_name,
            amount_usdt=VIP_PRICE_USDT,
            rate_twd=rate,
            status="pending",
            created_at=now_utc(),
            expires_at=now_utc() + timedelta(minutes=30),
        )

        session.add(order)
        await session.commit()
        await session.refresh(order)
        return order


# =========================================================
# /start
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "✨ 歡迎來到 77VIP\n\n"
        "🔐 77VIP 專屬入口\n\n"
        f"🔥 永久 VIP｜{VIP_PRICE_USDT} USDT\n\n"
        "💵 USDT（TRC20）付款",
        reply_markup=main_reply_keyboard(),
    )

    await update.message.reply_text(
        "請選擇下方功能 👇",
        reply_markup=home_inline_keyboard(),
    )


# =========================================================
# /myid
# =========================================================

async def myid(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        f"🆔 你的 Telegram ID：\n\n{update.effective_user.id}"
    )


# =========================================================
# BUY
# =========================================================

async def buy_vip(query, context):
    await query.answer()

    user = query.from_user

    try:
        member = await context.bot.get_chat_member(
            chat_id=VIP_CHANNEL_ID,
            user_id=user.id,
        )

        if member.status in [
            "member",
            "administrator",
            "creator",
        ]:
            await query.edit_message_text(
                "✨ 你已經是 77VIP 會員囉！\n\n"
                "不需要再次購買 🔐"
            )
            return

    except Exception:
        pass

    active = await get_active_order(user.id)

    if active and active.status in ["awaiting_review", "processing"]:
        await query.edit_message_text(
            "🔎 你目前有一筆訂單正在處理中。\n\n"
            f"📄 訂單：{active.order_id}\n"
            f"狀態：{status_label(active.status)}\n\n"
            "請等待管理員完成確認。"
        )
        return

    await pay_usdt(query, context)


# =========================================================
# PAY PAGE
# =========================================================

async def pay_usdt(query, context):
    user = query.from_user

    try:
        await query.edit_message_text(
            "⏳ 正在建立 USDT 訂單..."
        )
    except Exception:
        pass

    order = await create_order_for_user(user)

    if order.status in ["awaiting_review", "processing"]:
        await context.bot.send_message(
            chat_id=user.id,
            text=(
                "🔎 你目前有一筆訂單正在處理中。\n\n"
                f"📄 訂單：{order.order_id}\n"
                f"狀態：{status_label(order.status)}"
            ),
        )
        return

    qr_image = create_usdt_qr(USDT_TRC20_ADDRESS)

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ 我已支付",
                    callback_data=f"payment_done:{order.order_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ 取消支付",
                    callback_data=f"cancel:{order.order_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "🏠 首頁",
                    callback_data="home",
                )
            ],
        ]
    )

    if order.rate_twd is not None:
        display_rate = Decimal(order.rate_twd).quantize(
            Decimal("0.01")
        )
        rate_text = (
            f"💱 即時匯率："
            f"1 USDT ≈ NT${display_rate}\n"
        )
    else:
        rate_text = "💱 即時匯率：暫時無法取得\n"

    text = (
        "💵 USDT 付款\n\n"
        f"📄 訂單號：{order.order_id}\n"
        f"💰 支付金額：{Decimal(order.amount_usdt):g} USDT\n"
        f"{rate_text}\n"
        "📥 收款地址（TRC20）：\n"
        f"{USDT_TRC20_ADDRESS}\n\n"
        "━━━━━━━━━━━━━━\n"
        "⏰ 請在 30 分鐘內完成轉帳\n\n"
        f"✅ 實際應付金額固定為 "
        f"{Decimal(order.amount_usdt):g} USDT\n"
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
        reply_markup=keyboard,
    )


# =========================================================
# PAYMENT CLAIM
# =========================================================

async def payment_done(
    query,
    context,
    order_id: str,
):
    await query.answer()

    user = query.from_user
    now = now_utc()

    async with SessionLocal() as session:
        result = await session.execute(
            select(Order).where(
                Order.order_id == order_id,
                Order.user_id == user.id,
            )
        )
        order = result.scalar_one_or_none()

        if not order:
            await query.answer(
                "找不到這筆訂單。",
                show_alert=True,
            )
            return

        if order.status == "awaiting_review":
            await query.answer(
                "這筆訂單已經提交審核。",
                show_alert=True,
            )
            return

        if order.status != "pending":
            await query.answer(
                f"此訂單目前狀態：{status_label(order.status)}",
                show_alert=True,
            )
            return

        if order.expires_at < now:
            order.status = "expired"
            await session.commit()

            await query.answer(
                "此訂單已超過 30 分鐘，請重新建立訂單。",
                show_alert=True,
            )
            return

        # 原子狀態轉移：pending -> awaiting_review
        changed = await session.execute(
            update(Order)
            .where(
                Order.order_id == order_id,
                Order.user_id == user.id,
                Order.status == "pending",
            )
            .values(
                status="awaiting_review",
                claimed_at=now,
            )
        )
        await session.commit()

        if changed.rowcount != 1:
            await query.answer(
                "這筆訂單已經被處理，請勿重複提交。",
                show_alert=True,
            )
            return

        amount = Decimal(order.amount_usdt)
        rate = order.rate_twd
        username = f"@{user.username}" if user.username else "未設定"

    admin_keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ 確認收款",
                    callback_data=f"paidok:{order_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ 未收到款",
                    callback_data=f"paidno:{order_id}",
                )
            ],
        ]
    )

    admin_text = (
        "💰 新的 VIP 付款確認\n\n"
        f"📄 訂單：{order_id}\n"
        f"💵 應付金額：{amount:g} USDT\n"
    )

    if rate is not None:
        display_rate = Decimal(rate).quantize(Decimal("0.01"))
        admin_text += (
            f"💱 建單匯率：1 USDT ≈ NT${display_rate}\n"
        )

    admin_text += (
        "\n"
        f"👤 名稱：{user.full_name}\n"
        f"🔗 Username：{username}\n"
        f"🆔 User ID：{user.id}\n\n"
        "⚠️ 請先確認自己的錢包／交易紀錄。\n"
        f"確認實際收到 {amount:g} USDT 後再批准。"
    )

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=admin_text,
        reply_markup=admin_keyboard,
    )

    await query.edit_message_caption(
        caption=(
            "✅ 已提交付款確認\n\n"
            f"📄 訂單：{order_id}\n"
            f"💵 金額：{amount:g} USDT\n\n"
            "正在等待管理員確認收款。\n"
            "確認完成後 Bot 會通知你 🔔"
        )
    )


# =========================================================
# ADMIN APPROVE — IDEMPOTENT
# =========================================================

async def payment_approve(
    query,
    context,
    order_id: str,
):
    if query.from_user.id != ADMIN_ID:
        await query.answer(
            "你沒有管理員權限。",
            show_alert=True,
        )
        return

    now = now_utc()

    # 先用 atomic compare-and-set 搶處理權：
    # awaiting_review -> processing
    async with SessionLocal() as session:
        changed = await session.execute(
            update(Order)
            .where(
                Order.order_id == order_id,
                Order.status == "awaiting_review",
            )
            .values(status="processing")
        )
        await session.commit()

        if changed.rowcount != 1:
            result = await session.execute(
                select(Order).where(Order.order_id == order_id)
            )
            order = result.scalar_one_or_none()

            if not order:
                await query.answer(
                    "找不到這筆訂單。",
                    show_alert=True,
                )
                return

            await query.answer(
                f"此訂單已處理：{status_label(order.status)}",
                show_alert=True,
            )
            return

        result = await session.execute(
            select(Order).where(Order.order_id == order_id)
        )
        order = result.scalar_one()

        user_id = order.user_id
        amount = Decimal(order.amount_usdt)

    await query.answer("正在開通 VIP…")

    try:
        # 曾被移除 / ban 的人先解除。
        try:
            await context.bot.unban_chat_member(
                chat_id=VIP_CHANNEL_ID,
                user_id=user_id,
                only_if_banned=True,
            )
        except Exception as e:
            print(f"Unban error: {e}")

        invite_expire = now_utc() + timedelta(minutes=30)

        invite = await context.bot.create_chat_invite_link(
            chat_id=VIP_CHANNEL_ID,
            expire_date=invite_expire,
            name=f"VIP-{order_id}",
        )

        vip_keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔐 進入 77VIP",
                        url=invite.invite_link,
                    )
                ]
            ]
        )

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "🎉 付款確認完成！\n\n"
                f"📄 訂單：{order_id}\n"
                f"💵 金額：{amount:g} USDT\n"
                "✅ VIP 已開通\n\n"
                "下方是你的專屬加入連結 🔐\n\n"
                "⚠️ 邀請連結 30 分鐘後失效。\n"
                "成功加入後，邀請連結會立即撤銷。"
            ),
            reply_markup=vip_keyboard,
        )

        async with SessionLocal() as session:
            await session.execute(
                update(Order)
                .where(
                    Order.order_id == order_id,
                    Order.status == "processing",
                )
                .values(
                    status="approved",
                    approved_at=now_utc(),
                    invite_link=invite.invite_link,
                )
            )
            await session.commit()

        await query.edit_message_text(
            query.message.text
            + "\n\n✅ 已確認收款\n"
            + "✅ VIP 邀請已傳送"
        )

    except Exception as e:
        print(f"Payment approve error: {e}")

        # 外部操作失敗就退回待審核，可重新按確認。
        async with SessionLocal() as session:
            await session.execute(
                update(Order)
                .where(
                    Order.order_id == order_id,
                    Order.status == "processing",
                )
                .values(status="awaiting_review")
            )
            await session.commit()

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "❌ VIP 開通失敗\n\n"
                f"訂單：{order_id}\n"
                f"User ID：{user_id}\n"
                f"錯誤：{e}\n\n"
                "訂單已恢復為待確認，可再次按確認收款。"
            ),
        )


# =========================================================
# ADMIN REJECT — IDEMPOTENT
# =========================================================

async def payment_reject(
    query,
    context,
    order_id: str,
):
    if query.from_user.id != ADMIN_ID:
        await query.answer(
            "你沒有管理員權限。",
            show_alert=True,
        )
        return

    now = now_utc()

    async with SessionLocal() as session:
        result = await session.execute(
            select(Order).where(Order.order_id == order_id)
        )
        order = result.scalar_one_or_none()

        if not order:
            await query.answer(
                "找不到這筆訂單。",
                show_alert=True,
            )
            return

        changed = await session.execute(
            update(Order)
            .where(
                Order.order_id == order_id,
                Order.status == "awaiting_review",
            )
            .values(
                status="rejected",
                rejected_at=now,
            )
        )
        await session.commit()

        if changed.rowcount != 1:
            await query.answer(
                f"此訂單已處理：{status_label(order.status)}",
                show_alert=True,
            )
            return

        user_id = order.user_id

    await query.answer("已標記為未收到款。")

    await context.bot.send_message(
        chat_id=user_id,
        text=(
            "⚠️ 目前尚未確認收到款項。\n\n"
            f"📄 訂單：{order_id}\n\n"
            "請再次確認：\n"
            "• 是否使用 TRC20 網路\n"
            "• 收款地址是否正確\n"
            f"• 是否實際轉出 {VIP_PRICE_USDT:g} USDT\n"
            "• 交易是否已完成\n\n"
            "如已付款，請聯絡管理員協助確認。"
        ),
    )

    await query.edit_message_text(
        query.message.text
        + "\n\n❌ 尚未確認收到款"
    )


# =========================================================
# CANCEL ORDER
# =========================================================

async def cancel_payment(
    query,
    context,
    order_id: str,
):
    await query.answer()

    user_id = query.from_user.id

    async with SessionLocal() as session:
        changed = await session.execute(
            update(Order)
            .where(
                Order.order_id == order_id,
                Order.user_id == user_id,
                Order.status == "pending",
            )
            .values(
                status="cancelled",
                cancelled_at=now_utc(),
            )
        )
        await session.commit()

    if changed.rowcount != 1:
        order = await get_order(order_id)
        current = status_label(order.status) if order else "不存在"

        await query.answer(
            f"此訂單無法取消，目前狀態：{current}",
            show_alert=True,
        )
        return

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔥 重新購買 VIP",
                    callback_data="buy_vip",
                )
            ],
            [
                InlineKeyboardButton(
                    "🏠 首頁",
                    callback_data="home",
                )
            ],
        ]
    )

    try:
        await query.edit_message_caption(
            caption="❌ 訂單已取消。",
            reply_markup=keyboard,
        )
    except Exception:
        await query.edit_message_text(
            "❌ 訂單已取消。",
            reply_markup=keyboard,
        )


# =========================================================
# MEMBER JOIN -> REVOKE INVITE + DB
# =========================================================

async def vip_member_update(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    member_update = update.chat_member

    if not member_update:
        return

    if member_update.chat.id != VIP_CHANNEL_ID:
        return

    new_member = member_update.new_chat_member

    if new_member.status not in [
        "member",
        "administrator",
        "creator",
    ]:
        return

    user = new_member.user
    invite_link = member_update.invite_link

    if not invite_link or not invite_link.name:
        return

    if not invite_link.name.startswith("VIP-"):
        return

    order_id = invite_link.name[4:]
    order = await get_order(order_id)

    if not order:
        return

    if order.user_id != user.id:
        print(
            f"Invite mismatch: order={order_id} "
            f"expected={order.user_id} got={user.id}"
        )
        return

    try:
        await context.bot.revoke_chat_invite_link(
            chat_id=VIP_CHANNEL_ID,
            invite_link=invite_link.invite_link,
        )

        async with SessionLocal() as session:
            await session.execute(
                update(Order)
                .where(
                    Order.order_id == order_id,
                    Order.user_id == user.id,
                    Order.status.in_(["approved", "joined"]),
                )
                .values(
                    status="joined",
                    joined_at=now_utc(),
                    invite_revoked_at=now_utc(),
                )
            )
            await session.commit()

        await context.bot.send_message(
            chat_id=user.id,
            text=(
                "✅ 已成功加入 77VIP！\n\n"
                f"📄 訂單：{order_id}\n"
                "🔒 你的專屬邀請連結已自動失效。"
            ),
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
                f"📄 訂單：{order_id}\n"
                f"👤 名稱：{user.full_name}\n"
                f"🔗 Username：{username}\n"
                f"🆔 User ID：{user.id}\n\n"
                "🔒 專屬邀請連結已撤銷。"
            ),
        )

    except Exception as e:
        print(f"Revoke invite error: {e}")


# =========================================================
# USER ORDER HISTORY
# =========================================================

async def send_my_orders(
    chat_id: int,
    user_id: int,
    context: ContextTypes.DEFAULT_TYPE,
):
    await notify_expired_orders()

    orders = await get_recent_orders(
        user_id=user_id,
        limit=10,
    )

    if not orders:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "📋 我的訂單\n\n"
                "目前沒有任何訂單紀錄。"
            ),
        )
        return

    lines = ["📋 我的訂單｜最近 10 筆\n"]

    for order in orders:
        lines.append(
            f"📄 {order.order_id}\n"
            f"💵 {Decimal(order.amount_usdt):g} USDT\n"
            f"📌 {status_label(order.status)}\n"
            f"🕒 {fmt_time(order.created_at)}\n"
        )

    await context.bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
    )


async def my_order_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await send_my_orders(
        chat_id=update.effective_chat.id,
        user_id=update.effective_user.id,
        context=context,
    )


# =========================================================
# USER PURCHASE STATUS
# =========================================================

async def send_my_purchases(
    chat_id: int,
    user_id: int,
    context: ContextTypes.DEFAULT_TYPE,
):
    try:
        member = await context.bot.get_chat_member(
            chat_id=VIP_CHANNEL_ID,
            user_id=user_id,
        )

        is_vip = member.status in [
            "member",
            "administrator",
            "creator",
        ]

    except Exception:
        is_vip = False

    async with SessionLocal() as session:
        result = await session.execute(
            select(Order)
            .where(
                Order.user_id == user_id,
                Order.status.in_(["approved", "joined"]),
            )
            .order_by(Order.approved_at.desc())
            .limit(1)
        )
        order = result.scalar_one_or_none()

    if is_vip:
        text = "🌟 我的購買\n\n✅ 永久 VIP\n狀態：已開通"

        if order:
            text += (
                f"\n\n📄 訂單：{order.order_id}"
                f"\n🕒 開通：{fmt_time(order.approved_at)}"
            )

    else:
        text = (
            "🌟 我的購買\n\n"
            "目前尚未加入 77VIP。"
        )

        if order:
            text += (
                f"\n\n最近已核准訂單：{order.order_id}"
                f"\n狀態：{status_label(order.status)}"
            )

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
    )


async def my_purchases_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await send_my_purchases(
        chat_id=update.effective_chat.id,
        user_id=update.effective_user.id,
        context=context,
    )


# =========================================================
# PLACEHOLDER MENUS
# =========================================================

async def my_discount_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "🎟 我的優惠\n\n"
        "目前暫無可使用優惠。"
    )


async def invite_reward_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "💰 邀請返利\n\n"
        "邀請返利功能目前尚未開放。"
    )


# =========================================================
# ADMIN /orders [status]
# =========================================================

async def admin_orders(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.effective_user.id != ADMIN_ID:
        return

    await notify_expired_orders()

    status = None

    if context.args:
        status = context.args[0].lower()

        if status not in STATUS_LABELS:
            await update.message.reply_text(
                "可用狀態：\n"
                + ", ".join(STATUS_LABELS.keys())
            )
            return

    orders = await get_recent_orders(
        status=status,
        limit=15,
    )

    if not orders:
        await update.message.reply_text(
            "目前沒有符合條件的訂單。"
        )
        return

    title = (
        f"🧾 訂單查詢｜{status_label(status)}"
        if status
        else "🧾 最近 15 筆訂單"
    )

    lines = [title, ""]

    for order in orders:
        username = (
            f"@{order.username}"
            if order.username
            else "無 Username"
        )

        lines.append(
            f"📄 {order.order_id}\n"
            f"👤 {order.full_name}｜{username}\n"
            f"🆔 {order.user_id}\n"
            f"💵 {Decimal(order.amount_usdt):g} USDT\n"
            f"📌 {status_label(order.status)}\n"
            f"🕒 {fmt_time(order.created_at)}\n"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


# =========================================================
# ADMIN /order ORDER_ID
# =========================================================

async def admin_order_detail(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.effective_user.id != ADMIN_ID:
        return

    if not context.args:
        await update.message.reply_text(
            "用法：/order VIP2026XXXXXX"
        )
        return

    order_id = context.args[0].strip()
    order = await get_order(order_id)

    if not order:
        await update.message.reply_text(
            "找不到這筆訂單。"
        )
        return

    username = (
        f"@{order.username}"
        if order.username
        else "未設定"
    )

    rate_text = (
        f"{Decimal(order.rate_twd).quantize(Decimal('0.01'))}"
        if order.rate_twd is not None
        else "-"
    )

    await update.message.reply_text(
        "🧾 訂單詳細資料\n\n"
        f"📄 訂單：{order.order_id}\n"
        f"📌 狀態：{status_label(order.status)}\n"
        f"👤 名稱：{order.full_name}\n"
        f"🔗 Username：{username}\n"
        f"🆔 User ID：{order.user_id}\n"
        f"💵 金額：{Decimal(order.amount_usdt):g} USDT\n"
        f"💱 匯率：1 USDT ≈ NT${rate_text}\n\n"
        f"建立：{fmt_time(order.created_at)}\n"
        f"到期：{fmt_time(order.expires_at)}\n"
        f"送審：{fmt_time(order.claimed_at)}\n"
        f"核准：{fmt_time(order.approved_at)}\n"
        f"拒絕：{fmt_time(order.rejected_at)}\n"
        f"加入：{fmt_time(order.joined_at)}"
    )


# =========================================================
# HOME + BOTTOM MENU
# =========================================================

async def show_home_message(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
):
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "✨ 77VIP 專屬入口\n\n"
            f"🔥 永久 VIP｜{VIP_PRICE_USDT} USDT\n"
            "💵 USDT（TRC20）付款"
        ),
        reply_markup=home_inline_keyboard(),
    )


async def menu_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    text = update.message.text
    user = update.effective_user
    chat_id = update.effective_chat.id

    if text == "🏠 首頁":
        await show_home_message(chat_id, context)
        return

    if text == "📋 我的訂單":
        await send_my_orders(
            chat_id,
            user.id,
            context,
        )
        return

    if text == "🌟 我的購買":
        await send_my_purchases(
            chat_id,
            user.id,
            context,
        )
        return

    if text == "🎟 我的優惠":
        await update.message.reply_text(
            "🎟 我的優惠\n\n"
            "目前暫無可使用優惠。"
        )
        return

    if text == "💰 邀請返利":
        await update.message.reply_text(
            "💰 邀請返利\n\n"
            "邀請返利功能目前尚未開放。"
        )
        return


# =========================================================
# CALLBACK ROUTER
# =========================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    data = query.data or ""

    if data == "buy_vip":
        await buy_vip(query, context)
        return

    if data == "home":
        await query.answer()
        try:
            await query.edit_message_text(
                "✨ 77VIP 專屬入口\n\n"
                f"🔥 永久 VIP｜{VIP_PRICE_USDT} USDT\n"
                "💵 USDT（TRC20）付款",
                reply_markup=home_inline_keyboard(),
            )
        except Exception:
            await show_home_message(
                query.from_user.id,
                context,
            )
        return

    if data.startswith("payment_done:"):
        order_id = data.split(":", 1)[1]
        await payment_done(
            query,
            context,
            order_id,
        )
        return

    if data.startswith("cancel:"):
        order_id = data.split(":", 1)[1]
        await cancel_payment(
            query,
            context,
            order_id,
        )
        return

    if data.startswith("paidok:"):
        order_id = data.split(":", 1)[1]
        await payment_approve(
            query,
            context,
            order_id,
        )
        return

    if data.startswith("paidno:"):
        order_id = data.split(":", 1)[1]
        await payment_reject(
            query,
            context,
            order_id,
        )
        return

    await query.answer()


# =========================================================
# HANDLERS
# =========================================================

telegram_app.add_handler(
    CommandHandler("start", start)
)

telegram_app.add_handler(
    CommandHandler("myid", myid)
)

telegram_app.add_handler(
    CommandHandler("my_order", my_order_command)
)

telegram_app.add_handler(
    CommandHandler("my_purchases", my_purchases_command)
)

telegram_app.add_handler(
    CommandHandler("my_discount", my_discount_command)
)

telegram_app.add_handler(
    CommandHandler("invite_reward", invite_reward_command)
)

telegram_app.add_handler(
    CommandHandler("orders", admin_orders)
)

telegram_app.add_handler(
    CommandHandler("order", admin_order_detail)
)

telegram_app.add_handler(
    CallbackQueryHandler(button_handler)
)

telegram_app.add_handler(
    ChatMemberHandler(
        vip_member_update,
        ChatMemberHandler.CHAT_MEMBER,
    )
)

telegram_app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        menu_handler,
    )
)


# =========================================================
# STARLETTE
# =========================================================

async def homepage(request: Request):
    db_type = (
        "PostgreSQL"
        if DATABASE_URL.startswith("postgresql+asyncpg://")
        else "SQLite"
    )

    return PlainTextResponse(
        f"77 VIP Bot DB FINAL is running! | DB={db_type}"
    )


async def webhook(request: Request):
    data = await request.json()

    telegram_update = Update.de_json(
        data=data,
        bot=telegram_app.bot,
    )

    await telegram_app.process_update(
        telegram_update
    )

    return PlainTextResponse("OK")


async def startup():
    global order_expiry_task

    # 建表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await telegram_app.initialize()

    await telegram_app.bot.set_my_commands(
        [
            BotCommand("start", "🏠 店鋪首頁"),
            BotCommand("my_order", "📋 我的訂單"),
            BotCommand("my_purchases", "🌟 我的購買"),
            BotCommand("my_discount", "🎟 我的優惠"),
            BotCommand("invite_reward", "💰 邀請返利"),
        ]
    )

    await telegram_app.bot.set_webhook(
        url=f"{WEBHOOK_URL}/webhook",
        allowed_updates=[
            "message",
            "callback_query",
            "chat_member",
        ],
    )

    # 啟動時先檢查一次，接著每 30 秒檢查。
    await notify_expired_orders()

    order_expiry_task = asyncio.create_task(
        order_expiry_worker()
    )


async def shutdown():
    global order_expiry_task

    if order_expiry_task is not None:
        order_expiry_task.cancel()

        try:
            await order_expiry_task
        except asyncio.CancelledError:
            pass

        order_expiry_task = None

    await telegram_app.shutdown()
    await engine.dispose()


app = Starlette(
    routes=[
        Route(
            "/",
            homepage,
            methods=["GET", "HEAD"],
        ),
        Route(
            "/webhook",
            webhook,
            methods=["POST"],
        ),
    ],
    on_startup=[startup],
    on_shutdown=[shutdown],
)
