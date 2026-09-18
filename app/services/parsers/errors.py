class ParserError(Exception):
    """Базовая ошибка парсера. Мониторинг ловит её и переходит к следующему фильтру."""


class ParserNetworkError(ParserError):
    """Сеть недоступна, тайм-аут или сервер отвечает 5xx — после всех повторов."""


class ParserBlockedError(ParserError):
    """Площадка блокирует запросы (403, капча, Cloudflare challenge, 429 без восстановления)."""


class ParserResponseError(ParserError):
    """Ответ получен, но его формат не соответствует ожидаемому."""
