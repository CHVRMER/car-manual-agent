"""AC-7 점검표 읽기 정확도: 추출 결과와 정답 CSV를 행 단위로 비교."""
import csv
import re
from collections import Counter

KEY = ("section", "item", "applies_to", "action")
VALUES = ("schedule_type", "interval_km", "interval_months", "first_km", "first_months", "severe_conditions")


def _norm_text(s) -> str:
    return re.sub(r"\s", "", str(s or ""))


def _norm_num(s) -> str:
    s = str(s or "").strip()
    return str(int(float(s))) if s not in ("", "None", "nan") else ""


def _norm_sev(s) -> str:
    nums = re.findall(r"\d+", str(s or ""))
    return ",".join(sorted(nums, key=int))


def _row_key(r: dict) -> tuple:
    return tuple(_norm_text(r.get(k)) for k in KEY)


def _row_values(r: dict) -> tuple:
    out = [_norm_text(r.get("schedule_type"))]
    out += [_norm_num(r.get(k)) for k in ("interval_km", "interval_months", "first_km", "first_months")]
    out.append(_norm_sev(r.get("severe_conditions")))
    return tuple(out)


def load_truth(path: str, name: str) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        return [r for r in csv.DictReader(f) if r["manual_id"] == name]


def compare(truth: list[dict], pred: list[dict]) -> dict:
    """정답 행마다 같은 키(구분, 항목, 적용 대상, 동작)의 예측 행이 있고 값도 같으면 정답.
    같은 키가 여러 번 나오면 개수만큼 짝을 맞춤."""
    pred_pool = Counter((_row_key(r), _row_values(r)) for r in pred)
    pred_keys = Counter(_row_key(r) for r in pred)
    details, correct = [], 0
    for r in truth:
        k, v = _row_key(r), _row_values(r)
        if pred_pool[(k, v)] > 0:
            pred_pool[(k, v)] -= 1
            pred_keys[k] -= 1
            status = "정답"
            correct += 1
        elif pred_keys[k] > 0:
            pred_keys[k] -= 1  # 키는 맞았으니 짝을 소비함
            status = "값 틀림"
        else:
            status = "누락"
        details.append({"결과": status, "쪽": r["page"], "구분": r["section"], "항목": r["item"],
                        "적용": r["applies_to"], "동작": r["action"]})
    extra = sum(n for n in pred_keys.values() if n > 0)
    return {"정답 행": len(truth), "맞은 행": correct,
            "정확도": round(correct / len(truth), 3) if truth else 0.0,
            "예측 행": len(pred), "정답에 없는 예측 행(추정)": extra, "details": details}
