"""Парсер OLX Украина.

Используется публичный JSON API сайта (``/api/v1/offers/``), которым пользуется сам фронтенд OLX.
Это стабильнее разбора HTML, но API не документирован и может меняться — вся специфика
формата изолирована в ``parse_offers``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.entities import Listing, SearchCriteria
from app.domain.errors import InvalidCriteriaError
from app.services.parsers.base import MarketplaceParser
from app.services.parsers.errors import ParserResponseError
from app.services.parsers.http_client import HttpClient

logger = logging.getLogger(__name__)

API_URL = "https://www.olx.ua/api/v1/offers/"
DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "uk-UA,uk;q=0.9,ru;q=0.8,en;q=0.7",
    "Referer": "https://www.olx.ua/",
}
# Дополнительные фильтры OLX, которые можно передать через SearchCriteria.extra.
ALLOWED_EXTRA_PARAMS = frozenset({"category_id", "region_id", "city_id", "district_id", "distance"})
IMAGE_SIZE = "800x600"


class OlxUaParser(MarketplaceParser):
    code = "olx_ua"
    title = "OLX.ua"

    def __init__(self, http: HttpClient, *, page_size: int = 40, max_pages: int = 5) -> None:
        self._http = http
        self._page_size = page_size
        self._max_pages = max_pages

    async def search(
        self, criteria: SearchCriteria, *, since: datetime | None = None
    ) -> Sequence[Listing]:
        """Читает выдачу постранично, пока не дойдёт до объявлений старше ``since``.

        По популярным запросам за один интервал может появиться больше объявлений, чем помещается
        на странице, поэтому одной страницы мало. Ограничители: ``since`` и ``max_pages``.
        """
        listings: list[Listing] = []
        seen: set[str] = set()
        for page in range(self._max_pages):
            params = self.build_params(criteria, offset=page * self._page_size)
            payload = await self._http.get_json(API_URL, params=params)
            batch = parse_offers(payload, marketplace=self.code)
            listings.extend(item for item in batch if item.external_id not in seen)
            seen.update(item.external_id for item in batch)

            if since is None or len(_offers(payload)) < self._page_size:
                break  # первая страница по новому фильтру либо конец выдачи
            oldest = min((item.published_at for item in batch if item.published_at), default=None)
            if oldest is not None and oldest < since:
                break  # дальше только то, что мы уже видели в прошлом цикле
        else:
            logger.warning(
                "OLX: достигнут лимит в %d страниц по запросу '%s' — часть объявлений могла "
                "не попасть в выдачу; уменьшите MONITORING__INTERVAL_SECONDS",
                self._max_pages,
                criteria.query,
            )
        return listings

    def build_params(self, criteria: SearchCriteria, *, offset: int = 0) -> dict[str, Any]:
        params: dict[str, Any] = {
            "offset": offset,
            "limit": self._page_size,
            "query": criteria.query.strip(),
            "sort_by": "created_at:desc",
            "currency": "UAH",
        }
        if criteria.price_min is not None:
            params["filter_float_price:from"] = _format_number(criteria.price_min)
        if criteria.price_max is not None:
            params["filter_float_price:to"] = _format_number(criteria.price_max)
        for key, value in criteria.extra.items():
            if key in ALLOWED_EXTRA_PARAMS:
                params[key] = value
        return params

    def validate_criteria(self, criteria: SearchCriteria) -> None:
        super().validate_criteria(criteria)
        unknown = set(criteria.extra) - ALLOWED_EXTRA_PARAMS
        if unknown:
            raise InvalidCriteriaError(f"Неизвестные параметры OLX: {', '.join(sorted(unknown))}")

    async def aclose(self) -> None:
        await self._http.aclose()


def _offers(payload: Any) -> list[Any]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
        raise ParserResponseError("OLX: в ответе нет списка 'data'")
    return list(payload["data"])


def parse_offers(payload: Any, *, marketplace: str = OlxUaParser.code) -> list[Listing]:
    offers = _offers(payload)
    listings: list[Listing] = []
    for offer in offers:
        try:
            listings.append(_parse_offer(offer, marketplace))
        except (KeyError, TypeError, ValueError) as exc:
            # Одно «кривое» объявление не должно ломать весь результат.
            offer_id = offer.get("id") if isinstance(offer, Mapping) else None
            logger.warning("OLX: пропускаю объявление %s: %r", offer_id, exc)
    if offers and not listings:
        # API не документирован: если не разобралось ни одно объявление — это смена формата,
        # а не «кривые» данные. Молча вернуть пустой список значит тихо перестать работать.
        raise ParserResponseError(
            f"OLX: ни одно из {len(offers)} объявлений не разобрано — изменился формат ответа"
        )
    return listings


def _parse_offer(offer: Mapping[str, Any], marketplace: str) -> Listing:
    params = offer.get("params") or []
    price_param = next((p for p in params if p.get("key") == "price"), None)
    price_value = (price_param or {}).get("value") or {}

    location = offer.get("location") or {}
    city = (location.get("city") or {}).get("name")
    region = (location.get("region") or {}).get("name")

    promotion = offer.get("promotion") or {}
    return Listing(
        marketplace=marketplace,
        external_id=str(offer["id"]),
        url=str(offer["url"]),
        title=str(offer["title"]).strip(),
        price=_to_decimal(price_value.get("value")),
        currency=price_value.get("currency"),
        location=", ".join(part for part in (city, region) if part) or None,
        image_url=_first_photo(offer.get("photos") or []),
        published_at=_parse_datetime(offer.get("created_time")),
        attributes={
            "params": {
                p["key"]: {"name": p.get("name"), "value": (p.get("value") or {}).get("label")}
                for p in params
                if p.get("key") and p.get("key") != "price"
            },
            "price_label": price_value.get("label"),
            "negotiable": bool(price_value.get("negotiable")),
            "is_business": bool(offer.get("business")),
            "is_promoted": bool(promotion.get("top_ad")),
            "city": city,
            "region": region,
        },
    )


def _first_photo(photos: Sequence[Mapping[str, Any]]) -> str | None:
    if not photos:
        return None
    link = photos[0].get("link")
    if not link:
        return None
    width, height = IMAGE_SIZE.split("x")
    return str(link).replace("{width}", width).replace("{height}", height)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value))


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _format_number(value: Decimal) -> str:
    return format(value.normalize(), "f")
