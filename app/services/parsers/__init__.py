"""Парсеры маркетплейсов.

Чтобы добавить площадку: унаследуйтесь от ``MarketplaceParser``, реализуйте ``search``
и зарегистрируйте фабрику в ``app.main.PARSER_FACTORIES``. Ядро (сервисы, БД, бот) менять не нужно.
"""

from app.services.parsers.base import MarketplaceParser
from app.services.parsers.errors import (
    ParserBlockedError,
    ParserError,
    ParserNetworkError,
    ParserResponseError,
)
from app.services.parsers.registry import ParserRegistry

__all__ = [
    "MarketplaceParser",
    "ParserBlockedError",
    "ParserError",
    "ParserNetworkError",
    "ParserRegistry",
    "ParserResponseError",
]
