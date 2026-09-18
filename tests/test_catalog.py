"""Справочник площадки и точные ключевые слова: пользователь не видит ни одного сырого id."""

from decimal import Decimal

import pytest

from app.api.schemas import CriteriaIn
from app.domain.entities import Listing, SearchCriteria
from app.domain.errors import InvalidCriteriaError
from app.services.parsers.catalog import load_catalog
from app.services.parsers.http_client import HttpClient, HttpClientOptions
from app.services.parsers.olx_ua import OlxUaParser

CATALOG = load_catalog("olx_ua")


def _parser() -> OlxUaParser:
    return OlxUaParser(HttpClient(HttpClientOptions()), page_size=10, catalog=CATALOG)


def listing(title: str) -> Listing:
    return Listing(marketplace="olx_ua", external_id="1", url="https://olx.ua/1", title=title)


# ---------- Справочник ----------


def test_catalog_has_real_regions_cities_and_categories() -> None:
    assert len(CATALOG.regions) >= 20, "в Украине 24 области плюс Крым"
    assert len(CATALOG.categories) >= 10

    kyiv_region = next(r for r in CATALOG.regions if "Київ" in r.name("uk"))
    kyiv = next(city for city in kyiv_region.children if city.name("uk") == "Київ")
    assert kyiv.id == 268, "id Киева на OLX"
    assert kyiv.name("ru") == "Киев", "названия есть на обоих языках"


def test_catalog_categories_are_human_readable() -> None:
    names_ru = [category.name("ru") for category in CATALOG.categories]

    assert "Недвижимость" in names_ru
    assert "Электроника" in names_ru
    electronics = next(c for c in CATALOG.categories if c.name("ru") == "Электроника")
    assert electronics.children, "у категории есть подкатегории для второго списка"


def test_unknown_language_falls_back_instead_of_showing_an_id() -> None:
    region = CATALOG.regions[0]

    assert region.name("en") == region.names["uk"]


# ---------- Проверка выбора ----------


def test_city_and_category_from_the_catalog_are_accepted() -> None:
    criteria = SearchCriteria(query="iphone", extra={"city_id": 268, "category_id": 1})

    _parser().validate_criteria(criteria)  # не должно бросить


@pytest.mark.parametrize(
    "extra",
    [{"city_id": 99999999}, {"region_id": 12345}, {"category_id": 987654}],
)
def test_ids_outside_the_catalog_are_rejected(extra: dict[str, int]) -> None:
    with pytest.raises(InvalidCriteriaError, match="справочник"):
        _parser().validate_criteria(SearchCriteria(query="iphone", extra=extra))


def test_selected_values_become_olx_query_parameters() -> None:
    criteria = CriteriaIn(
        query="аксесуари для iphone",
        price_max=Decimal(500),
        condition="used",
        city_id=268,
        category_id=1,
    ).to_domain()

    params = _parser().build_params(criteria)

    assert params["query"] == "аксесуари для iphone"
    assert params["city_id"] == 268
    assert params["category_id"] == 1
    assert params["filter_enum_state[0]"] == "used"
    assert params["filter_float_price:to"] == "500"


def test_city_wins_over_region_in_the_form() -> None:
    """В Mini App выбран город — область в запрос не идёт, иначе поиск шире, чем просили."""
    criteria = CriteriaIn(query="iphone", region_id=25, city_id=268).to_domain()

    assert criteria.extra["city_id"] == 268
    assert criteria.extra["region_id"] == 25, "хранится оба, в запрос парсер берёт точный город"


# ---------- Точные ключевые слова ----------


def test_excluded_words_cut_the_noise() -> None:
    criteria = CriteriaIn(query="iphone 13", exclude_words=["чохол", "скло"]).to_domain()

    assert criteria.matches(listing("iPhone 13 128GB")) is True
    assert criteria.matches(listing("Чохол на iPhone 13")) is False
    assert criteria.matches(listing("Захисне СКЛО iPhone 13")) is False, "регистр не важен"


def test_all_words_mode_demands_the_exact_phrase_parts() -> None:
    criteria = CriteriaIn(query="аксесуари для iphone", match_all_words=True).to_domain()

    assert criteria.matches(listing("Аксесуари для iPhone 13")) is True
    assert criteria.matches(listing("iPhone 13 128GB")) is False, "нет слова «аксесуари»"


def test_without_the_strict_mode_nothing_is_filtered() -> None:
    criteria = CriteriaIn(query="аксесуари для iphone").to_domain()

    assert criteria.matches(listing("iPhone 13 128GB")) is True


def test_criteria_survive_the_database_round_trip() -> None:
    criteria = CriteriaIn(
        query="iphone 13",
        price_min=Decimal(5000),
        condition="new",
        city_id=268,
        exclude_words=["чохол"],
        match_all_words=True,
    ).to_domain()

    restored = SearchCriteria.from_json(criteria.to_json())

    assert restored == criteria
    assert CriteriaIn.from_domain(restored).city_id == 268, "форма показывается той же"
