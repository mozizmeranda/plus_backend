from datetime import datetime
from typing import List

from database.database import db, tashkent_tz, slot_start_tashkent


async def check_booking_access(telegram_id: int, booking_id: str, old_slots: List[str]) -> int:
    """
    Можно ли переносить указанные слоты брони. Коды: 1 — да, 0 — чужая/не найдена,
    -1 — ошибка сервера, -2 — до одного из слотов меньше 6 часов,
    -3 — среди слотов есть не принадлежащий брони.
    """
    return await db.check_access_for_reschedule(telegram_id, booking_id, old_slots)


def is_slot_in_future(date_str: str, time_slot: str) -> bool:
    """Начался ли слот уже (сравнение по Ташкенту, а не по времени сервера)."""
    return slot_start_tashkent(date_str, time_slot) > datetime.now(tashkent_tz)
