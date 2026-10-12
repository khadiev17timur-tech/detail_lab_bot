from aiogram.fsm.state import State, StatesGroup


class CalcStates(StatesGroup):
    service = State()
    car_class = State()
    addons = State()
    result = State()
