"""Оплата Telegram Stars: подтверждение платежа и зачисление подписки.

Счёт выставляет Web API (Mini App открывает его через ``WebApp.openInvoice``), а сюда
Telegram присылает два апдейта: предварительную проверку и факт оплаты. Больше бот
в оплате не участвует — тарифы и сроки считает ``BillingService``.
"""

import logging
from decimal import Decimal

from aiogram import F, Router
from aiogram.types import Message, PreCheckoutQuery

from app.config import BotSettings
from app.handlers.common import open_app_keyboard
from app.payments.stars import STARS_PROVIDER, parse_payload
from app.services.billing import BillingService

logger = logging.getLogger(__name__)

router = Router(name="payments")


@router.pre_checkout_query()
async def confirm_checkout(query: PreCheckoutQuery) -> None:
    """Telegram даёт ~10 секунд на ответ; отвечать нужно всегда, иначе оплата сорвётся."""
    if parse_payload(query.invoice_payload) is None:
        await query.answer(ok=False, error_message="Счёт устарел, откройте приложение заново")
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def payment_succeeded(
    message: Message, billing: BillingService, bot_settings: BotSettings
) -> None:
    payment = message.successful_payment
    if payment is None or message.from_user is None:
        return

    parsed = parse_payload(payment.invoice_payload)
    if parsed is None:
        # Деньги списаны, а payload не читается — это не должно случаться молча.
        logger.error("Не разобран payload оплаты: %r", payment.invoice_payload)
        await message.answer("Платёж получен, но не распознан. Напишите в поддержку.")
        return

    tariff, user_id = parsed
    subscription = await billing.activate_paid(
        user_id,
        tariff,
        provider=STARS_PROVIDER,
        # Повторная доставка апдейта не продлит подписку второй раз.
        payment_id=payment.telegram_payment_charge_id,
        amount=Decimal(payment.total_amount),
        currency=payment.currency,
    )
    logger.info("Stars: пользователь %d оплатил тариф %s", user_id, tariff.value)

    text = f"✅ Подписка активна до {subscription.ends_at:%d.%m.%Y}."
    if bot_settings.webapp_url:
        await message.answer(text, reply_markup=open_app_keyboard(bot_settings.webapp_url))
    else:
        await message.answer(text)
