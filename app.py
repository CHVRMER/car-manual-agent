"""car-manual-agent: 설명서 등록(본문 저장 + 점검표 읽기·확인), 에이전트, 평가(AC-7 ~ AC-10)."""
import csv
import json
import re
import time
from datetime import date, datetime
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

AI_BLUE = "#2b4a9b"
NAVY = "#1b2a59"
AI_BUBBLE = "#e7edf9"
USER_BUBBLE = "#f0f2f6"

STYLE = f"""
<style>
[data-testid="stSidebarCollapseButton"] {{ display: none !important; }}

/* 실행 중 표시: 화면 전체를 덮는 로딩은 쓰지 않는다. 오래 걸리는 작업은 각자 진행 막대·말풍선 로딩을 보여 준다 */
[data-testid="stStatusWidget"]:has([data-testid="stStatusWidgetRunningIcon"]) {{ visibility: hidden; }}
/* 다시 그리는 동안 화면이 흐려지지 않게 */
[data-stale="true"] {{ opacity: 1 !important; transition: none !important; }}

/* 첫 질문 전: 입력창 위에 위아래로 움직이는 안내 말풍선. 입력창을 누르면 사라진다 */
[data-testid="stElementContainer"]:has(.cma-hint) {{ display: none; }}
@keyframes cma-bounce {{
  0%, 100% {{ transform: translate(-50%, 0); }}
  50% {{ transform: translate(-50%, -8px); }}
}}
body:has(.cma-hint) [data-testid="stChatInput"] {{ position: relative; overflow: visible; }}
body:has(.cma-hint) [data-testid="stChatInput"]::before,
body:has(.cma-hint) [data-testid="stChatInput"]::after {{
  content: ""; position: absolute; left: 50%; z-index: 10; pointer-events: none;
  animation: cma-bounce 1.2s ease-in-out infinite;
}}
body:has(.cma-hint) [data-testid="stChatInput"]::before {{
  content: "여기에 궁금한 점을 물어보세요!"; bottom: calc(100% + 14px); white-space: nowrap;
  padding: 0.5rem 1rem; border-radius: 1rem; background: {NAVY}; color: #fff;
  font-size: 0.9rem; font-weight: 600; box-shadow: 0 4px 12px rgba(27, 42, 89, 0.25);
}}
body:has(.cma-hint) [data-testid="stChatInput"]::after {{
  bottom: calc(100% + 6px); border-style: solid; border-width: 8px 8px 0 8px;
  border-color: {NAVY} transparent transparent transparent;
}}
body:has(.cma-hint) [data-testid="stChatInput"]:focus-within::before,
body:has(.cma-hint) [data-testid="stChatInput"]:focus-within::after,
body.cma-hint-dismissed [data-testid="stChatInput"]::before,
body.cma-hint-dismissed [data-testid="stChatInput"]::after {{ display: none; }}
[data-testid="stElementContainer"]:has(iframe[srcdoc*="cma-hint-dismissed"]) {{ display: none; }}

[data-testid="stSidebarUserContent"] {{ padding-top: 0; margin-top: -3.6rem; }}
[data-testid="stSidebar"] .st-key-brand {{ margin-bottom: 0.6rem; }}
[data-testid="stSidebar"] .st-key-brand button {{
  height: 24px; min-height: 0; padding: 0 !important; background: transparent !important;
}}
[data-testid="stSidebar"] .st-key-brand p {{
  line-height: 24px; font-size: 22px !important; font-weight: 900; letter-spacing: -0.01em;
  color: {NAVY}; white-space: nowrap;
}}
[data-testid="stSidebar"] .st-key-brand button:hover p {{ color: {AI_BLUE}; }}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"]:has(.side-label) {{ margin-bottom: 0; }}
[data-testid="stSidebar"] .side-label {{ margin: 1.2rem 0 0.5rem 0; font-size: 0.875rem; color: rgba(27, 42, 89, 0.6); }}

/* 업로드 박스: 네이비 굵은 점선, 안의 요소는 가운데 정렬 */
[data-testid="stFileUploaderDropzone"] {{
  flex-direction: column; align-items: center; justify-content: center; gap: 0.6rem;
  padding: 2rem 1rem; text-align: center;
  border: 3px dashed {NAVY}; border-radius: 0.75rem; background: #f4f6fb;
}}
[data-testid="stFileUploaderDropzone"] > span {{ margin: 0; }}
[data-testid="stFileUploaderDropzone"] button {{ color: {NAVY}; border-color: {NAVY}; }}
[data-testid="stFileUploaderDropzone"] button:hover {{ color: #fff; background: {NAVY}; border-color: {NAVY}; }}
[data-testid="stFileUploaderDropzone"] button:hover [data-testid="stIconMaterial"] {{ color: #fff; }}
[data-testid="stFileUploaderDropzoneInstructions"],
[data-testid="stFileUploaderDropzoneInstructions"] span {{ color: {NAVY}; text-align: center; margin: 0; }}

/* 설명서 목록: 일반 AI 서비스의 대화 목록처럼 왼쪽 정렬로 세로로 */
[data-testid="stSidebar"] [data-testid="stButton"] button {{ justify-content: flex-start; padding: 0.4rem 0.75rem; }}
[data-testid="stSidebar"] [data-testid="stButton"] button > div {{ justify-content: flex-start; width: 100%; }}
[data-testid="stSidebar"] [data-testid="stButton"] p {{ text-align: left; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
[data-testid="stSidebar"] [data-testid="stBaseButton-tertiary"]:hover {{ background: rgba(27, 42, 89, 0.07); color: inherit; }}
[data-testid="stSidebar"] [data-testid="stBaseButton-primary"] {{ background: rgba(27, 42, 89, 0.12); color: {NAVY}; border: none; }}
[data-testid="stSidebar"] [data-testid="stBaseButton-primary"]:hover {{ background: rgba(27, 42, 89, 0.18); color: {NAVY}; }}
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] {{ color: {NAVY}; border-color: rgba(27, 42, 89, 0.35); }}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {{ gap: 0.25rem; }}

/* 대화: AI 아바타는 파란색, 사람 아바타는 검정색 */
[data-testid="stChatMessageAvatarAssistant"] {{ background-color: {AI_BLUE} !important; color: #fff !important; }}
[data-testid="stChatMessageAvatarUser"] {{ background-color: #111 !important; color: #fff !important; }}

/* 말풍선: 꼬리(::before 삼각형)가 아이콘 쪽을 가리킨다 */
[data-testid="stChatMessage"] {{ background: transparent; }}
[data-testid="stChatMessageContent"] {{
  position: relative; overflow: visible; border-radius: 1rem; padding: 0.6rem 1rem;
}}
[data-testid="stChatMessageContent"]::before {{
  content: ""; position: absolute; top: 0.6rem; border-style: solid;
}}

/* AI 답변: 왼쪽 아이콘, 꼬리는 왼쪽 */
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) [data-testid="stChatMessageContent"] {{
  flex: 0 1 auto; max-width: 85%; margin-left: 0.6rem; background: {AI_BUBBLE};
}}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) [data-testid="stChatMessageContent"]::before {{
  left: -8px; border-width: 8px 9px 8px 0; border-color: transparent {AI_BUBBLE} transparent transparent;
}}

/* 사람 질문: 아이콘과 말풍선을 오른쪽에, 꼬리는 오른쪽 */
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {{ flex-direction: row-reverse; }}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] {{
  flex: 0 1 auto; max-width: 75%; margin-right: 0.6rem; background: {USER_BUBBLE};
}}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"]::before {{
  right: -8px; border-width: 8px 0 8px 9px; border-color: transparent transparent transparent {USER_BUBBLE};
}}
</style>
"""

st.set_page_config(page_title="Car Manual Agent", layout="wide")
st.markdown(STYLE, unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def init_db():
    make_tables()  # 서버가 켜진 뒤 한 번만 (매 클릭마다 DB에 가지 않게)
    return True


init_db()


@st.cache_data(ttl=300, show_spinner=False)
def list_manuals():
    """설명서 목록. 등록·저장 때 list_manuals.clear()로 새로 읽는다"""
    with connect() as conn:
        return conn.execute("SELECT manual_id, name, car_model, powertrain, schedule_confirmed, pages "
                            "FROM manuals ORDER BY uploaded_at DESC").fetchall()


@st.cache_resource(show_spinner=False)
def pdf_bytes(manual_id: str) -> bytes:
    """원본 PDF (수십 MB)를 클릭마다 다시 읽지 않게 메모리에 둔다"""
    return (MANUAL_DIR / f"{manual_id}.pdf").read_bytes()


@st.cache_data(show_spinner=False)
def preview_image(manual_id: str, page: int) -> bytes:
    return render_page_png(pdf_bytes(manual_id), page, scale=1.0)


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


def select(manual_id, view="chat"):
    st.session_state["manual_id"] = manual_id
    st.session_state["view"] = view


@st.cache_data(show_spinner=False)
def page_image(manual_id: str, page: int) -> bytes:
    return render_page_png(pdf_bytes(manual_id), page, scale=1.3)


def words(text: str, delay: float = 0.04):
    for token in re.split(r"(\s+)", text):
        yield token
        if token.strip():
            time.sleep(delay)


def show_result(manual_id: str, res: dict, stream: bool = False):
    """답, 근거 쪽 이미지(FR-8), 도구 호출 과정(FR-13). stream이면 답을 어절 단위로 흘려 보여 준다"""
    if stream:
        st.write_stream(words(res["answer"]))
    else:
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


HINT_JS = """
<script>
(() => {
  const w = window.parent, doc = w.document, KEY = "cma-hint-dismissed";
  const dismiss = () => {
    doc.body.classList.add(KEY);
    try { w.sessionStorage.setItem(KEY, "1"); } catch (e) {}
  };
  try { if (w.sessionStorage.getItem(KEY)) doc.body.classList.add(KEY); } catch (e) {}
  if (w.__cmaHintBound) return;
  w.__cmaHintBound = true;
  const onInput = (e) => e.target.closest && e.target.closest('[data-testid="stChatInput"]') && dismiss();
  doc.addEventListener("focusin", onInput, true);
  doc.addEventListener("pointerdown", onInput, true);
})();
</script>
"""


def hint_script():
    """입력창을 한 번이라도 누르면 안내 말풍선을 이 탭에서 다시 보이지 않게 한다 (CSS만으로는 기억할 수 없음)"""
    st.iframe(HINT_JS, height=1)  # 보이지 않는 칸 (CSS로 숨김)


# ---------------- 설명서 올리기 (첫 화면) ----------------
def upload_view():
    st.markdown("<div style='height: 16vh'></div>", unsafe_allow_html=True)
    _, mid_col, _ = st.columns([1, 3, 1])
    with mid_col:
        st.markdown("<div style='text-align: center; font-size: 1.5rem; font-weight: 700; margin-bottom: 0.4rem'>"
                    "차량 취급설명서 PDF를 올려 주세요</div>"
                    "<p style='text-align: center; color: rgba(49, 51, 63, 0.6); font-size: 0.875rem'>"
                    "설명서를 등록하면 아래 입력창에서 질문할 수 있습니다.</p>", unsafe_allow_html=True)
        up = st.file_uploader("취급설명서 PDF", type=["pdf"], label_visibility="collapsed")
        c1, c2, c3 = st.columns(3, vertical_alignment="bottom")
        name = c1.text_input("설명서 이름", placeholder="예: CN8_2027",
                             help="정답 CSV의 manual_id와 같게 쓰면 평가에서 바로 비교됩니다")
        car_model = c2.text_input("차종", placeholder="예: 아반떼 2027")
        powertrain = c3.selectbox("파워트레인", ["가솔린", "디젤", "LPG", "하이브리드", "전기"])
        if st.button("설명서 등록", type="primary", disabled=not (up and name), width="stretch"):
            data = up.getvalue()
            mid = file_hash(data)
            with connect() as conn:
                exists = conn.execute("SELECT name FROM manuals WHERE manual_id=%s", (mid,)).fetchone()
            if not exists:
                (MANUAL_DIR / f"{mid}.pdf").write_bytes(data)
                pages = read_pages(data)
                docs, ids = to_chunks(pages, mid)
                bar = st.progress(0.0, text="임베딩 준비")
                add_in_batches(docs, ids, bar)
                with connect() as conn:
                    conn.execute("INSERT INTO manuals (manual_id, name, car_model, powertrain, file_name, pages, chunks) "
                                 "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                                 (mid, name, car_model, powertrain, up.name, len(pages), len(docs)))
                list_manuals.clear()
            select(mid)
            st.rerun()


# ---------------- 정기 점검표 읽기·확인 ----------------
@st.fragment  # 쪽 번호·미리 보기 조작은 이 패널만 다시 그린다
def schedule_panel(mid: str, reg_name: str):
    pdf_path = MANUAL_DIR / f"{mid}.pdf"
    if not pdf_path.exists():
        st.warning("원본 PDF가 이 서버에 없습니다. 같은 파일을 다시 올려 주세요.")
        return
    data = pdf_bytes(mid)
    if st.session_state.get("range_for") != mid:
        st.session_state["range"] = find_schedule_pages(read_pages(data))
        st.session_state["range_for"] = mid
    s0, e0 = st.session_state["range"]
    c1, c2 = st.columns(2)
    start = c1.number_input("점검표 시작 쪽 (PDF 기준)", 1, 2000, s0, key=f"start_{mid}")
    end = c2.number_input("점검표 끝 쪽", 1, 2000, e0, key=f"end_{mid}")
    if st.toggle("후보 쪽 미리 보기", key=f"preview_{mid}"):
        for p in range(int(start), int(end) + 1):
            st.image(preview_image(mid, p), caption=f"PDF {p}쪽")

    rows_key, severe_key = f"rows_{mid}", f"severe_{mid}"
    flash_key = f"saved_{mid}"
    if st.button("점검표 읽기", key=f"read_{mid}"):
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
        st.session_state[rows_key], st.session_state[severe_key] = raw, severe

    if rows_key in st.session_state:
        st.markdown("**확인하고 고치기**")
        st.caption("모델이 읽은 결과입니다. 이미지와 대조해 틀린 칸을 고친 뒤 저장하세요. 저장 전 값은 답변에 쓰지 않습니다.")
        severe = st.session_state[severe_key]
        with st.form(key=f"confirm_{mid}", border=False):  # 표 편집·체크는 저장을 누를 때 한 번에 반영
            edited = st.data_editor(st.session_state[rows_key], num_rows="dynamic", width="stretch",
                                    key=f"editor_{mid}")
            st.write("가혹 조건: 주로 이런 환경에서 운전하면 체크하세요")
            chosen = [no for no, (_, text) in sorted(severe.items())
                      if st.checkbox(f"{no}. {text}", key=f"sev_{mid}_{no}")]
            if not severe:
                st.info("가혹 조건 문장 목록을 찾지 못했습니다. 점검표 범위에 가혹 조건 쪽이 들어 있는지 확인하세요.")
            submitted = st.form_submit_button("확인하고 저장")
        if st.session_state.get(flash_key):  # 저장 직후: 버튼 바로 아래에 결과
            msg = st.session_state.pop(flash_key)
            st.success(msg)
            st.toast(msg, icon=":material/check_circle:")
        if submitted:
            rows = []
            for r in edited.to_dict("records"):
                vals = []
                for c in COLS:
                    v = r.get(c)
                    if v is None or (not isinstance(v, str) and pd.isna(v)):
                        v = None
                    elif isinstance(v, float) and v.is_integer():
                        v = int(v)  # data_editor가 빈칸 때문에 정수를 실수로 바꿈
                    vals.append(v)
                rows.append([mid] + vals)
            with st.spinner(f"점검표 {len(rows)}행을 저장하는 중..."), connect() as conn:
                conn.execute("DELETE FROM maintenance_schedule WHERE manual_id=%s", (mid,))
                conn.execute("DELETE FROM severe_conditions WHERE manual_id=%s", (mid,))
                with conn.cursor() as cur:  # 한 줄씩 왕복하지 않고 묶어서 보냄 (터널 너머 DB라 차이가 큼)
                    cur.executemany(f"INSERT INTO maintenance_schedule (manual_id, {', '.join(COLS)}) "
                                    f"VALUES (%s, {', '.join(['%s'] * len(COLS))})", rows)
                    cur.executemany("INSERT INTO severe_conditions VALUES (%s,%s,%s,%s)",
                                    [(mid, no, p, text) for no, (p, text) in severe.items()])
                conn.execute("UPDATE manuals SET schedule_confirmed=TRUE, severe_selected=%s WHERE manual_id=%s",
                             (",".join(map(str, chosen)), mid))
            st.session_state[flash_key] = f"점검표 {len(edited)}행, 가혹 조건 {len(severe)}개 저장"
            list_manuals.clear()
            st.rerun()  # 사이드바의 '점검표 미확인' 표시와 경고를 바로 갱신


def maintenance_records(mid: str):
    with connect() as conn:
        recs = conn.execute("SELECT item, done_date, mileage FROM my_maintenance WHERE manual_id=%s "
                            "ORDER BY done_date DESC, id DESC", (mid,)).fetchall()
    if recs:
        st.dataframe(pd.DataFrame(recs, columns=["항목", "날짜", "주행거리(km)"]), width="stretch")
    else:
        st.caption("아직 기록이 없습니다. 예: \"오늘 엔진오일 갈았어. 30,000km야. 기록해 줘\"")


# ---------------- 대화 ----------------
def manual_header(manual):
    mid, name, car_model, powertrain, confirmed, pages = manual
    with st.container(border=True):
        left, right = st.columns([5, 1], vertical_alignment="center")
        left.markdown(f"**📘 {name}**")
        left.caption(" · ".join(str(x) for x in [car_model, powertrain, f"{pages}쪽" if pages else None] if x))
        hist_key = f"chat_{mid}"
        if st.session_state.get(hist_key) and right.button("대화 지우기", width="stretch"):
            st.session_state[hist_key] = []
            st.rerun()
        if not confirmed:
            st.warning("점검표를 아직 확인·저장하지 않았습니다. 정비 주기 질문에는 답할 수 없습니다 (R-4). "
                       "아래 '정기 점검표 읽기·확인'에서 저장하세요.")
        with st.expander("정기 점검표 읽기·확인", expanded=False):
            schedule_panel(mid, name)
        with st.expander("내 정비 기록"):
            maintenance_records(mid)


def chat_history(mid: str):
    for m in st.session_state.setdefault(f"chat_{mid}", []):
        with st.chat_message(m["role"]):
            if m["role"] == "assistant":
                show_result(mid, m["res"])
            else:
                st.markdown(m["content"])


def answer(mid: str, q: str):
    hist = st.session_state.setdefault(f"chat_{mid}", [])
    with st.chat_message("user"):
        st.markdown(q)
    res = None
    with st.chat_message("assistant"):
        try:
            from agent import run_agent
            with st.spinner("설명서를 찾는 중..."):
                res = run_agent(mid, q, history=[{"role": m["role"], "content": m["content"]} for m in hist])
        except Exception as e:
            st.error(f"에이전트 실행 중 오류: {type(e).__name__}: {str(e)[:500]}")
        if res:
            show_result(mid, res, stream=True)
    if res:
        hist += [{"role": "user", "content": q},
                 {"role": "assistant", "content": res["answer"], "res": res}]


# ---------------- 평가 ----------------
def eval_view(manuals):
    st.subheader("점검표 추출 정확도")
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
    st.subheader("에이전트 평가: 도구 선택 · 근거 쪽 · 범위 밖 거절")
    st.caption("eval/eval_questions.csv의 질문 15개를 고른 설명서로 실행합니다. "
               "평가 동안에는 내 정비 기록을 잠시 비우고, 질문마다 그 질문의 setup 기록만 넣었다가 지웁니다. "
               "끝나면 원래 기록을 되돌립니다.")
    if not manuals:
        st.info("등록된 설명서가 없습니다.")
        return
    names = {m[0]: m[1] for m in manuals}
    ids = list(names)
    default = ids.index(st.session_state["manual_id"]) if st.session_state.get("manual_id") in ids else 0
    current = st.selectbox("평가할 설명서", ids, index=default, format_func=names.get)
    if names[current] != "CN8_2027":
        st.warning(f"평가 질문은 아반떼(CN8_2027) 기준입니다. 지금 고른 설명서는 {names[current]}입니다.")
    if st.button("에이전트 평가 실행 (질문 15개, 몇 분 걸림)"):
        from agent import build_graph, make_tools, run_agent
        from agent_eval import format_summary, load_questions, parse_setup, save_report, score
        questions = load_questions(str(BASE / "eval" / "eval_questions.csv"))
        out_dir = BASE / "eval" / "agent_results"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")  # 같은 날 여러 번 돌려도 덮어쓰지 않게

        # 1) 내 정비 기록 백업 (평가는 기록이 없는 같은 조건에서 반복 측정해야 비교할 수 있음)
        with connect() as conn:
            backup = conn.execute("SELECT item, done_date, mileage FROM my_maintenance WHERE manual_id=%s "
                                  "ORDER BY id", (current,)).fetchall()
        (out_dir / f"{stamp}_records_backup.json").write_text(  # 도중에 앱이 꺼져도 되살릴 수 있게
            json.dumps([list(r) for r in backup], ensure_ascii=False, default=str), encoding="utf-8")

        def clear_records():
            with connect() as conn:
                conn.execute("DELETE FROM my_maintenance WHERE manual_id=%s", (current,))

        results = []
        bar = st.progress(0.0, text="평가 준비")
        try:
            clear_records()
            tools, info = make_tools(current)
            graph = build_graph(tools, info)
            for i, qq in enumerate(questions):
                sp = parse_setup(qq.get("setup"))
                if sp:  # 2) 이 질문의 setup만 넣고
                    with connect() as conn:
                        conn.execute("INSERT INTO my_maintenance (manual_id, item, done_date, mileage) "
                                     "VALUES (%s,%s,%s,%s)", (current, *sp))
                try:
                    res = run_agent(current, qq["question"], graph=graph)
                except Exception as e:
                    res = {"answer": f"오류: {type(e).__name__}: {e}", "tools": [], "pages": [], "tool_calls": []}
                finally:
                    clear_records()  # 3) 다음 질문에 섞이지 않게 바로 지움 (q10이 남긴 기록 포함)
                results.append({"id": qq["id"], **res})
                bar.progress((i + 1) / len(questions), text=f"{qq['id']} 완료")
        finally:
            # 4) 원래 기록 되돌리기 (오류가 나도 실행)
            with connect() as conn:
                conn.execute("DELETE FROM my_maintenance WHERE manual_id=%s", (current,))
                if backup:
                    with conn.cursor() as cur:
                        cur.executemany("INSERT INTO my_maintenance (manual_id, item, done_date, mileage) "
                                        "VALUES (%s,%s,%s,%s)", [(current, *r) for r in backup])
        (out_dir / f"{stamp}.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        report = score(questions, results)
        path = Path(save_report(report, directory=str(out_dir)))
        path = path.replace(out_dir / f"{stamp}.csv")
        st.text(format_summary(report))
        st.dataframe(pd.DataFrame(report["rows"]), width="stretch")
        st.write("답 확인 (정답 문장과 사람이 비교)")
        st.dataframe(pd.DataFrame([{"id": q["id"], "질문": q["question"], "정답": q["reference_answer"],
                                    "에이전트 답": r["answer"]} for q, r in zip(questions, results)]),
                     width="stretch")
        st.caption(f"저장: {path}, 같은 폴더의 .json")


# ---------------- 화면 ----------------
manuals = list_manuals()
by_id = {m[0]: m for m in manuals}
if st.session_state.get("manual_id") not in by_id:
    st.session_state["manual_id"] = None
current = st.session_state["manual_id"]
view = st.session_state.setdefault("view", "chat")

with st.sidebar:
    st.button("JOOYOUNG TECH", key="brand", on_click=select, args=(None,), type="tertiary",
              help="처음 화면으로")
    st.markdown('<p class="side-label">설명서</p>', unsafe_allow_html=True)
    for m in manuals:
        selected = view == "chat" and m[0] == current
        label = f"{m[1]} ({m[2]}){'' if m[4] else ' · 점검표 미확인'}"
        st.button(label, key=f"manual_{m[0]}", on_click=select, args=(m[0],),
                  type="primary" if selected else "tertiary", width="stretch")
    if not manuals:
        st.caption("아직 등록한 설명서가 없습니다.")
    st.divider()
    st.button("평가", on_click=select, args=(current, "eval"),
              type="primary" if view == "eval" else "tertiary", width="stretch")

if view == "eval":
    eval_view(manuals)
elif current is None:
    upload_view()
else:
    manual_header(by_id[current])
    chat_history(current)
    hint = st.empty()
    if not st.session_state.get(f"chat_{current}"):
        hint.markdown('<span class="cma-hint"></span>', unsafe_allow_html=True)
        hint_script()

q = st.chat_input("설명서에 대해 물어보세요. 예: 엔진오일은 얼마마다 갈아?" if current
                  else "설명서를 먼저 등록하면 질문할 수 있습니다.",
                  disabled=view == "eval" or current is None)
if q and current and view == "chat":
    hint.empty()
    answer(current, q)
