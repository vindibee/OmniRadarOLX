import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.domain.entities import SearchCriteria
from app.domain.errors import InvalidCriteriaError
from app.services.parsers.errors import ParserResponseError
from app.services.parsers.http_client import HttpClient, HttpClientOptions
from app.services.parsers.olx_ua import OlxUaParser, parse_offers

FIXTURE = Path(__file__).parent / "fixtures" / "olx_offers.json"


@pytest.fixture
def payload() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_parse_offers_normalizes_listing(payload: dict[str, Any]) -> None:
    listings = parse_offers(payload)

    assert len(listings) == 2, "битое объявление должно быть пропущено, а не ронять парсинг"
    first = listings[0]
    assert first.marketplace == "olx_ua"
    assert first.external_id == "900000001"
    assert first.title == "iPhone 13 128GB"
    assert first.price == Decimal("15500")
    assert first.currency == "UAH"
    assert first.location == "Київ, Київська область"
    assert (
        first.image_url == "https://ireland.apollo.olxcdn.com:443/v1/files/abc-UA/image;s=800x600"
    )
    assert first.published_at == datetime(2026, 9, 16, 23, 40, tzinfo=timezone(timedelta(hours=3)))
    assert first.attributes["params"] == {"state": {"name": "Стан", "value": "Вживане"}}
    assert first.attributes["negotiable"] is True


def test_parse_offers_handles_missing_optional_fields(payload: dict[str, Any]) -> None:
    promoted = parse_offers(payload)[1]

    assert promoted.price is None
    assert promoted.location is None
    assert promoted.image_url is None
    assert promoted.attributes["is_promoted"] is True


@pytest.mark.parametrize("bad_payload", [None, [], {"data": None}, {"error": "blocked"}])
def test_parse_offers_rejects_unexpected_format(bad_payload: Any) -> None:
    with pytest.raises(ParserResponseError):
        parse_offers(bad_payload)


def _parser() -> OlxUaParser:
    return OlxUaParser(HttpClient(HttpClientOptions()), page_size=10)


def test_build_params() -> None:
    criteria = SearchCriteria(
        query="  iphone 13 ",
        price_min=Decimal("5000.00"),
        price_max=Decimal("20000"),
        extra={"city_id": 268},
    )

    params = _parser().build_params(criteria)

    assert params == {
        "offset": 0,
        "limit": 10,
        "query": "iphone 13",
        "sort_by": "created_at:desc",
        "currency": "UAH",
        "filter_float_price:from": "5000",
        "filter_float_price:to": "20000",
        "city_id": 268,
    }


@pytest.mark.parametrize(
    "criteria",
    [
        SearchCriteria(query="   "),
        SearchCriteria(query="x", price_min=Decimal(10), price_max=Decimal(5)),
        SearchCriteria(query="x", price_min=Decimal(-1)),
        SearchCriteria(query="x", extra={"hack": 1}),
    ],
)
def test_validate_criteria_rejects_invalid(criteria: SearchCriteria) -> None:
    with pytest.raises(InvalidCriteriaError):
        _parser().validate_criteria(criteria)


def _offer(offer_id: int, *, age: timedelta = timedelta(0)) -> dict[str, Any]:
    created = datetime.now(UTC) - age
    return {
        "id": offer_id,
        "url": f"https://www.olx.ua/d/obyavlenie/{offer_id}",
        "title": f"Лот {offer_id}",
        "created_time": created.isoformat(),
        "params": [],
        "photos": [],
    }


class FakeHttp(HttpClient):
    """Подменяет сеть: отдаёт заранее подготовленные страницы выдачи."""

    def __init__(self, pages: list[list[dict[str, Any]]]) -> None:
        super().__init__(HttpClientOptions(min_request_interval_seconds=0))
        self._pages = pages
        self.requests: list[dict[str, Any]] = []

    async def get_json(
        self,
        url: str,
        *,
        params: Any = None,
        headers: Any = None,
    ) -> Any:
        query = dict(params or {})
        self.requests.append(query)
        index = int(query["offset"]) // int(query["limit"])
        return {"data": self._pages[index] if index < len(self._pages) else []}


def _paginating_parser(pages: list[list[dict[str, Any]]], *, max_pages: int = 5) -> OlxUaParser:
    return OlxUaParser(FakeHttp(pages), page_size=2, max_pages=max_pages)


async def test_search_without_since_reads_one_page() -> None:
    parser = _paginating_parser([[_offer(1), _offer(2)], [_offer(3), _offer(4)]])

    listings = await parser.search(SearchCriteria(query="iphone"))

    assert [item.external_id for item in listings] == ["1", "2"]
    assert len(parser._http.requests) == 1  # type: ignore[attr-defined]


async def test_search_pages_until_listings_are_older_than_since() -> None:
    parser = _paginating_parser(
        [
            [_offer(1), _offer(2, age=timedelta(minutes=5))],
            [_offer(3, age=timedelta(minutes=10)), _offer(4, age=timedelta(hours=3))],
            [_offer(5, age=timedelta(days=1)), _offer(6, age=timedelta(days=2))],
        ]
    )

    listings = await parser.search(
        SearchCriteria(query="iphone"), since=datetime.now(UTC) - timedelta(hours=1)
    )

    assert [item.external_id for item in listings] == ["1", "2", "3", "4"]
    offsets = [request["offset"] for request in parser._http.requests]  # type: ignore[attr-defined]
    assert offsets == [0, 2], "третья страница уже за границей интереса"


async def test_search_stops_on_incomplete_page() -> None:
    parser = _paginating_parser([[_offer(1)], [_offer(2)]])

    listings = await parser.search(
        SearchCriteria(query="iphone"), since=datetime(2000, 1, 1, tzinfo=UTC)
    )

    assert [item.external_id for item in listings] == ["1"]
    assert len(parser._http.requests) == 1  # type: ignore[attr-defined]


async def test_search_respects_max_pages_and_deduplicates() -> None:
    page = [_offer(1), _offer(2)]
    parser = _paginating_parser([page, page, page, page], max_pages=3)

    listings = await parser.search(
        SearchCriteria(query="iphone"), since=datetime(2000, 1, 1, tzinfo=UTC)
    )

    assert [item.external_id for item in listings] == ["1", "2"], "повторы между страницами"
    assert len(parser._http.requests) == 3  # type: ignore[attr-defined]


def test_parse_offers_rejects_response_it_cannot_parse_at_all() -> None:
    """Смена формата API — это ошибка, а не «пустая выдача»: иначе бот тихо перестанет работать."""
    with pytest.raises(ParserResponseError, match="формат"):
        parse_offers({"data": [{"id": 1}, {"id": 2}]})


def test_condition_from_the_form_becomes_an_olx_filter() -> None:
    criteria = SearchCriteria(query="iphone", extra={"state": "used", "city_id": 268})

    params = _parser().build_params(criteria)

    assert params["filter_enum_state[0]"] == "used"
    assert params["city_id"] == 268


def test_unknown_condition_is_rejected_before_it_reaches_olx() -> None:
    with pytest.raises(InvalidCriteriaError):
        _parser().validate_criteria(SearchCriteria(query="iphone", extra={"state": "broken"}))
