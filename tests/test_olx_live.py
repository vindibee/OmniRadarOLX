"""Живой smoke-тест: сверяет реальный ответ OLX с тем, что ожидает парсер.

API ``/api/v1/offers/`` не документирован и может измениться в любой момент — фикстура этого
не поймает. Тест ходит в сеть, поэтому по умолчанию не запускается:

    pytest -m live
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.entities import SearchCriteria
from app.services.parsers.errors import ParserError
from app.services.parsers.http_client import HttpClient, HttpClientOptions
from app.services.parsers.olx_ua import DEFAULT_HEADERS, OlxUaParser

pytestmark = pytest.mark.live


async def test_real_search_matches_the_expected_format() -> None:
    parser = OlxUaParser(
        HttpClient(HttpClientOptions(), default_headers=DEFAULT_HEADERS), page_size=40, max_pages=2
    )
    try:
        listings = await parser.search(
            SearchCriteria(query="iphone"), since=datetime.now(UTC) - timedelta(hours=6)
        )
    except ParserError as exc:
        pytest.skip(f"OLX недоступен или блокирует запрос: {exc}")
    finally:
        await parser.aclose()

    assert listings, "по запросу 'iphone' на OLX всегда что-то есть"
    assert len({item.external_id for item in listings}) == len(listings), "дубли между страницами"
    for item in listings:
        assert item.external_id.isdigit()
        assert item.url.startswith("https://")
        assert item.title
        assert item.published_at is not None and item.published_at.tzinfo is not None
    assert any(item.price is not None for item in listings), "цены перестали разбираться"
