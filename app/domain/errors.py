class DomainError(Exception):
    """Базовая ошибка бизнес-логики. Сообщение безопасно показывать пользователю."""


class UnknownMarketplaceError(DomainError):
    def __init__(self, code: str) -> None:
        super().__init__(f"Площадка «{code}» не поддерживается")
        self.code = code


class InvalidCriteriaError(DomainError):
    pass


class FilterLimitExceededError(DomainError):
    def __init__(self, limit: int) -> None:
        super().__init__(f"Достигнут лимит фильтров: {limit}")
        self.limit = limit


class FilterNotFoundError(DomainError):
    def __init__(self, filter_id: int) -> None:
        super().__init__("Фильтр не найден")
        self.filter_id = filter_id


class TrialAlreadyUsedError(DomainError):
    def __init__(self) -> None:
        super().__init__("Демо-доступ уже использован")


class SubscriptionRequiredError(DomainError):
    def __init__(self) -> None:
        super().__init__("Нужна активная подписка")


class PresetLimitExceededError(DomainError):
    def __init__(self, limit: int) -> None:
        super().__init__(f"Достигнут лимит пресетов: {limit}")
        self.limit = limit


class PresetNotFoundError(DomainError):
    def __init__(self, preset_id: int) -> None:
        super().__init__("Пресет не найден")
        self.preset_id = preset_id


class NotificationError(Exception):
    """Временная ошибка доставки уведомления — можно повторить позже."""


class RecipientUnavailableError(NotificationError):
    """Получатель недоступен навсегда (заблокировал бота, удалил чат)."""
