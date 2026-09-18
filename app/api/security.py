"""Проверка подлинности Mini App: разбор и валидация Telegram ``initData``.

Telegram отдаёт Web App строку ``initData`` — query-string с данными пользователя и подписью.
Подпись проверяется по схеме из документации Bot API:

    secret_key   = HMAC_SHA256(key="WebAppData", message=<токен бота>)
    data_check   = "\\n".join(f"{k}={v}" for k, v in sorted(поля кроме hash))
    hash         = HMAC_SHA256(key=secret_key, message=data_check)

Доверять чему-либо из ``initData`` до проверки подписи нельзя: это обычная строка из браузера,
её подделает кто угодно. Файл не зависит от FastAPI, поэтому проверяется юнит-тестами.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl

DEFAULT_MAX_AGE = timedelta(hours=24)
_SECRET_SALT = b"WebAppData"


class InitDataError(Exception):
    """initData отсутствует, просрочена или подписана не нашим токеном."""


@dataclass(frozen=True, slots=True)
class WebAppUser:
    """Пользователь, чью личность подтвердила подпись Telegram."""

    id: int
    username: str | None
    full_name: str
    language_code: str | None
    auth_date: datetime


def parse_init_data(
    raw: str, bot_token: str, *, max_age: timedelta = DEFAULT_MAX_AGE, now: datetime | None = None
) -> WebAppUser:
    """Проверяет подпись и возвращает пользователя.

    :raises InitDataError: подпись не сходится, данные просрочены или сломаны.
    """
    if not raw:
        raise InitDataError("initData не передана")

    # strict_parsing и keep_blank_values: строку нельзя «подчистить» — она часть подписи.
    try:
        fields = dict(parse_qsl(raw, keep_blank_values=True, strict_parsing=True))
    except ValueError as exc:
        raise InitDataError("initData не разбирается") from exc

    received_hash = fields.pop("hash", None)
    if not received_hash:
        raise InitDataError("В initData нет поля hash")

    if not hmac.compare_digest(_signature(fields, bot_token), received_hash):
        raise InitDataError("Подпись initData не совпадает")

    auth_date = _auth_date(fields)
    moment = now or datetime.now(UTC)
    if moment - auth_date > max_age:
        # Иначе перехваченная один раз initData работала бы вечно.
        raise InitDataError("initData просрочена")
    if auth_date - moment > timedelta(minutes=5):
        raise InitDataError("initData из будущего")

    return _user(fields, auth_date)


def _signature(fields: dict[str, str], bot_token: str) -> str:
    data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret_key = hmac.new(_SECRET_SALT, bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()


def _auth_date(fields: dict[str, str]) -> datetime:
    try:
        return datetime.fromtimestamp(int(fields["auth_date"]), UTC)
    except (KeyError, ValueError) as exc:
        raise InitDataError("Некорректное поле auth_date") from exc


def _user(fields: dict[str, str], auth_date: datetime) -> WebAppUser:
    try:
        payload = json.loads(fields["user"])
        user_id = int(payload["id"])
    except (KeyError, ValueError, TypeError) as exc:
        raise InitDataError("В initData нет данных пользователя") from exc

    full_name = " ".join(
        part for part in (payload.get("first_name"), payload.get("last_name")) if part
    )
    return WebAppUser(
        id=user_id,
        username=payload.get("username"),
        full_name=full_name or str(user_id),
        language_code=payload.get("language_code"),
        auth_date=auth_date,
    )


def build_init_data(fields: dict[str, str], bot_token: str) -> str:
    """Собирает подписанную initData — нужно тестам и локальной отладке Mini App."""
    from urllib.parse import urlencode

    signed = dict(fields)
    signed["hash"] = _signature(signed, bot_token)
    return urlencode(signed)
