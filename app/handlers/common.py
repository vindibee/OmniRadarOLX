from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.handlers import keyboards
from app.handlers.callbacks import MenuAction, MenuCallback
from app.services.subscriptions import SubscriptionService

router = Router(name="common")

WELCOME_TEXT = (
    "👋 Привет! Я слежу за новыми объявлениями и присылаю их сюда.\n\n"
    "Создайте фильтр — и я сообщу, как только появится подходящее предложение."
)
HELP_TEXT = (
    "<b>Как это работает</b>\n"
    "1. Нажмите «➕ Новый фильтр», выберите площадку и введите запрос.\n"
    "2. При желании укажите диапазон цен.\n"
    "3. Бот регулярно проверяет площадку и присылает только новые объявления.\n\n"
    "Команды: /start — меню, /filters — мои фильтры, /cancel — отменить ввод."
)


@router.message(CommandStart())
async def cmd_start(
    message: Message, state: FSMContext, subscription_service: SubscriptionService
) -> None:
    await state.clear()
    if message.from_user:
        await subscription_service.register_user(
            user_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
        )
    await message.answer(WELCOME_TEXT, reply_markup=keyboards.main_menu())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=keyboards.back_to_menu())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=keyboards.main_menu())


@router.callback_query(MenuCallback.filter(F.action == MenuAction.MAIN))
async def on_main_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.edit_text(WELCOME_TEXT, reply_markup=keyboards.main_menu())
    await callback.answer()


@router.callback_query(MenuCallback.filter(F.action == MenuAction.HELP))
async def on_help(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(HELP_TEXT, reply_markup=keyboards.back_to_menu())
    await callback.answer()


@router.message(StateFilter(None))
async def fallback(message: Message) -> None:
    await message.answer("Не понял команду. Откройте меню:", reply_markup=keyboards.main_menu())
