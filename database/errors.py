class SlotTaken(Exception):
    """Выбранное время уже занято (или не помещается в расписание)."""


class VoucherUnavailable(Exception):
    """Купон недоступен: уже использован, просрочен, чужой или отозван."""


class NotAllowed(Exception):
    """Операция недопустима для текущего состояния записи."""
