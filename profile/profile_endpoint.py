from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from database.database import db
from config import models
from config.config import TIME_SLOTS
from .helpers import check_booking_access, is_slot_in_future

profile_router = APIRouter(prefix="/profile", tags=["Profile"])


@profile_router.get("/{telegram_id}")
async def get_profile(telegram_id: int):
    """
    Получение профиля пользователя и всех его бронирований.
    """
    rows = await db.get_full_profile(telegram_id)

    if not rows:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "Пользователь не найден"}
        )

    result = {
        "name": rows['profile'][0],
        "phone": rows['profile'][1],
        "bookings": []
    }

    for row in rows["future_bookings"]:
        result["bookings"].append({
                "date": row[1],
                "location": row[0],
                "telegram_id": telegram_id,
                "time_slot": row[2],
                "booking_id": row[3]
            })

    return JSONResponse(status_code=status.HTTP_200_OK, content=result)


@profile_router.put("/{telegram_id}")
async def update_profile(telegram_id: int, body: models.UpdateProfileBody):
    """
    Изменение имени и номера телефона пользователя.
    """
    # Проверяем, существует ли пользователь (опираясь на то, что если нет профиля - нет юзера)
    rows = await db.get_full_profile(telegram_id)
    if not rows:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "Пользователь не найден"}
        )

    try:
        await db.update_user_profile(telegram_id, body.name, body.phone)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "success", "message": "Профиль успешно обновлен"}
        )
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": f"Ошибка при обновлении профиля: {str(e)}"}
        )


def _error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


@profile_router.post("/reschedule_booking")
async def reschedule_booking(body: models.RescheduleBooking):
    """
    Перенос одного или нескольких слотов брони на новую дату. Всё время — по Ташкенту.

    Правила:
      - бронь должна принадлежать пользователю;
      - переносить можно только слоты, которые есть в брони; остальные остаются на месте;
      - до начала каждого переносимого слота должно оставаться не менее 6 часов;
      - новые слоты должны быть из сетки TIME_SLOTS, в будущем и свободны;
      - цена нового слота должна совпадать с ценой старого.
    """
    # 1. Валидация запроса
    old_slots = [move.old_slot for move in body.moves]
    new_slots = [move.new_slot for move in body.moves]
    new_date = body.new_date.isoformat()

    if len(set(old_slots)) != len(old_slots) or len(set(new_slots)) != len(new_slots):
        return _error(status.HTTP_400_BAD_REQUEST, "Слоты в moves не должны повторяться")

    unknown_slots = [slot for slot in new_slots if slot not in TIME_SLOTS]
    if unknown_slots:
        return _error(status.HTTP_400_BAD_REQUEST, f"Неизвестные слоты: {unknown_slots}")

    if not all(is_slot_in_future(new_date, slot) for slot in new_slots):
        return _error(status.HTTP_400_BAD_REQUEST, "Нельзя перенести бронь на время в прошлом")

    # 2. Доступ и ограничение по времени (только для переносимых слотов)
    access = await check_booking_access(body.telegram_id, body.booking_id, old_slots)
    match access:
        case 0:
            return _error(status.HTTP_403_FORBIDDEN, "У вас нет доступа к этому бронированию")
        case -1:
            return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "Ошибка сервера")
        case -2:
            return _error(status.HTTP_400_BAD_REQUEST, "Перенос возможен не позднее чем за 6 часов до игры")
        case -3:
            return _error(status.HTTP_400_BAD_REQUEST, "Среди переносимых слотов есть слот, которого нет в брони")

    # 3. Атомарный перенос (проверка цены и доступности + изменение bookings и single_events)
    try:
        result = await db.reschedule_booking(
            telegram_id=body.telegram_id,
            booking_id=body.booking_id,
            new_date=new_date,
            moves={move.old_slot: move.new_slot for move in body.moves},
        )
    except Exception as e:
        return _error(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Ошибка при переносе брони: {str(e)}")

    match result["status"]:
        case "ok":
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"status": "success", "message": "Бронь успешно перенесена"}
            )
        case "occupied":
            return _error(status.HTTP_409_CONFLICT, f"Слот {result['slot']} уже занят")
        case "price_mismatch":
            return _error(
                status.HTTP_409_CONFLICT,
                f"Цена слота {result['slot']} ({result['new_price']}) не совпадает с ценой "
                f"текущего слота ({result['old_price']})"
            )
        case "duplicate_slot":
            return _error(status.HTTP_400_BAD_REQUEST, f"Слот {result['slot']} уже есть в этой брони")
        case "unknown_old_slot":
            return _error(status.HTTP_400_BAD_REQUEST, f"Слота {result['slot']} нет в брони")
        case "old_booking":
            return _error(status.HTTP_400_BAD_REQUEST, f"Перенос можно делать только для новых броней")
        case _:  # not_found
            return _error(status.HTTP_403_FORBIDDEN, "У вас нет доступа к этому бронированию")
