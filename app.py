"""car-manual-agent: 설명서 등록(본문 저장 + 점검표 읽기·확인), 에이전트, 평가(AC-7 ~ AC-10)."""
import csv
import json
import os
import time
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from db import connect, get_store, make_tables
from manual_utils import file_hash, find_schedule_pages, read_pages, render_page_png, to_chunks

BASE = Path(__file__).parent
MANUAL_DIR = BASE / "manuals"          # 업로드한 원본 (근거 이미지 렌더링용, git 제외)
EXTRACT_DIR = BASE / "eval" / "extracted"  # 모델이 읽은 그대로의 점검표 (사용자 수정 전)
TRUTH = BASE / "eval" / "schedule_ground_truth.csv"
MANUAL_DIR.mkdir(exist_ok=True)
EXTRACT_DIR.mkdir(parents=True, exist_ok=True)

COLS = ["page", "section", "item", "applies_to", "action", "schedule_type", "interval_km",
        "interval_months", "first_km", "first_months", "severe_conditions", "footnote", "raw_text"]

st.set_page_config(page_title="car-manual-agent", layout="wide")
make_tables()


def list_manuals():
    with connect() as conn:
        return conn.execute("SELECT manual_id, name, car_model, powertrain, schedule_confirmed "
                            "FROM manuals ORDER BY uploaded_at DESC").fetchall()


def add_in_batches(docs, ids, bar, batch=100, retries=3):
    """100개씩 임베딩해 저장. 호출 한도 초과 등으로 실패하면 잠시 쉬고 다시 시도"""
    store = get_store()
    for i in range(0, len(docs), batch):
        for attempt in range(retries):
            try:
                store.add_documents(docs[i:i + batch], ids=ids[i:i + batch])
                break
            except Exception:
                if attempt == retries - 1:
                    raise
                time.sleep(5 * (attempt + 1))
        done = min(i + batch, len(docs))
        bar.progress(done / len(docs), text=f"임베딩 {done}/{len(docs)} 조각")


st.title("car-manual-agent")

with st.sidebar:
    manuals = list_manuals()
    labels = {m[0]: f"{m[1]} ({m[2]}, {m[3]}){'' if m[4] else ' · 점검표 미확인'}" for m in manuals}
    current = st.selectbox("사용할 설명서", list(labels), format_func=labels.get) if manuals else None
    st.session_state["manual_id"] = current

reg_tab, agent_tab, eval_tab = st.tabs(["설명서 등록", "에이전트", "평가"])

# ---------------- 설명서 등록 ----------------
with reg_tab:
    st.subheader("1. 설명서 올리기")
    up = st.file_uploader("취급설명서 PDF", type=["pdf"])
    c1, c2, c3 = st.columns(3)
    name = c1.text_input("설명서 이름 (예: CN8_2027)", help="정답 CSV의 manual_id와 같게 쓰면 평가 탭에서 바로 비교됩니다")
    car_model = c2.text_input("차종 (예: 아반떼 2027)")
    powertrain = c3.selectbox("파워트레인", ["가솔린", "디젤", "LPG", "하이브리드", "전기"])

    if up and name and st.button("본문 저장"):
        data = up.getvalue()
        mid = file_hash(data)
        with connect() as conn:
            exists = conn.execute("SELECT name FROM manuals WHERE manual_id=%s", (mid,)).fetchone()
        if exists:
            st.info(f"이미 저장된 설명서입니다 ({exists[0]}). 새로 처리하지 않습니다.")
        else:
            (MANUAL_DIR / f"{mid}.pdf").write_bytes(data)
            pages = read_pages(data)
            docs, ids = to_chunks(pages, mid)
            bar = st.progress(0.0, text="임베딩 준비")
            add_in_batches(docs, ids, bar)
            with connect() as conn:
                conn.execute("INSERT INTO manuals (manual_id, name, car_model, powertrain, file_name, pages, chunks) "
                             "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                             (mid, name, car_model, powertrain, up.name, len(pages), len(docs)))
            st.success(f"{name}: {len(pages)}쪽 → 조각 {len(docs)}개 저장")
        st.session_state["reg_id"], st.session_state["reg_name"] = mid, name

    mid = st.session_state.get("reg_id") or current
    if mid:
        pdf_path = MANUAL_DIR / f"{mid}.pdf"
        with connect() as conn:
            reg_name = conn.execute("SELECT name FROM manuals WHERE manual_id=%s", (mid,)).fetchone()[0]
        st.divider()
        st.subheader(f"2. 정기 점검표 읽기 — {reg_name}")
        if not pdf_path.exists():
            st.warning("원본 PDF가 이 서버에 없습니다. 같은 파일을 다시 올려 주세요.")
        else:
            data = pdf_path.read_bytes()
            if st.session_state.get("range_for") != mid:
                st.session_state["range"] = find_schedule_pages(read_pages(data))
                st.session_state["range_for"] = mid
            s0, e0 = st.session_state["range"]
            c1, c2 = st.columns(2)
            start = c1.number_input("점검표 시작 쪽 (PDF 기준)", 1, 2000, s0)
            end = c2.number_input("점검표 끝 쪽", 1, 2000, e0)
            with st.expander("후보 쪽 미리 보기"):
                for p in range(int(start), int(end) + 1):
                    st.image(render_page_png(data, p, scale=1.0), caption=f"PDF {p}쪽")

            if st.button("점검표 읽기 (Claude Haiku 4.5)"):
                from schedule_reader import read_schedule_page
                rows, severe = [], {}
                bar = st.progress(0.0)
                pages = list(range(int(start), int(end) + 1))
                last_section = None
                texts = {x["page"]: x["text"] for x in read_pages(data)}
                for i, p in enumerate(pages):
                    try:
                        res = read_schedule_page(render_page_png(data, p), p, last_section,
                                                 texts.get(p, ""))  # 쪽마다 따로 호출
                    except Exception as e:  # 한 쪽이 실패해도 나머지 쪽은 계속 읽음
                        st.warning(f"{p}쪽을 읽지 못했습니다: {type(e).__name__}. 이 쪽 행은 확인 단계에서 직접 추가하세요.")
                        with st.expander(f"{p}쪽 오류 내용"):
                            st.code(str(e)[:3000])
                        bar.progress((i + 1) / len(pages), text=f"{p}쪽: 실패")
                        continue
                    if res.rows:
                        last_section = res.rows[-1].section
                    rows += [{"page": p, **r.model_dump()} for r in res.rows]
                    severe.update({s.no: (p, s.condition) for s in res.severe_conditions})
                    bar.progress((i + 1) / len(pages), text=f"{p}쪽: {len(res.rows)}행")
                raw = pd.DataFrame(rows, columns=COLS)
                raw.insert(0, "manual_id", reg_name)
                raw.to_csv(EXTRACT_DIR / f"{reg_name}.csv", index=False, encoding="utf-8-sig")  # 수정 전 원본
                st.session_state["rows"], st.session_state["severe"] = raw, severe

            if "rows" in st.session_state:
                st.subheader("3. 확인하고 고치기")
                st.caption("모델이 읽은 결과입니다. 이미지와 대조해 틀린 칸을 고친 뒤 저장하세요. 저장 전 값은 답변에 쓰지 않습니다.")
                edited = st.data_editor(st.session_state["rows"], num_rows="dynamic", width="stretch",
                                        key=f"editor_{mid}")
                severe = st.session_state["severe"]
                st.write("가혹 조건: 주로 이런 환경에서 운전하면 체크하세요")
                chosen = [no for no, (_, text) in sorted(severe.items())
                          if st.checkbox(f"{no}. {text}", key=f"sev_{mid}_{no}")]
                if not severe:
                    st.info("가혹 조건 문장 목록을 찾지 못했습니다. 점검표 범위에 가혹 조건 쪽이 들어 있는지 확인하세요.")
                if st.button("확인하고 저장"):
                    with connect() as conn:
                        conn.execute("DELETE FROM maintenance_schedule WHERE manual_id=%s", (mid,))
                        conn.execute("DELETE FROM severe_conditions WHERE manual_id=%s", (mid,))
                        for r in edited.to_dict("records"):
                            vals = []
                            for c in COLS:
                                v = r.get(c)
                                if v is None or (not isinstance(v, str) and pd.isna(v)):
                                    v = None
                                elif isinstance(v, float) and v.is_integer():
                                    v = int(v)  # data_editor가 빈칸 때문에 정수를 실수로 바꿈
                                vals.append(v)
                            conn.execute(f"INSERT INTO maintenance_schedule (manual_id, {', '.join(COLS)}) "
                                         f"VALUES (%s, {', '.join(['%s'] * len(COLS))})", [mid] + vals)
                        for no, (p, text) in severe.items():
                            conn.execute("INSERT INTO severe_conditions VALUES (%s,%s,%s,%s)", (mid, no, p, text))
                        conn.execute("UPDATE manuals SET schedule_confirmed=TRUE, severe_selected=%s WHERE manual_id=%s",
                                     (",".join(map(str, chosen)), mid))
                    st.success(f"점검표 {len(edited)}행, 가혹 조건 {len(severe)}개 저장")

# ---------------- 에이전트 ----------------
@st.cache_data(show_spinner=False)
def page_image(manual_id: str, page: int) -> bytes:
    return render_page_png((MANUAL_DIR / f"{manual_id}.pdf").read_bytes(), page, scale=1.3)


def show_result(manual_id: str, res: dict):
    """답, 근거 쪽 이미지(FR-8), 도구 호출 과정(FR-13)"""
    st.markdown(res["answer"])
    pages = res.get("pages") or []
    if pages and (MANUAL_DIR / f"{manual_id}.pdf").exists():
        cols = st.columns(min(len(pages), 4))
        for col, p in zip(cols, pages[:4]):
            col.image(page_image(manual_id, p), caption=f"PDF {p}쪽", width="stretch")
    if res.get("tool_calls"):
        with st.expander("도구 호출 과정"):
            for c in res["tool_calls"]:
                st.markdown(f"**{c['name']}** `{json.dumps(c['args'], ensure_ascii=False)}`")
                st.code(str(c["output"])[:2000])


with agent_tab:
    if not current:
        st.info("설명서 등록 탭에서 설명서를 먼저 올려 주세요.")
    else:
        if not dict((m[0], m[4]) for m in manuals).get(current):
            st.warning("이 설명서는 점검표를 아직 확인·저장하지 않았습니다. 정비 주기 질문에는 답할 수 없습니다 (R-4).")
        hist = st.session_state.setdefault(f"chat_{current}", [])
        for m in hist:
            with st.chat_message(m["role"]):
                if m["role"] == "assistant":
                    show_result(current, m["res"])
                else:
                    st.markdown(m["content"])
        q = st.chat_input("설명서에 대해 물어보세요. 예: 엔진오일은 얼마마다 갈아?")
        if q:
            with st.chat_message("user"):
                st.markdown(q)
            with st.chat_message("assistant"):
                try:
                    from agent import run_agent
                    with st.spinner("설명서를 찾는 중..."):
                        res = run_agent(current, q, history=[{"role": m["role"], "content": m["content"]} for m in hist])
                except Exception as e:
                    st.error(f"에이전트 실행 중 오류: {type(e).__name__}: {str(e)[:500]}")
                    res = None
                if res:
                    show_result(current, res)
            if res:
                hist += [{"role": "user", "content": q},
                         {"role": "assistant", "content": res["answer"], "res": res}]
        if hist and st.button("대화 지우기"):
            st.session_state[f"chat_{current}"] = []
            st.rerun()

        st.divider()
        st.subheader("내 정비 기록")
        with connect() as conn:
            recs = conn.execute("SELECT item, done_date, mileage FROM my_maintenance WHERE manual_id=%s "
                                "ORDER BY done_date DESC, id DESC", (current,)).fetchall()
        if recs:
            st.dataframe(pd.DataFrame(recs, columns=["항목", "날짜", "주행거리(km)"]), width="stretch")
        else:
            st.caption("아직 기록이 없습니다. 예: \"오늘 엔진오일 갈았어. 30,000km야. 기록해 줘\"")

# ---------------- 평가 ----------------
with eval_tab:
    st.subheader("AC-7 점검표 읽기 정확도")
    st.caption("모델이 읽은 그대로의 결과(eval/extracted/설명서이름.csv)를 정답 CSV와 비교합니다. 사용자가 고친 값은 쓰지 않습니다.")
    files = sorted(p.stem for p in EXTRACT_DIR.glob("*.csv"))
    if not files:
        st.info("아직 읽은 점검표가 없습니다.")
    else:
        from schedule_eval import compare, load_truth
        target = st.selectbox("설명서", files)
        if st.button("비교"):
            with open(EXTRACT_DIR / f"{target}.csv", encoding="utf-8-sig") as f:
                pred = list(csv.DictReader(f))
            truth = load_truth(str(TRUTH), target)
            if not truth:
                st.warning(f"정답 CSV에 manual_id가 {target}인 행이 없습니다.")
            else:
                res = compare(truth, pred)
                st.write({k: v for k, v in res.items() if k != "details"})
                st.dataframe(pd.DataFrame(res["details"]), width="stretch")

    st.divider()
    st.subheader("AC-8 ~ AC-10 에이전트 평가")
    st.caption("eval/eval_questions.csv의 질문 15개를 사이드바에서 고른 설명서로 실행합니다. "
               "setup 기록은 평가 동안만 넣고, 평가 중에 생긴 기록과 함께 끝나면 지웁니다.")
    cur_name = dict((m[0], m[1]) for m in manuals).get(current) if current else None
    if cur_name and cur_name != "CN8_2027":
        st.warning(f"평가 질문은 아반떼(CN8_2027) 기준입니다. 지금 고른 설명서는 {cur_name}입니다.")
    if current and st.button("에이전트 평가 실행 (질문 15개, 몇 분 걸림)"):
        from agent import build_graph, make_tools, run_agent
        from agent_eval import format_summary, load_questions, parse_setup, save_report, score
        questions = load_questions(str(BASE / "eval" / "eval_questions.csv"))
        with connect() as conn:
            max_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM my_maintenance").fetchone()[0]
        results = []
        bar = st.progress(0.0)
        try:
            with connect() as conn:  # setup 기록 넣기 (q08, q09)
                for qq in questions:
                    sp = parse_setup(qq.get("setup"))
                    if sp:
                        conn.execute("INSERT INTO my_maintenance (manual_id, item, done_date, mileage) VALUES "
                                     "(%s,%s,%s,%s) ON CONFLICT (manual_id, item, done_date) DO NOTHING",
                                     (current, *sp))
            tools, info = make_tools(current)
            graph = build_graph(tools, info)
            for i, qq in enumerate(questions):
                try:
                    res = run_agent(current, qq["question"], graph=graph)
                except Exception as e:
                    res = {"answer": f"오류: {type(e).__name__}: {e}", "tools": [], "pages": [], "tool_calls": []}
                results.append({"id": qq["id"], **res})
                bar.progress((i + 1) / len(questions), text=f"{qq['id']} 완료")
        finally:
            with connect() as conn:  # 평가 동안 생긴 기록 지우기
                conn.execute("DELETE FROM my_maintenance WHERE manual_id=%s AND id > %s", (current, max_id))
        out_dir = BASE / "eval" / "agent_results"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{date.today().isoformat()}.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        report = score(questions, results)
        path = save_report(report, directory=str(out_dir))
        st.text(format_summary(report))
        st.dataframe(pd.DataFrame(report["rows"]), width="stretch")
        st.write("답 확인 (정답 문장과 사람이 비교)")
        st.dataframe(pd.DataFrame([{"id": q["id"], "질문": q["question"], "정답": q["reference_answer"],
                                    "에이전트 답": r["answer"]} for q, r in zip(questions, results)]),
                     width="stretch")
        st.caption(f"저장: {path}, 같은 폴더의 .json")
