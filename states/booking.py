from aiogram.fsm.state import State, StatesGroup


class BookingStates(StatesGroup):
    service = State()
    car = State()
    car_class = State()
    date = State()
    time = State()
    name = State()
    phone = State()
    comment = State()
    confirm = State()
