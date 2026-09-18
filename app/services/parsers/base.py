from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime

from app.domain.entities import Listing, MarketplaceInfo, SearchCriteria
from app.domain.errors import InvalidCriteriaError


class MarketplaceParser(ABC):
    """Единый контракт для всех площадок.

    Парсер ничего не знает о Telegram и БД: получает критерии поиска,
    возвращает нормализованные ``Listing`` и сообщает о сбоях через ``ParserError``.
    """

    # Заполняются подклассом. Не ClassVar: обёртки вроде CachingParser проксируют их с экземпляра.
    code: str
    title: str

    @property
    def info(self) -> MarketplaceInfo:
        return MarketplaceInfo(code=self.code, title=self.title)

    @abstractmethod
    async def search(
        self, criteria: SearchCriteria, *, since: datetime | None = None
    ) -> Sequence[Listing]:
        """Возвращает свежие объявления (новые — первыми).

        :param since: граница интереса. Парсер дочитывает страницы выдачи, пока не дойдёт
            до объявлений старше ``since``; ``None`` — ограничиться первой страницей
            (первый прогон фильтра: история всё равно не отправляется, а помечается «виденной»).
        :raises ParserError: при сетевом сбое, блокировке или неожиданном формате ответа.
        """

    def validate_criteria(self, criteria: SearchCriteria) -> None:
        """Проверяет критерии до сохранения фильтра. Переопределяйте для специфики площадки."""
        if not criteria.query.strip():
            raise InvalidCriteriaError("Поисковый запрос не может быть пустым")
        for value in (criteria.price_min, criteria.price_max):
            if value is not None and value < 0:
                raise InvalidCriteriaError("Цена не может быть отрицательной")
        if (
            criteria.price_min is not None
            and criteria.price_max is not None
            and criteria.price_min > criteria.price_max
        ):
            raise InvalidCriteriaError("Минимальная цена больше максимальной")

    async def aclose(self) -> None:  # noqa: B027 — по умолчанию освобождать нечего
        """Освобождает ресурсы (HTTP-сессии)."""
