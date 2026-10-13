from aiogram.fsm.state import State, StatesGroup


class RuleStates(StatesGroup):
    title = State()
    kind = State()
    value = State()
    trigger = State()
    threshold = State()
    valid_days = State()
    confirm = State()


class BlockStates(StatesGroup):
    dates = State()
    reason = State()
