"""가짜 에이전트 결과로 AC-8~AC-10 채점과 setup 파싱을 확인한다."""
from datetime import date
from pathlib import Path

import pytest

from agent_eval import load_questions, parse_setup, save_report, score

QUESTIONS = Path("eval/eval_questions.csv")


def test_parse_setup():
    assert parse_setup("") is None
    assert parse_setup(None) is None
    assert parse_setup("   ") is None
    assert parse_setup("엔진 오일 및 오일 필터, 2026-08-29, 30000km") == (
        "엔진 오일 및 오일 필터", date(2026, 8, 29), 30000,
    )
    assert parse_setup("브레이크 액, 2026-04-15, 20000km") == (
        "브레이크 액", date(2026, 4, 15), 20000,
    )


def test_parse_setup_rejects_bad_text():
    with pytest.raises(ValueError):
        parse_setup("2026.8.29")


def _question(**kwargs):
    base = {
        "id": "q01", "type": "사실 찾기", "expected_tool": "search_manual",
        "answer_pages": "15", "reference_answer": "정답",
    }
    base.update(kwargs)
    return base


def test_score_rules_with_fake_results():
    questions = [
        _question(id="q01", expected_tool="search_manual", answer_pages="15"),
        _question(id="q02", expected_tool="maintenance_info", answer_pages="408;411"),
        _question(id="q08", expected_tool="maintenance_info", answer_pages="408"),
        _question(id="q13", type="문서에 없음", expected_tool="없음", answer_pages=""),
        _question(id="q14", type="문서에 없음", expected_tool="없음", answer_pages=""),
        _question(id="q15", type="문서에 없음", expected_tool="없음", answer_pages=""),
    ]
    results = [
        {"id": "q01", "tools": ["search_manual"], "pages": [15], "answer": "정답"},
        {"id": "q02", "tools": ["maintenance_info"], "pages": [408], "answer": "일부 쪽"},
        {"id": "q08", "tools": ["search_manual"], "pages": [99], "answer": "다른 도구"},
        {"id": "q13", "tools": [], "pages": [], "answer": "취급설명서에서 찾을 수 없는 내용입니다."},
        {"id": "q14", "tools": ["search_manual"], "pages": [10],
         "answer": "취급설명서에서 찾을 수 없는 내용입니다."},
        {"id": "q15", "tools": ["search_manual"], "pages": [], "answer": "연비는 15km입니다."},
    ]
    report = score(questions, results)
    by_id = {row["id"]: row for row in report["rows"]}

    assert by_id["q01"]["AC-8 결과"] == "정답"
    assert by_id["q01"]["AC-9 결과"] == "정답"
    assert by_id["q02"]["AC-9 결과"] == "정답"  # 408만 있어도 하나라도 맞으면 정답
    assert by_id["q08"]["AC-8 결과"] == "오답"
    assert by_id["q08"]["AC-9 결과"] == "오답"
    assert by_id["q13"]["AC-8 결과"] == "정답"
    assert by_id["q14"]["AC-8 결과"] == "정답"
    assert by_id["q15"]["AC-8 결과"] == "오답"
    assert report["ac8"] == {"correct": 4, "total": 6, "ratio": 0.667}
    assert report["ac9_any"] == {"correct": 2, "total": 3, "ratio": 0.667}
    assert report["ac9_all"] == {"correct": 1, "total": 3, "ratio": 0.333}
    assert report["ac10"] == {"correct": 2, "total": 3, "ratio": 0.667}


def test_perfect_fake_run_on_real_questions(tmp_path):
    questions = load_questions(QUESTIONS)
    assert [row["id"] for row in questions] == [f"q{i:02d}" for i in range(1, 16)]
    results = []
    for row in questions:
        expected = row["expected_tool"]
        tools = [] if expected == "없음" else [expected]
        pages = [int(part) for part in row["answer_pages"].split(";") if part]
        results.append({
            "id": row["id"],
            "tools": tools,
            "pages": pages,
            "answer": row["reference_answer"],
        })
    report = score(questions, results)
    assert report["ac8"]["ratio"] == 1.0
    assert report["ac9_any"]["ratio"] == 1.0
    assert report["ac9_all"]["ratio"] == 1.0
    assert report["ac10"] == {"correct": 3, "total": 3, "ratio": 1.0}
    saved = save_report(report, tmp_path, day=date(2026, 11, 25))
    assert Path(saved).name == "2026-11-25.csv"
    assert "q08" in Path(saved).read_text(encoding="utf-8")
