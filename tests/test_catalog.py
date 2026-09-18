"""Справочник площадки и точные ключевые слова: пользователь не видит ни одного сырого id."""

from decimal import Decimal

import pytest

from app.api.schemas import CriteriaIn, LocationIn
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
        category_id=1,
        locations=[LocationIn(kind="city", id=268, name="Київ")],
    ).to_domain()

    params = _parser().build_params(criteria, location=criteria.locations[0])

    assert params["query"] == "аксесуари для iphone"
    assert params["city_id"] == 268
    assert params["category_id"] == 1
    assert params["filter_enum_state[0]"] == "used"
    assert params["filter_float_price:to"] == "500"


def test_every_location_becomes_its_own_request() -> None:
    """OLX принимает одну локацию за запрос, поэтому мульти-выбор — это несколько обходов."""
    criteria = CriteriaIn(
        query="iphone",
        locations=[
            LocationIn(kind="city", id=268, name="Київ"),
            LocationIn(kind="region", id=5, name="Львівська область"),
        ],
    ).to_domain()
    parser = _parser()

    city_params = parser.build_params(criteria, location=criteria.locations[0])
    region_params = parser.build_params(criteria, location=criteria.locations[1])

    assert city_params["city_id"] == 268 and "region_id" not in city_params
    assert region_params["region_id"] == 5 and "city_id" not in region_params


def test_locations_outside_the_catalog_are_rejected() -> None:
    criteria = CriteriaIn(
        query="iphone", locations=[LocationIn(kind="city", id=99999999, name="Нигде")]
    ).to_domain()

    with pytest.raises(InvalidCriteriaError, match="справочник"):
        _parser().validate_criteria(criteria)


# ---------- Точные ключевые слова ----------


def test_minus_words_cut_the_noise() -> None:
    criteria = CriteriaIn(query="iphone 13", minus_words=["чохол", "скло"]).to_domain()

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
        locations=[LocationIn(kind="city", id=268, name="Київ")],
        minus_words=["чохол"],
        match_all_words=True,
        only_private=True,
        only_with_delivery=True,
    ).to_domain()

    restored = SearchCriteria.from_json(criteria.to_json())

    assert restored == criteria
    form = CriteriaIn.from_domain(restored)
    assert form.locations[0].id == 268, "форма показывается той же"
    assert form.only_private and form.only_with_delivery


def test_old_presets_still_open(  # минус-слова раньше назывались exclude_words
) -> None:
    restored = SearchCriteria.from_json(
        {"query": "iphone", "exclude_words": ["чохол"], "extra": {"city_id": 268}}
    )

    assert restored.minus_words == ("чохол",)
    assert CriteriaIn.from_domain(restored).locations[0].id == 268, "город переехал в locations"
