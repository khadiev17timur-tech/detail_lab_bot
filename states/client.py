from aiogram.fsm.state import State, StatesGroup


class ReviewStates(StatesGroup):
    comment = State()


class CarStates(StatesGroup):
    title = State()
    car_class = State()
