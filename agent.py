"""2일차: 도구 4개 + LangGraph 에이전트.

화면(app.py)과 평가는 run_agent()만 부른다.
도구는 설명서마다 만들어서, 검색과 기록이 고른 설명서 안에서만 일어나게 한다 (R-8).
"""
import os
import re
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Optional

from dotenv import load_dotenv
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from db import connect, get_store
from maintenance_calc import next_due

load_dotenv()

KST = timezone(timedelta(hours=9))
R6 = "취급설명서에서 찾을 수 없는 내용입니다."
SCHEDULE_COLS = ["page", "section", "item", "applies_to", "action", "schedule_type", "interval_km",
                 "interval_months", "first_km", "first_months", "severe_conditions", "raw_text"]

# 사용자가 흔히 쓰는 말 → 점검표에 적힌 말 (띄어쓰기 없이 비교)
ALIASES = {
    "에어컨필터": "공조장치용에어필터",
    "에어컨에어필터": "공조장치용에어필터",
    "실내필터": "공조장치용에어필터",
    "미션오일": "변속기오일",
    "오토미션오일": "변속기오일",
    "엔진오일": "엔진오일및오일필터",
    "오일필터": "엔진오일및오일필터",
    "브레이크오일": "브레이크액",
    "부동액": "냉각수",
    "에어필터": "에어클리너필터",
}


def today_kst() -> date:
    return datetime.now(KST).date()


# ---------------- 점검표 항목 찾기 (DB 없이 테스트할 수 있게 분리) ----------------
def _norm(text: str) -> str:
    return re.sub(r"[\s·,()\-_/]", "", str(text or "")).lower()


def match_items(query: str, names: list[str], limit: int = 3) -> list[str]:
    """사용자가 말한 항목 이름에 맞는 점검표 항목 이름들. 없으면 빈 목록."""
    q = _norm(query)
    for k in sorted(ALIASES, key=len, reverse=True):  # 긴 별칭부터, 한 번만 바꿈
        if k in q and ALIASES[k] not in q:
            q = q.replace(k, ALIASES[k])
            break
    if not q:
        return []
    exact = [n for n in names if _norm(n) == q]
    if exact:
        return exact
    contains = [n for n in names if q in _norm(n) or _norm(n) in q]
    if contains:
        return sorted(set(contains), key=lambda n: len(n))[:limit]
    scored = sorted(((SequenceMatcher(None, q, _norm(n)).ratio(), n) for n in set(names)), reverse=True)
    return [n for s, n in scored if s >= 0.6][:limit]


def applies(applies_to: str, powertrain: str) -> bool:
    """설명서 등록 때 고른 파워트레인에 해당하는 행만 쓴다 (FR-9)."""
    a = str(applies_to or "").strip()
    return a in ("", "전체") or (powertrain and powertrain in a)


def _pages_str(pages) -> str:
    return ", ".join(f"{p}쪽" for p in pages)


# ---------------- DB 조회 ----------------
def load_schedule(manual_id: str) -> list[dict]:
    with connect() as conn:
        cur = conn.execute(f"SELECT {', '.join(SCHEDULE_COLS)} FROM maintenance_schedule "
                           "WHERE manual_id=%s ORDER BY page, id", (manual_id,))
        return [dict(zip(SCHEDULE_COLS, r)) for r in cur.fetchall()]


def load_manual(manual_id: str) -> dict:
    with connect() as conn:
        r = conn.execute("SELECT name, car_model, powertrain, severe_selected, schedule_confirmed "
                         "FROM manuals WHERE manual_id=%s", (manual_id,)).fetchone()
        sev = dict(conn.execute("SELECT no, condition FROM severe_conditions WHERE manual_id=%s",
                                (manual_id,)).fetchall())
    return {"name": r[0], "car_model": r[1], "powertrain": r[2] or "",
            "severe_selected": {int(x) for x in (r[3] or "").split(",") if x.strip().isdigit()},
            "confirmed": bool(r[4]), "severe": sev}


def last_record(manual_id: str, item: str):
    with connect() as conn:
        return conn.execute("SELECT done_date, mileage FROM my_maintenance WHERE manual_id=%s AND item=%s "
                            "ORDER BY done_date DESC, id DESC LIMIT 1", (manual_id, item)).fetchone()


def list_records(manual_id: str) -> list[tuple]:
    with connect() as conn:
        return conn.execute("SELECT item, done_date, mileage FROM my_maintenance WHERE manual_id=%s "
                            "ORDER BY done_date DESC, id DESC", (manual_id,)).fetchall()


# ---------------- 도구 ----------------
def make_tools(manual_id: str):
    info = load_manual(manual_id)

    @tool(response_format="content_and_artifact")
    def search_manual(query: str):
        """취급설명서 본문을 검색한다. 기능 사용법, 경고등, 경고, 주의사항, 타이어 공기압, 비상 조치처럼
        설명서 본문에 있는 내용을 물을 때 쓴다. query에는 설명서에 나올 법한 단어를 넣는다."""
        docs = get_store().similarity_search(query, k=4, filter={"manual_id": manual_id})
        if not docs:
            return f"검색 결과가 없습니다. 이 경우 '{R6}'라고 답한다.", []
        pages = []
        for d in docs:
            if d.metadata["page"] not in pages:
                pages.append(d.metadata["page"])
        body = "\n\n".join(f"[PDF {d.metadata['page']}쪽]\n{d.page_content}" for d in docs)
        return body, pages

    @tool(response_format="content_and_artifact")
    def maintenance_info(item: str, current_km: Optional[int] = None):
        """정기 점검표에서 소모품의 교체·점검 주기를 찾고, 정비 기록이 있으면 다음 정비 시점을 계산해 돌려준다.
        '얼마마다 갈아?', '교체 주기는?', '다음 교체는 언제야?', '가혹 조건이면 주기가 달라져?'에 쓴다.
        item: 소모품 이름 (예: 엔진오일, 에어컨 필터, 변속기 오일). current_km: 질문에 현재 주행거리가 있으면 그 값."""
        rows = [r for r in load_schedule(manual_id) if applies(r["applies_to"], info["powertrain"])]
        if not rows:
            return "저장된 정기 점검표가 없습니다. 설명서 등록 탭에서 점검표를 확인하고 저장해야 합니다.", []
        names = list(dict.fromkeys(r["item"] for r in rows))
        found = match_items(item, names)
        if not found:
            return (f"점검표에서 '{item}' 항목을 찾지 못했습니다. 점검표 항목: " + ", ".join(names)), []

        lines, pages = [], []
        for name in found:
            rec = last_record(manual_id, name)
            last_date, last_km = (rec[0], rec[1]) if rec else (None, None)
            km_now = current_km if current_km is not None else last_km  # FR-12
            lines.append(f"■ {name}")
            if rec:
                lines.append(f"  마지막 정비 기록: {last_date}, " + (f"{last_km:,}km" if last_km else "주행거리 없음"))
            else:
                lines.append("  마지막 정비 기록: 없음")
            for r in (x for x in rows if x["item"] == name):
                res = next_due(r, last_date, last_km, current_km=km_now, today=today_kst())
                head = f"  [{r['section']} 조건, {r['action']}, PDF {r['page']}쪽]"
                rule = res["rule"] or res["message"]
                lines.append(f"{head} {rule}")
                if r["section"] == "가혹" and r["severe_conditions"]:
                    nos = [int(x) for x in str(r["severe_conditions"]).split(",") if x.strip().isdigit()]
                    conds = [f"{n}. {info['severe'].get(n, '')}" for n in nos]
                    mine = sorted(set(nos) & info["severe_selected"])
                    lines.append("    해당 운행 조건: " + " / ".join(conds))
                    lines.append("    사용자가 해당한다고 체크한 조건: "
                                 + (", ".join(map(str, mine)) if mine else "없음"))
                if res["next_date"] or res["next_km"]:
                    nxt = []
                    if res["next_date"]:
                        nxt.append(str(res["next_date"]))
                    if res["next_km"]:
                        nxt.append(f"{res['next_km']:,}km")
                    lines.append("    다음 시점: " + " 또는 ".join(nxt)
                                 + (" 중 먼저 오는 때" if len(nxt) == 2 else "")
                                 + (" (이미 지남)" if res["overdue"] else ""))
                elif rec and res["message"]:
                    lines.append(f"    {res['message']}")
                if r["page"] not in pages:
                    pages.append(r["page"])
        lines.append(f"(점검표 근거: {_pages_str(pages)})")
        return "\n".join(lines), pages

    @tool
    def add_maintenance(item: str, done_date: Optional[str] = None, mileage: Optional[int] = None) -> str:
        """사용자가 정비한 내용을 정비 기록에 저장한다. '오늘 엔진오일 갈았어, 기록해 줘'에 쓴다.
        item: 정비 항목. done_date: YYYY-MM-DD. 사용자가 '오늘'이라고 하거나 날짜를 말하지 않으면 비워 둔다.
        mileage: 사용자가 말한 주행거리(km). 말하지 않았으면 비워 둔다."""
        names = list(dict.fromkeys(r["item"] for r in load_schedule(manual_id)))
        found = match_items(item, names, limit=1)
        name = found[0] if found else item.strip()
        try:
            day = date.fromisoformat(done_date) if done_date else today_kst()
        except ValueError:
            return f"날짜 형식을 알 수 없습니다: {done_date}. YYYY-MM-DD 형식으로 다시 알려 달라고 답한다."
        with connect() as conn:
            cur = conn.execute("INSERT INTO my_maintenance (manual_id, item, done_date, mileage) "
                               "VALUES (%s,%s,%s,%s) ON CONFLICT (manual_id, item, done_date) DO NOTHING",
                               (manual_id, name, day, mileage))
            inserted = cur.rowcount == 1
        km = f", {mileage:,}km" if mileage else ""
        if not inserted:  # R-7
            return f"{name} {day} 기록은 이미 있습니다. 다시 저장하지 않았습니다."
        note = "" if found else " (점검표에 없는 항목 이름이라 말한 그대로 저장)"
        return f"저장했습니다: {name}, {day}{km}{note}"

    @tool
    def list_maintenance(item: Optional[str] = None) -> str:
        """저장된 정비 기록을 보여 준다. '브레이크액 마지막으로 언제 갈았어?'처럼 지난 기록을 물을 때 쓴다.
        item을 비우면 모든 항목의 기록을 보여 준다."""
        recs = list_records(manual_id)
        if item:
            names = list(dict.fromkeys(r[0] for r in recs))
            found = set(match_items(item, names))
            recs = [r for r in recs if r[0] in found]
        if not recs:
            return "정비 기록이 없습니다."
        return "\n".join(f"- {r[0]}: {r[1]}" + (f", {r[2]:,}km" if r[2] else "") for r in recs[:20])

    return [search_manual, maintenance_info, add_maintenance, list_maintenance], info


# ---------------- 시스템 프롬프트 (R-1 ~ R-9) ----------------
def system_prompt(info: dict) -> str:
    return f"""당신은 {info['car_model']}({info['powertrain']}) 취급설명서를 근거로 답하는 차량 관리 비서입니다.
오늘 날짜는 {today_kst()}입니다.

도구 고르기
- 기능 사용법, 경고등, 경고, 주의사항, 타이어 공기압, 비상 조치 → search_manual
- "얼마마다 갈아?", "교체 주기", "다음 교체는 언제야?", 가혹 조건과 주기 → maintenance_info
- "기록해 줘", "갈았어" → add_maintenance
- "마지막으로 언제 갈았어?" → list_maintenance
- 중고 시세, 정비소 추천, 다른 차와 비교처럼 설명서와 정비 기록으로 답할 수 없는 질문은 도구를 부르지 말고
  "{R6}"라고만 답합니다.

답하는 규칙
1. 도구 결과에 있는 숫자(거리, 기간, 압력, 용량)만 씁니다. 숫자를 만들어 내지 않습니다.
2. 다음 정비 시점은 직접 계산하지 말고 maintenance_info 결과의 '다음 시점'을 그대로 씁니다.
3. 주기를 말할 때는 통상 조건을 먼저 말하고, 가혹 조건 주기가 있으면 해당 운행 조건 문장과 함께 덧붙입니다.
4. 검색 결과에 답이 없으면 "{R6}"라고 답합니다.
5. 경고등, 브레이크, 타이어처럼 안전과 관련된 답에는 "증상이 계속되면 서비스센터 점검을 받으세요."를 붙이고,
   "계속 달려도 된다"고 단정하지 않습니다.
6. 쉬운 한국어 존댓말로 짧게 답합니다. 부품 이름은 설명서에 적힌 그대로 씁니다.
7. 쪽 번호는 답에 쓰지 않아도 됩니다. 근거 쪽 이미지는 화면이 따로 보여 줍니다."""


def build_graph(tools, info):
    llm = ChatBedrockConverse(model=os.environ["BEDROCK_MODEL_ID"],
                              region_name=os.environ.get("AWS_REGION"),
                              temperature=0, max_tokens=1500)
    llm_tools = llm.bind_tools(tools)
    sys_msg = SystemMessage(content=system_prompt(info))

    def model(state: MessagesState):
        return {"messages": [llm_tools.invoke([sys_msg] + state["messages"])]}

    g = StateGraph(MessagesState)
    g.add_node("model", model)
    g.add_node("tools", ToolNode(tools))
    g.add_edge(START, "model")
    g.add_conditional_edges("model", tools_condition)
    g.add_edge("tools", "model")
    return g.compile()


_THINK = re.compile(r"<thinking>.*?</thinking>", re.S)


def _text(msg: AIMessage) -> str:
    """Nova는 답 앞에 <thinking> 블록을 붙이기도 해서 지운다."""
    c = msg.content
    if isinstance(c, list):
        c = "".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    return _THINK.sub("", c or "").strip()


def run_agent(manual_id: str, question: str, history: Optional[list] = None, graph=None) -> dict:
    """질문 하나를 실행한다.
    history: 이전 대화 [{"role": "user"|"assistant", "content": str}] (최근 몇 개만 넘김)
    돌려주는 값은 agent_eval.py 형식에 화면용 칸을 더한 것:
    {"answer", "tools", "pages", "tool_calls": [{"name", "args", "output"}]}"""
    if graph is None:
        tools, info = make_tools(manual_id)
        graph = build_graph(tools, info)
    msgs = []
    for h in (history or [])[-6:]:
        msgs.append(HumanMessage(content=h["content"]) if h["role"] == "user" else AIMessage(content=h["content"]))
    msgs.append(HumanMessage(content=question))
    n_before = len(msgs)

    out = graph.invoke({"messages": msgs}, config={"recursion_limit": 10})
    new = out["messages"][n_before:]

    calls, outputs, pages = [], {}, []
    for m in new:
        if isinstance(m, AIMessage):
            for tc in m.tool_calls:
                calls.append({"id": tc["id"], "name": tc["name"], "args": tc["args"], "output": ""})
        elif isinstance(m, ToolMessage):
            outputs[m.tool_call_id] = m.content
            if m.name in ("search_manual", "maintenance_info") and m.artifact:
                for p in m.artifact:
                    if p not in pages:
                        pages.append(p)
    for c in calls:
        c["output"] = outputs.get(c.pop("id"), "")
    final = next((m for m in reversed(new) if isinstance(m, AIMessage) and not m.tool_calls), None)
    answer = _text(final) if final else "답을 만들지 못했습니다. 다시 질문해 주세요."
    if R6.rstrip(".") in answer:
        pages = []  # 거절한 답에는 근거 이미지를 붙이지 않음
    return {"answer": answer, "tools": [c["name"] for c in calls], "pages": pages, "tool_calls": calls}
