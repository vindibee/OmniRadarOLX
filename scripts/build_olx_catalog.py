"""Собирает справочник областей, городов и категорий OLX.ua в JSON для Mini App.

Пользователь не должен видеть числовые ID, а выдумывать их нельзя — поэтому справочник
строится из самой площадки:

* **категории** — из состояния страницы (``window.__PRERENDERED_STATE__``), где лежит всё дерево;
* **области и города** — из выдачи ``/api/v1/offers/`` по каждой области: у объявления есть
  ``location.region`` и ``location.city`` с настоящими id и названиями.

Названия берутся на двух языках: украинская и русская версии сайта отдают их по своим адресам.

Запуск (обновить справочник):

    python scripts/build_olx_catalog.py
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from curl_cffi.requests import AsyncSession

OUTPUT = Path(__file__).resolve().parents[1] / "app/services/parsers/data/olx_ua_catalog.json"
API = "https://www.olx.ua/api/v1/offers/"
PAGES_PER_REGION = 3
PAGE_SIZE = 40
REGION_ID_RANGE = range(1, 30)
# Сколько городов оставляем в области: самые «населённые» объявлениями, остальные — шум.
CITIES_PER_REGION = 25
REQUEST_PAUSE_SECONDS = 0.7
LANGUAGES = {"uk": "https://www.olx.ua/uk/", "ru": "https://www.olx.ua/ru/"}
STATE_RE = re.compile(r'window\.__PRERENDERED_STATE__\s*=\s*"(.*?)";\s*\n', re.S)


def _session(language: str) -> AsyncSession:
    return AsyncSession(
        impersonate="chrome",
        timeout=30,
        headers={
            "Accept-Language": f"{language}-UA,{language};q=0.9",
            "Referer": "https://www.olx.ua/",
        },
        # Язык страницы OLX выбирает по cookie, а не по адресу: без неё дерево категорий
        # приезжает украинским даже с /ru/.
        cookies={"lang": language},
    )


async def fetch_category_tree(language: str) -> dict[int, dict[str, Any]]:
    async with _session(language) as session:
        response = await session.get(LANGUAGES[language])
        match = STATE_RE.search(response.text)
        if match is None:
            raise RuntimeError("OLX: на странице нет __PRERENDERED_STATE__ — формат изменился")
        state = json.loads(json.loads('"' + match.group(1) + '"'))
    return {int(key): value for key, value in state["categories"]["list"].items()}


async def fetch_geo(language: str) -> tuple[dict[int, str], dict[int, dict[str, Any]], Counter[int]]:
    """Области и города по выдаче: настоящие id, названия и частота встречаемости."""
    regions: dict[int, str] = {}
    cities: dict[int, dict[str, Any]] = {}
    hits: Counter[int] = Counter()

    async with _session(language) as session:
        for region_id in REGION_ID_RANGE:
            for page in range(PAGES_PER_REGION):
                response = await session.get(
                    API,
                    params={
                        "offset": page * PAGE_SIZE,
                        "limit": PAGE_SIZE,
                        "region_id": region_id,
                        "sort_by": "created_at:desc",
                    },
                    headers={"Accept": "application/json"},
                )
                if response.status_code != 200:
                    break
                offers = response.json().get("data") or []
                if not offers:
                    break
                for offer in offers:
                    location = offer.get("location") or {}
                    region, city = location.get("region") or {}, location.get("city") or {}
                    if region.get("id") and region.get("name"):
                        regions[int(region["id"])] = region["name"]
                    if city.get("id") and city.get("name") and region.get("id"):
                        city_id = int(city["id"])
                        cities[city_id] = {
                            "id": city_id,
                            "name": city["name"],
                            "region_id": int(region["id"]),
                        }
                        hits[city_id] += 1
                await asyncio.sleep(REQUEST_PAUSE_SECONDS)
            print(f"  область {region_id}: {regions.get(region_id, '—')}")
    return regions, cities, hits


def build(
    geo: dict[str, tuple[dict[int, str], dict[int, dict[str, Any]], Counter[int]]],
    categories: dict[str, dict[int, dict[str, Any]]],
) -> dict[str, Any]:
    uk_regions, uk_cities, hits = geo["uk"]
    ru_regions, ru_cities, _ = geo["ru"]

    regions: list[dict[str, Any]] = []
    for region_id, uk_name in sorted(uk_regions.items(), key=lambda item: item[1]):
        region_cities = [city for city in uk_cities.values() if city["region_id"] == region_id]
        region_cities.sort(key=lambda city: (-hits[city["id"]], city["name"]))
        regions.append(
            {
                "id": region_id,
                "names": {"uk": uk_name, "ru": ru_regions.get(region_id, uk_name)},
                "cities": [
                    {
                        "id": city["id"],
                        "names": {
                            "uk": city["name"],
                            "ru": (ru_cities.get(city["id"]) or city)["name"],
                        },
                    }
                    for city in region_cities[:CITIES_PER_REGION]
                ],
            }
        )

    uk_tree, ru_tree = categories["uk"], categories["ru"]
    roots = [item for item in uk_tree.values() if item.get("parentId") == 0]
    roots.sort(key=lambda item: item.get("displayOrder", 0))
    category_list = [
        {
            "id": root["id"],
            "names": {"uk": root["name"], "ru": ru_tree.get(root["id"], root)["name"]},
            "children": [
                {
                    "id": child_id,
                    "names": {
                        "uk": uk_tree[child_id]["name"],
                        "ru": ru_tree.get(child_id, uk_tree[child_id])["name"],
                    },
                }
                for child_id in root.get("children", [])
                if child_id in uk_tree
            ],
        }
        for root in roots
    ]

    return {
        "marketplace": "olx_ua",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "regions": regions,
        "categories": category_list,
    }


async def main() -> None:
    geo = {}
    categories = {}
    for language in LANGUAGES:
        print(f"Язык {language}: категории…")
        categories[language] = await fetch_category_tree(language)
        print(f"Язык {language}: области и города…")
        geo[language] = await fetch_geo(language)

    catalog = build(geo, categories)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    cities = sum(len(region["cities"]) for region in catalog["regions"])
    print(
        f"\nГотово: {len(catalog['regions'])} областей, {cities} городов, "
        f"{len(catalog['categories'])} категорий → {OUTPUT}"
    )


if __name__ == "__main__":
    asyncio.run(main())
