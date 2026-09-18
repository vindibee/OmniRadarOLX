"""Справочник площадки: области, города и категории с человеческими названиями.

Пользователь выбирает «Киев» и «Электроника», а не числа: Mini App получает справочник
отсюда, присылает обратно выбранные id, а бэкенд проверяет, что такие в справочнике есть.
Сам файл собирается скриптом ``scripts/build_olx_catalog.py`` из данных OLX — выдумывать
id нельзя, они настоящие.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG_DIR = Path(__file__).resolve().parent / "data"


@dataclass(frozen=True, slots=True)
class CatalogItem:
    """Элемент справочника: id для площадки, названия — для человека."""

    id: int
    names: dict[str, str]
    children: tuple[CatalogItem, ...] = ()

    def name(self, language: str) -> str:
        return self.names.get(language) or next(iter(self.names.values()), str(self.id))


@dataclass(frozen=True, slots=True)
class Catalog:
    marketplace: str
    generated_at: str
    regions: tuple[CatalogItem, ...]
    categories: tuple[CatalogItem, ...]

    @property
    def region_ids(self) -> frozenset[int]:
        return frozenset(region.id for region in self.regions)

    @property
    def city_ids(self) -> frozenset[int]:
        return frozenset(city.id for region in self.regions for city in region.children)

    @property
    def category_ids(self) -> frozenset[int]:
        return frozenset(
            item.id for category in self.categories for item in (category, *category.children)
        )

    def to_json(self) -> dict[str, Any]:
        def dump(item: CatalogItem) -> dict[str, Any]:
            payload: dict[str, Any] = {"id": item.id, "names": item.names}
            if item.children:
                payload["children"] = [dump(child) for child in item.children]
            return payload

        return {
            "marketplace": self.marketplace,
            "generated_at": self.generated_at,
            "regions": [dump(region) for region in self.regions],
            "categories": [dump(category) for category in self.categories],
        }


def _item(payload: dict[str, Any], children_key: str = "children") -> CatalogItem:
    return CatalogItem(
        id=int(payload["id"]),
        names=dict(payload["names"]),
        children=tuple(_item(child) for child in payload.get(children_key, [])),
    )


@lru_cache(maxsize=4)
def load_catalog(marketplace: str) -> Catalog:
    """Справочник площадки. Читается один раз за процесс — файл неизменен во время работы."""
    path = CATALOG_DIR / f"{marketplace}_catalog.json"
    if not path.exists():
        # Площадка без справочника — не ошибка: просто без выпадающих списков.
        return Catalog(marketplace=marketplace, generated_at="", regions=(), categories=())
    payload = json.loads(path.read_text(encoding="utf-8"))
    regions = tuple(_item(region, "cities") for region in payload.get("regions", []))
    categories = tuple(_item(category) for category in payload.get("categories", []))
    return Catalog(
        marketplace=payload.get("marketplace", marketplace),
        generated_at=payload.get("generated_at", ""),
        regions=regions,
        categories=categories,
    )
