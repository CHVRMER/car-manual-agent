"""다음 정비 시점 계산. DB와 모델 없이 maintenance_info가 호출한다."""
import calendar
from datetime import date


def add_months(day: date, months: int) -> date:
    """개월을 더한다. 대상 달에 같은 일이 없으면 그 달의 말일을 쓴다."""
    index = day.month - 1 + months
    year = day.year + index // 12
    month = index % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last))


def _as_int(value):
    if value is None or value == "":
        return None
    return int(value)


def _fmt_km(km: int) -> str:
    return f"{km:,}km"


def _interval_phrase(km, months, action: str) -> str:
    if km is not None and months is not None:
        return f"{_fmt_km(km)} 또는 {months}개월마다 {action}. 먼저 오는 시점"
    if km is not None:
        return f"{_fmt_km(km)}마다 {action}"
    if months is not None:
        return f"{months}개월마다 {action}"
    return ""


def _rule(row: dict, include_first: bool) -> str:
    action = row.get("action") or "점검"
    repeat = _interval_phrase(
        _as_int(row.get("interval_km")),
        _as_int(row.get("interval_months")),
        action,
    )
    if not include_first:
        return repeat
    first = _interval_phrase(
        _as_int(row.get("first_km")),
        _as_int(row.get("first_months")),
        action,
    )
    if first and repeat:
        first_core = first.split("마다")[0]
        return f"최초 {first_core}, 이후 {repeat}"
    return repeat or first


def next_due(row: dict, last_date: date | None, last_km: int | None,
             current_km: int | None = None, today: date | None = None) -> dict:
    """점검표 한 행과 마지막 정비 기록으로 다음 시점을 계산한다.

    schedule_type이 distance가 아니면 날짜와 주행거리를 계산하지 않고 message만 채운다.
    기록이 없으면 반복 주기를 rule로만 알리고, 다음 시점은 비운다.
    """
    today = today or date.today()
    result = {
        "item": row.get("item"),
        "section": row.get("section"),
        "action": row.get("action"),
        "rule": "",
        "next_date": None,
        "next_km": None,
        "overdue": False,
        "remaining_km": None,
        "message": "",
    }
    schedule_type = row.get("schedule_type")
    if schedule_type != "distance":
        result["message"] = {
            "daily": "일일 점검 항목입니다",
            "condition_based": "상태에 따라 수시로 점검합니다",
            "none": "점검·교체가 필요 없는 항목입니다",
        }.get(schedule_type, "주기를 계산할 수 없는 항목입니다")
        return result

    no_record = last_date is None and last_km is None
    result["rule"] = _rule(row, include_first=no_record)
    if no_record:
        result["message"] = "기록이 없어 다음 시기를 계산할 수 없습니다"
        return result

    interval_months = _as_int(row.get("interval_months"))
    interval_km = _as_int(row.get("interval_km"))
    if last_date is not None and interval_months is not None:
        result["next_date"] = add_months(last_date, interval_months)
    if last_km is not None and interval_km is not None:
        result["next_km"] = last_km + interval_km

    if result["next_date"] is not None and today >= result["next_date"]:
        result["overdue"] = True
    if result["next_km"] is not None and current_km is not None and current_km >= result["next_km"]:
        result["overdue"] = True
    if current_km is not None and result["next_km"] is not None:
        result["remaining_km"] = result["next_km"] - current_km
    return result
