"""AC-8 도구 선택, AC-9 근거 쪽, AC-10 거절을 채점한다.

에이전트 실행 결과는 질문마다 아래 dict다.

    {"id": "q02", "tools": ["maintenance_info"], "pages": [408, 411], "answer": "..."}

expected_tool이 없음이면 도구를 부르지 않거나, search_manual만 부르고
R-6 문구로 답한 경우를 AC-8 정답으로 본다.

실행: python agent_eval.py 결과.json  (eval/agent_results/날짜.csv에 저장)
"""
import csv
import json
import re
import sys
from datetime import date
from pathlib import Path

QUESTIONS_PATH = "eval/eval_questions.csv"
RESULTS_DIR = "eval/agent_results"
R6 = "취급설명서에서 찾을 수 없는 내용입니다"
SETUP_RE = re.compile(
    r"^(?P<item>.+),\s*(?P<day>\d{4}-\d{2}-\d{2}),\s*(?P<km>\d+)\s*km\s*$"
)
COLUMNS = ["id", "기대 도구", "실제 도구", "도구 선택 결과", "정답 쪽", "보여 준 쪽", "근거 쪽 결과"]


def parse_setup(text: str | None):
    """'항목, YYYY-MM-DD, 숫자km'를 (item, date, km)로 바꾼다. 빈칸이면 None."""
    if text is None or str(text).strip() == "":
        return None
    matched = SETUP_RE.match(str(text).strip())
    if not matched:
        raise ValueError(f"setup 형식이 아닙니다: {text}")
    return (
        matched.group("item").strip(),
        date.fromisoformat(matched.group("day")),
        int(matched.group("km")),
    )


def load_questions(path: str) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _pages(text: str) -> list[int]:
    return [int(part) for part in re.split(r"[;,\s]+", str(text or "").strip()) if part]


def _ratio(correct: int, total: int) -> float:
    return round(correct / total, 3) if total else 0.0


def _ac8(question: dict, result: dict) -> bool:
    tools = result.get("tools") or []
    expected = (question.get("expected_tool") or "").strip()
    if expected == "없음":
        if not tools:
            return True
        only_search = all(name == "search_manual" for name in tools)
        return only_search and R6 in (result.get("answer") or "")
    return bool(tools) and tools[0] == expected


def _ac9(expected_pages: list[int], shown: list[int]) -> tuple[bool, bool]:
    shown_set = set(shown)
    any_hit = any(page in shown_set for page in expected_pages)
    all_hit = all(page in shown_set for page in expected_pages)
    return any_hit, all_hit


def score(questions: list[dict], results: list[dict]) -> dict:
    by_id = {row["id"]: row for row in results}
    rows = []
    ac8_hit = ac9_any_hit = ac9_all_hit = ac10_hit = 0
    ac9_total = ac10_total = 0

    for question in questions:
        result = by_id.get(question["id"], {})
        tools = result.get("tools") or []
        shown = [int(page) for page in (result.get("pages") or [])]
        expected_pages = _pages(question.get("answer_pages"))
        ac8_ok = _ac8(question, result)
        ac8_hit += ac8_ok

        if expected_pages:
            ac9_total += 1
            any_hit, all_hit = _ac9(expected_pages, shown)
            ac9_any_hit += any_hit
            ac9_all_hit += all_hit
            ac9_label = "정답" if any_hit else "오답"
        else:
            ac9_label = "대상 아님"

        if question.get("type") == "문서에 없음":
            ac10_total += 1
            if R6 in (result.get("answer") or ""):
                ac10_hit += 1

        rows.append({
            "id": question["id"],
            "기대 도구": question.get("expected_tool", ""),
            "실제 도구": ";".join(tools),
            "도구 선택 결과": "정답" if ac8_ok else "오답",
            "정답 쪽": question.get("answer_pages", ""),
            "보여 준 쪽": ";".join(str(page) for page in shown),
            "근거 쪽 결과": ac9_label,
        })

    return {
        "ac8": {"correct": ac8_hit, "total": len(questions), "ratio": _ratio(ac8_hit, len(questions))},
        "ac9_any": {"correct": ac9_any_hit, "total": ac9_total, "ratio": _ratio(ac9_any_hit, ac9_total)},
        "ac9_all": {"correct": ac9_all_hit, "total": ac9_total, "ratio": _ratio(ac9_all_hit, ac9_total)},
        "ac10": {"correct": ac10_hit, "total": ac10_total, "ratio": _ratio(ac10_hit, ac10_total)},
        "rows": rows,
    }


def save_report(report: dict, directory: str = RESULTS_DIR, day: date | None = None) -> str:
    """질문별 표를 eval/agent_results/날짜.csv로 저장하고 경로를 돌려준다."""
    day = day or date.today()
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{day.isoformat()}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(report["rows"])
    return str(path)


def format_summary(report: dict) -> str:
    labels = [
        ("도구 선택 정확도", "ac8"),
        ("근거 쪽 적중률(하나라도)", "ac9_any"),
        ("근거 쪽 적중률(모두)", "ac9_all"),
        ("범위 밖 질문 거절률", "ac10"),
    ]
    lines = []
    for label, key in labels:
        item = report[key]
        lines.append(f"{label}: {item['correct']}/{item['total']} ({item['ratio']})")
    return "\n".join(lines)


def main(argv: list[str]) -> None:
    """python agent_eval.py 결과.json — 결과 JSON은 질문별 dict의 list."""
    if len(argv) != 2:
        sys.exit("사용법: python agent_eval.py 결과.json")
    with open(argv[1], encoding="utf-8") as f:
        results = json.load(f)
    report = score(load_questions(QUESTIONS_PATH), results)
    print(format_summary(report))
    print()
    print("\t".join(COLUMNS))
    for row in report["rows"]:
        print("\t".join(str(row[column]) for column in COLUMNS))
    print()
    print("저장:", save_report(report))


if __name__ == "__main__":
    main(sys.argv)
