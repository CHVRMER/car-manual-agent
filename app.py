"""car-manual-agent 1일차: 설명서 등록(본문 저장 + 점검표 읽기·확인)과 점검표 읽기 정확도."""
import csv
import os
import time
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
                edited = st.data_editor(st.session_state["rows"], num_rows="dynamic", use_container_width=True,
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

# ---------------- 에이전트 (2일차) ----------------
with agent_tab:
    st.info("2일차에 도구 4개와 LangGraph 에이전트를 붙입니다.")

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
                st.dataframe(pd.DataFrame(res["details"]), use_container_width=True)
