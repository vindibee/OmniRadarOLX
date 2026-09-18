from aiogram.fsm.state import State, StatesGroup


class CreateFilter(StatesGroup):
    marketplace = State()
    query = State()
    price_min = State()
    price_max = State()
    confirm = State()
