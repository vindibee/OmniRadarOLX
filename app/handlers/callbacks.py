from enum import StrEnum

from aiogram.filters.callback_data import CallbackData


class MenuAction(StrEnum):
    NEW_FILTER = "new"
    MY_FILTERS = "list"
    HELP = "help"
    MAIN = "main"


class MenuCallback(CallbackData, prefix="menu"):
    action: MenuAction


class MarketplaceCallback(CallbackData, prefix="mp"):
    code: str


class SkipCallback(CallbackData, prefix="skip"):
    pass


class ConfirmAction(StrEnum):
    SAVE = "save"
    CANCEL = "cancel"


class ConfirmCallback(CallbackData, prefix="confirm"):
    action: ConfirmAction


class SubscriptionAction(StrEnum):
    TOGGLE = "toggle"
    DELETE = "delete"


class SubscriptionCallback(CallbackData, prefix="sub"):
    action: SubscriptionAction
    subscription_id: int
