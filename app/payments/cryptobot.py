"""CryptoBot (@CryptoBot): счёт в USDT и проверка вебхука.

Crypto Pay API — это несколько POST-запросов с токеном в заголовке, поэтому клиент написан
на aiohttp напрямую. Официальную обёртку ``aiocryptopay`` подключить нельзя: она закрепляет
``certifi<2024`` и ``pydantic`` старых версий, что несовместимо с curl_cffi и aiogram 3.15+.

Подтверждение приходит вебхуком на Web API. Подпись проверяется до разбора тела: эндпоинт
открыт в интернет, и «я оплатил, честно» от кого угодно принимать нельзя.

    secret = SHA256(<токен приложения>)
    signature = HMAC_SHA256(key=secret, message=<сырое тело запроса>)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from app.domain.tariffs import Tariff
from app.payments.stars import build_payload, parse_payload
from app.services.billing import Offer

logger = logging.getLogger(__name__)

CRYPTOBOT_PROVIDER = "cryptobot"
DEFAULT_ASSET = "USDT"
API_URLS = {
    "mainnet": "https://pay.crypt.bot/api",
    "testnet": "https://testnet-pay.crypt.bot/api",
}
INVOICE_TTL_SECONDS = 3600


class CryptoBotError(Exception):
    """Счёт выставить не удалось: сеть, токен или ответ не по формату."""


@dataclass(frozen=True, slots=True)
class CryptoInvoice:
    invoice_id: str
    pay_url: str


@dataclass(frozen=True, slots=True)
class PaidInvoice:
    invoice_id: str
    tariff: Tariff
    user_id: int
    amount: Decimal | None
    asset: str | None


class CryptoBotPayments:
    def __init__(self, token: str, *, network: str = "mainnet", asset: str = DEFAULT_ASSET) -> None:
        self._token = token
        self._asset = asset
        self._url = API_URLS[network]
        self._session: ClientSession | None = None

    async def create_invoice(
        self, user_id: int, offer: Offer, *, description: str
    ) -> CryptoInvoice:
        result = await self._call(
            "createInvoice",
            {
                "asset": self._asset,
                "amount": str(offer.price_usd),
                "description": description,
                # Payload вернётся в вебхуке — по нему понятно, кому и что зачислять.
                "payload": build_payload(offer.tariff, user_id),
                "expires_in": INVOICE_TTL_SECONDS,
            },
        )
        try:
            return CryptoInvoice(
                invoice_id=str(result["invoice_id"]), pay_url=str(result["bot_invoice_url"])
            )
        except KeyError as exc:
            raise CryptoBotError(f"CryptoBot: в ответе нет поля {exc}") from exc

    async def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = await self._get_session()
        try:
            response = await session.post(f"{self._url}/{method}", json=payload)
            body = await response.json()
        except (ClientError, ValueError, TimeoutError) as exc:
            raise CryptoBotError(f"CryptoBot недоступен: {exc}") from exc

        if not body.get("ok"):
            raise CryptoBotError(f"CryptoBot отклонил запрос: {body.get('error')}")
        result: dict[str, Any] = body["result"]
        return result

    async def _get_session(self) -> ClientSession:
        if self._session is None or self._session.closed:
            self._session = ClientSession(
                headers={"Crypto-Pay-API-Token": self._token},
                timeout=ClientTimeout(total=20),
            )
        return self._session

    async def aclose(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    @staticmethod
    def verify(token: str, body: bytes, signature: str) -> bool:
        secret = hashlib.sha256(token.encode()).digest()
        expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    @staticmethod
    def parse_webhook(body: bytes) -> PaidInvoice | None:
        """Возвращает оплаченный счёт или ``None``, если это другое событие."""
        try:
            payload: dict[str, Any] = json.loads(body)
        except ValueError:
            logger.warning("CryptoBot: тело вебхука не разбирается")
            return None
        if payload.get("update_type") != "invoice_paid":
            return None

        invoice = payload.get("payload") or {}
        parsed = parse_payload(str(invoice.get("payload", "")))
        if parsed is None:
            logger.error("CryptoBot: не разобран payload счёта %r", invoice.get("invoice_id"))
            return None
        tariff, user_id = parsed
        return PaidInvoice(
            invoice_id=str(invoice.get("invoice_id")),
            tariff=tariff,
            user_id=user_id,
            amount=_to_decimal(invoice.get("amount")),
            asset=invoice.get("asset"),
        )


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None
