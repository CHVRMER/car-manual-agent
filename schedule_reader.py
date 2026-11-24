"""점검표 쪽 이미지를 Claude Haiku 4.5로 읽어 정답 CSV와 같은 형식의 행으로 받음."""
import base64
import os
import re
from typing import Literal, Optional

from dotenv import load_dotenv
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

load_dotenv()


class ScheduleRow(BaseModel):
    section: Optional[Literal["통상", "가혹"]] = Field(
        default=None, description="비워 둔다. 쪽 단위 table 값으로 코드가 채운다")
    item: str = Field(description="표에 적힌 점검 항목 이름 그대로. 각주 기호(*1, 1) 등)만 뺀다")
    applies_to: str = Field(default="전체",
                            description="엔진·연료·구동 방식·사양별 행이면 표에 적힌 그 이름, 아니면 '전체'")
    action: Literal["교체", "점검", "없음"] = Field(description="●는 교체, ○는 점검. 무점검·무교체는 없음")
    schedule_type: Literal["distance", "daily", "condition_based", "none"] = Field(
        description="거리나 기간 주기=distance, 일일 점검 열=daily, '상태에 따라 수시'=condition_based, 무점검·무교체=none")
    interval_km: Optional[int] = Field(default=None, description="반복 주기 km. 표에 없으면 비움")
    interval_months: Optional[int] = Field(default=None, description="반복 주기 개월. 표에 없으면 비움")
    first_km: Optional[int] = Field(default=None, description="'최초 ○○ km' 처럼 최초 주기가 따로 있을 때만")
    first_months: Optional[int] = Field(default=None, description="최초 주기 개월. 10년은 120")
    severe_conditions: str = Field(default="", description="가혹 조건표의 운행 조건 번호. 예: '2,5'")
    footnote: str = Field(default="", description="이 항목에 달린 각주 문장")
    raw_text: str = Field(default="", description="이 행의 주기 칸 원문 그대로")


class SevereCondition(BaseModel):
    no: int
    condition: str


class PageResult(BaseModel):
    table: Optional[Literal["통상", "가혹", "없음"]] = Field(
        default=None, description="이 쪽의 표가 통상 조건 점검표면 '통상', 가혹 조건표면 '가혹', 점검표가 없으면 '없음'")
    rows: list[ScheduleRow] = Field(default_factory=list)
    severe_conditions: list[SevereCondition] = Field(
        default_factory=list, description="이 쪽에 번호가 붙은 가혹 조건 목록이 있으면 번호와 문장")


SYSTEM = """당신은 자동차 취급설명서의 정기 점검표를 표 데이터로 옮기는 작업자입니다.
규칙:
1. 한 행은 한 항목의 한 동작입니다. 한 항목에 ○와 ●가 모두 있으면 두 행으로 나눕니다. 일일 점검 열의 ○도 별도 행(schedule_type=daily)입니다.
2. ○/●가 어느 열에 있는지는 열 머리글(예: 매 1만, 매 2만 ...)을 기준으로 판단합니다. 열 너비가 일정하지 않으니 주의합니다.
3. action은 항목 이름이 아니라 기호를 따릅니다. ●=교체, ○=점검.
4. '또는 12개월'처럼 기간이 함께 있으면 interval_months에 넣습니다. 10년은 120개월입니다.
5. 이미지에 보이지 않는 숫자는 만들지 말고 비웁니다.
6. 점검표가 아닌 쪽이면 rows를 빈 목록으로 둡니다.
7. 가혹 조건표의 '운행 조건' 번호는 severe_conditions에 '1,2,3'처럼 씁니다. 번호가 붙은 가혹 조건 문장 목록이 이 쪽에 있으면 severe_conditions 목록으로도 돌려줍니다.
8. 이 쪽의 표가 통상 조건표인지 가혹 조건표인지 table에 반드시 씁니다.
9. '매 10,000 km'처럼 반복되는 주기는 interval_km / interval_months에 넣습니다. first_km / first_months는 표에 '최초'라는 글자가 있을 때만 씁니다.
10. 항목 이름(item)은 함께 주는 '쪽 텍스트'에서 글자 그대로 복사합니다. 이미지의 글자를 새로 읽지 않습니다. 텍스트에서 줄바꿈으로 끊긴 이름은 이어 붙이고, 끝의 각주 기호(*1, *2 등)는 뺍니다.
11. ○/●의 열 위치와 표 구조는 이미지를 기준으로 판단합니다. 쪽 텍스트는 칸 위치 정보가 없습니다."""

reader = ChatBedrockConverse(model=os.environ["BEDROCK_VISION_MODEL_ID"],
                             region_name=os.environ["AWS_REGION"],
                             temperature=0, max_tokens=8192)
structured = reader.with_structured_output(PageResult)


_FOOTNOTE = re.compile(r"\s*\(?\*\d+\)?\s*$")


def _num(s: str) -> int:
    return int(s.replace(",", ""))


def _fill_from_raw(r: "ScheduleRow") -> None:
    """모델이 주기 칸을 비웠거나 '매' 주기를 최초 칸에 넣은 경우를 raw_text로 바로잡음."""
    raw = r.raw_text or ""
    if r.interval_km is None:
        m = re.search(r"매\s*([\d,]+)\s*km", raw)
        if m:
            r.interval_km = _num(m.group(1))
    if r.interval_months is None:
        m = re.search(r"매\s*(\d+)\s*개월", raw) or re.search(r"매\s*(\d+)\s*년", raw)
        if m:
            r.interval_months = int(m.group(1)) * (12 if m.group(0).endswith("년") else 1)
    if "최초" not in raw + (r.footnote or ""):  # 표에 '최초'가 없으면 최초 칸은 '매' 주기를 잘못 넣은 것
        if r.interval_km is None and r.first_km is not None:
            r.interval_km = r.first_km
        if r.interval_months is None and r.first_months is not None:
            r.interval_months = r.first_months
        r.first_km = r.first_months = None


def read_schedule_page(png: bytes, page: int, last_section: Optional[str] = None,
                       page_text: str = "") -> PageResult:
    """png: 쪽 이미지, page_text: pypdfium2로 뽑은 같은 쪽 글자(항목 이름 복사용).
    last_section: 앞 쪽의 표 구분. 모델이 table을 비웠을 때만 씀."""
    b64 = base64.b64encode(png).decode("utf-8")
    msg = HumanMessage(content=[
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        {"type": "text", "text": f"PDF {page}쪽입니다. 이 쪽의 정기 점검표를 규칙대로 옮겨 주세요.\n\n"
                                 f"[쪽 텍스트]\n{page_text}"},
    ])
    res = structured.invoke([SystemMessage(content=SYSTEM), msg])

    # 표 구분은 쪽 단위로 정함: 모델 값 > 쪽 텍스트 첫 줄 > 앞 쪽 값
    if res.table in ("통상", "가혹"):
        section = res.table
    elif page_text.lstrip().startswith("가혹"):
        section = "가혹"
    else:
        section = last_section or "통상"
    for r in res.rows:
        r.section = section
        r.item = _FOOTNOTE.sub("", r.item).strip()
        _fill_from_raw(r)
    return res
