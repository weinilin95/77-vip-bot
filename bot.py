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
