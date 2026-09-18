from collections.abc import Iterable, Sequence

from app.domain.entities import MarketplaceInfo
from app.domain.errors import UnknownMarketplaceError
from app.services.parsers.base import MarketplaceParser


class ParserRegistry:
    """Реестр парсеров по коду площадки. Создаётся в корне композиции, не синглтон."""

    def __init__(self, parsers: Iterable[MarketplaceParser] = ()) -> None:
        self._parsers: dict[str, MarketplaceParser] = {}
        for parser in parsers:
            self.register(parser)

    def register(self, parser: MarketplaceParser) -> None:
        if parser.code in self._parsers:
            raise ValueError(f"Парсер для '{parser.code}' уже зарегистрирован")
        self._parsers[parser.code] = parser

    def get(self, code: str) -> MarketplaceParser:
        try:
            return self._parsers[code]
        except KeyError:
            raise UnknownMarketplaceError(code) from None

    @property
    def codes(self) -> Sequence[str]:
        return list(self._parsers)

    def marketplaces(self) -> Sequence[MarketplaceInfo]:
        return [parser.info for parser in self._parsers.values()]

    async def aclose(self) -> None:
        for parser in self._parsers.values():
            await parser.aclose()
