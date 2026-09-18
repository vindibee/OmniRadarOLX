class DomainError(Exception):
    """Базовая ошибка бизнес-логики. Сообщение безопасно показывать пользователю."""


class UnknownMarketplaceError(DomainError):
    def __init__(self, code: str) -> None:
        super().__init__(f"Площадка «{code}» не поддерживается")
        self.code = code


class InvalidCriteriaError(DomainError):
    pass


class SubscriptionLimitExceededError(DomainError):
    def __init__(self, limit: int) -> None:
        super().__init__(f"Достигнут лимит фильтров: {limit}")
        self.limit = limit


class SubscriptionNotFoundError(DomainError):
    def __init__(self, subscription_id: int) -> None:
        super().__init__("Фильтр не найден")
        self.subscription_id = subscription_id


class NotificationError(Exception):
    """Временная ошибка доставки уведомления — можно повторить позже."""


class RecipientUnavailableError(NotificationError):
    """Получатель недоступен навсегда (заблокировал бота, удалил чат)."""
