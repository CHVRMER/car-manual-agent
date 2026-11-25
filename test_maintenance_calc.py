"""아반떼 정답 CSV 기준으로 next_due를 확인한다."""
from datetime import date

from maintenance_calc import next_due


def _row(**kwargs):
    base = {
        "section": "통상",
        "item": "엔진 오일 및 오일 필터",
        "action": "교체",
        "schedule_type": "distance",
        "interval_km": None,
        "interval_months": None,
        "first_km": None,
        "first_months": None,
        "severe_conditions": None,
        "raw_text": "",
    }
    base.update(kwargs)
    return base


OIL = _row(interval_km=15000, interval_months=12, raw_text="매 15,000 km 또는 12개월마다 교체")
OIL_SEVERE = _row(section="가혹", interval_km=7500, interval_months=6, severe_conditions="1,2,3,4,5,6,7,8,9,10")
BRAKE = _row(item="브레이크 액", interval_km=50000, raw_text="매 50,000 km마다 교체")
FILTER = _row(item="공조 장치용 에어 필터", interval_months=12, raw_text="매 12개월마다 교체")
COOLANT = _row(
    item="냉각수량 점검 및 교체",
    interval_km=40000,
    interval_months=24,
    first_km=200000,
    first_months=120,
)
IVT = _row(item="IVT 변속기 오일", action="없음", schedule_type="none")


def test_basic_oil():
    got = next_due(OIL, date(2026, 8, 29), 30000, today=date(2026, 11, 25))
    assert got["next_date"] == date(2027, 8, 29)
    assert got["next_km"] == 45000
    assert got["overdue"] is False
    assert "15,000km 또는 12개월마다 교체" in got["rule"]
    assert "먼저 오는 시점" in got["rule"]
    assert got["message"] == ""


def test_month_end_severe_oil():
    got = next_due(OIL_SEVERE, date(2026, 8, 29), 30000, today=date(2026, 11, 25))
    assert got["next_date"] == date(2027, 2, 28)
    assert got["next_km"] == 37500


def test_km_only_brake():
    got = next_due(BRAKE, date(2026, 4, 15), 20000)
    assert got["next_date"] is None
    assert got["next_km"] == 70000


def test_months_only_filter():
    got = next_due(FILTER, date(2026, 11, 24), None)
    assert got["next_date"] == date(2027, 11, 24)
    assert got["next_km"] is None


def test_repeat_interval_after_first_coolant():
    got = next_due(COOLANT, date(2026, 1, 10), 210000)
    assert got["next_date"] == date(2028, 1, 10)
    assert got["next_km"] == 250000


def test_no_record():
    got = next_due(OIL, None, None)
    assert got["next_date"] is None
    assert got["next_km"] is None
    assert got["message"] == "기록이 없어 다음 시기를 계산할 수 없습니다"
    assert "15,000km" in got["rule"]


def test_no_record_includes_first_interval():
    got = next_due(COOLANT, None, None)
    assert got["next_date"] is None
    assert "최초 200,000km 또는 120개월" in got["rule"]
    assert "이후" in got["rule"]
    assert got["message"] == "기록이 없어 다음 시기를 계산할 수 없습니다"


def test_none_schedule_skips_calculation():
    got = next_due(IVT, date(2026, 1, 1), 10000)
    assert got["message"] == "점검·교체가 필요 없는 항목입니다"
    assert got["next_date"] is None
    assert got["next_km"] is None


def test_other_non_distance_messages():
    daily = next_due(_row(schedule_type="daily", action="점검"), None, None)
    condition = next_due(_row(schedule_type="condition_based"), None, None)
    assert daily["message"] == "일일 점검 항목입니다"
    assert condition["message"] == "상태에 따라 수시로 점검합니다"


def test_overdue_by_km():
    got = next_due(OIL, date(2026, 8, 29), 30000, current_km=46000, today=date(2026, 11, 25))
    assert got["overdue"] is True
    assert got["remaining_km"] == -1000


def test_january_31_plus_one_month():
    row = _row(interval_months=1, interval_km=None)
    got = next_due(row, date(2026, 1, 31), None)
    assert got["next_date"] == date(2026, 2, 28)


def test_leap_day():
    row = _row(interval_months=1, interval_km=None)
    got = next_due(row, date(2024, 1, 31), None)
    assert got["next_date"] == date(2024, 2, 29)
