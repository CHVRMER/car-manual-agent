# 코드 설명서 (car-manual-agent)

이 문서는 코드를 처음 보는 팀원이 **어느 파일이 무슨 일을 하는지**, **질문 하나가 어떻게 처리되는지**, **무엇을 고치려면 어디를 봐야 하는지**를 빠르게 파악하도록 정리한 것입니다. 실행 방법은 `README.md`, 요구사항과 규칙(FR, R, AC 번호)은 `PRD.md`를 보세요.

> 2026-11-25 기준. `agent.py`와 에이전트 탭은 실제 Bedrock·DB로 아직 검증하지 않았습니다. 가짜 DB·모델로 흐름만 확인했습니다.

---

## 1. 파일 지도

| 파일 | 역할 | 주로 부르는 곳 |
|---|---|---|
| `app.py` | Streamlit 화면. 탭 3개(설명서 등록 / 에이전트 / 평가)와 사이드바 | 실행 진입점 |
| `db.py` | RDS 연결(`connect`), 벡터 저장소(`get_store`), 표 4개 만들기(`make_tables`) | 모든 곳 |
| `manual_utils.py` | PDF 처리: 해시, 쪽별 글자, 조각 나누기, 점검표 쪽 후보 찾기, 쪽 이미지 | `app.py` |
| `schedule_reader.py` | 점검표 쪽 이미지 + 쪽 텍스트 → Claude Haiku 4.5 → 표 행 | `app.py` (점검표 읽기 버튼) |
| `schedule_eval.py` | AC-7: 모델이 읽은 점검표와 정답 CSV 비교 | `app.py` (평가 탭) |
| `maintenance_calc.py` | 다음 정비 시점 계산 (`next_due`). DB·모델 없음 | `agent.py` |
| `agent.py` | 도구 4개 + LangGraph 에이전트 (`run_agent`) | `app.py` (에이전트 탭, 평가 탭) |
| `agent_eval.py` | AC-8 ~ AC-10 채점, setup 파싱 | `app.py` (평가 탭), 명령줄 |
| `test_*.py` | `maintenance_calc`, `agent_eval` 단위 테스트 (pytest) | `python -m pytest -q` |

| 데이터 | 내용 | git |
|---|---|---|
| `eval/schedule_ground_truth.csv` | 사람이 검증한 점검표 정답 (설명서별 행) | ✅ |
| `eval/severe_conditions.csv` | 설명서별 가혹 조건 번호와 문장 | ✅ |
| `eval/eval_questions.csv` | 평가 질문 15개, 기대 도구, 정답, 정답 쪽, setup | ✅ |
| `eval/eval_answers_draft.md` | 본문 질문 5개 정답을 찾은 근거 메모 | ✅ |
| `eval/extracted/{설명서 이름}.csv` | 모델이 읽은 그대로의 점검표 (사용자 수정 전). AC-7에 사용 | ✅ |
| `eval/agent_results/{날짜}.csv/.json` | 에이전트 평가 결과 | ✅ |
| `manuals/{manual_id}.pdf` | 업로드한 원본 PDF. 근거 이미지를 그릴 때 씀 | ❌ (저작물) |
| `data/` | 개발용 설명서 PDF, 쪽 이미지 | ❌ (저작물) |

---

## 2. 전체 흐름

```
[설명서 등록 탭]
PDF 업로드
 ├─ file_hash → manual_id (같은 파일 다시 올리면 건너뜀)
 ├─ manuals/{manual_id}.pdf 로 원본 저장
 ├─ read_pages → to_chunks(500자, 겹침 100) → Titan 임베딩 → pgvector   (메타데이터: manual_id, page)
 └─ manuals 표에 한 행

점검표 읽기
 ├─ find_schedule_pages → 쪽 범위 후보 (사용자가 고칠 수 있음)
 ├─ 쪽마다: render_page_png + 쪽 텍스트 → read_schedule_page (Haiku 4.5)
 ├─ eval/extracted/{이름}.csv 로 원본 저장  ← AC-7은 이 파일로 채점
 └─ 확인 화면(data_editor)에서 사람이 고침 + 가혹 조건 체크 → "확인하고 저장"
      → maintenance_schedule, severe_conditions 표, manuals.severe_selected

[에이전트 탭]
질문 → run_agent → LangGraph (model ⇄ tools) → 답 + 근거 쪽 이미지 + 도구 호출 과정

[평가 탭]
AC-7: eval/extracted vs 정답 CSV
AC-8~10: 질문 15개 실행 → agent_eval.score
```

---

## 3. DB 표 (`db.py`)

| 표 | 한 행 | 주요 칸 |
|---|---|---|
| `manuals` | 설명서 하나 | `manual_id`(파일 해시 16자), `name`(예: CN8_2027), `car_model`, `powertrain`, `severe_selected`(예: `1,3`), `schedule_confirmed` |
| `maintenance_schedule` | 점검표 한 항목의 한 동작 | `section`(통상/가혹), `item`, `applies_to`, `action`(교체/점검/없음), `schedule_type`(distance/daily/condition_based/none), `interval_km`, `interval_months`, `first_km`, `first_months`, `severe_conditions`(예: `1,4,5`) |
| `severe_conditions` | 가혹 조건 문장 하나 | `no`, `condition` |
| `my_maintenance` | 정비 기록 하나 | `item`, `done_date`, `mileage`. `UNIQUE(manual_id, item, done_date)` (R-7) |
| (pgvector) `car_manuals` 컬렉션 | 본문 조각 하나 | 메타데이터 `manual_id`, `page` |

- `manual_id`는 **파일 해시**이고, `name`은 사람이 붙인 **설명서 이름**입니다. 정답 CSV와 평가는 `name`(CN8_2027)으로, 나머지 표는 `manual_id`로 연결됩니다.
- `maintenance_schedule`에는 **사람이 확인하고 저장한 행만** 들어갑니다(R-4). 모델이 읽은 원본은 `eval/extracted/`에만 있습니다.

---

## 4. 점검표 읽기 (`schedule_reader.py`)

`read_schedule_page(png, page, last_section, page_text) -> PageResult`

1. 쪽 이미지와 **pypdfium2로 뽑은 쪽 텍스트**를 함께 보냅니다.
   - 이미지: ○/●가 어느 열에 있는지(주기) 판단용
   - 텍스트: 항목 이름을 정확히 복사하기 위한 용도. 이미지로만 읽으면 "냉각수량"을 "냉각수용"으로 읽는 등 오독이 많았습니다.
2. `with_structured_output(PageResult)`로 정해진 형식을 받습니다. 모델이 형식을 조금 어겨도(`"7,500"`, `"가혹 조건"`, `[1,2,3]`, `"●"`) validator가 바로잡습니다.
3. 받은 뒤 코드가 고치는 것:
   - `section`: 쪽 단위 `table` 값 → 쪽 텍스트가 "가혹"으로 시작하는지 → 앞 쪽 값 순서로 정함
   - 항목 이름 끝의 각주 기호(`*1`, `(*2)`) 제거
   - `_fill_from_raw`: `raw_text`의 "매 N km / N개월 / N년"으로 빈 주기 칸 채움. "최초"라는 글자가 없는데 `first_*`에 값이 있으면 `interval_*`로 옮김

**AC-7 기록**: 첫 측정(위 수정 전) 아반떼 10.4% (5/48). 원인은 ① 가혹 조건표 구분 오류 ② 각주 기호 ③ 최초/매 혼동 ④ 항목 이름 오독 ⑤ 열 위치 오독. ①~④를 고친 뒤 재측정 예정이고, EV6는 수정 후 한 번만 측정합니다(과적합 방지).

---

## 5. 에이전트 (`agent.py`)

### 5-1. 질문 하나가 처리되는 순서

```
run_agent(manual_id, question, history)
 ├─ make_tools(manual_id)   설명서마다 도구를 새로 만듦 → 검색·기록이 그 설명서 안에서만 (R-8)
 ├─ build_graph(tools, info)
 │    START → model ──(도구 호출 있음)──→ tools ──→ model ──(없음)──→ END
 │    model = Nova Lite + bind_tools + 시스템 프롬프트, recursion_limit=10
 ├─ graph.invoke(이전 대화 최대 6개 + 질문)
 └─ 새 메시지에서 정리
      tools      = 부른 도구 이름 (순서대로)        → AC-8
      pages      = search_manual, maintenance_info의 artifact 쪽 번호 → 근거 이미지, AC-9
      tool_calls = 도구 이름, 인자, 결과 글          → 화면의 "도구 호출 과정"
      answer     = 마지막 AI 답 (<thinking> 블록 제거)
      거절 답(R-6)이면 pages를 비움
```

### 5-2. 도구 4개

| 도구 | 언제 | 하는 일 | 돌려주는 것 |
|---|---|---|---|
| `search_manual(query)` | 기능 사용법, 경고등, 주의사항, 타이어 공기압 | pgvector에서 `manual_id`로 걸러 4조각 검색 | 글: `[PDF n쪽] 조각` / artifact: 쪽 번호 목록 |
| `maintenance_info(item, current_km)` | "얼마마다", "다음은 언제", 가혹 조건 | 점검표에서 항목 찾기 → 파워트레인 맞는 행만 → 통상·가혹 주기, 가혹 조건 문장, 사용자가 체크한 조건 → 기록이 있으면 `next_due`로 다음 시점 | 글: 항목별 정리 / artifact: 점검표 쪽 |
| `add_maintenance(item, done_date, mileage)` | "갈았어, 기록해 줘" | 항목 이름을 점검표 이름으로 맞춰 저장. 날짜 없으면 오늘(한국 시간). 같은 날짜·항목이면 저장 안 함 | 저장 결과 문장 |
| `list_maintenance(item)` | "마지막으로 언제 갈았어?" | 기록 조회 (최대 20개) | 기록 목록 |

- `response_format="content_and_artifact"`: 모델에게는 글만 가고, 쪽 번호는 화면이 따로 받습니다. 그래서 근거 이미지가 **모델이 답에 쓴 쪽 번호에 의존하지 않습니다**(FR-8).
- **계산은 모델이 하지 않습니다**(R-2). `maintenance_info`가 `next_due` 결과를 "다음 시점: 2027-08-29 또는 45,000km 중 먼저 오는 때"처럼 완성된 문장으로 넘기고, 프롬프트에서 그대로 쓰라고 지시합니다.
- 현재 주행거리는 질문에 있으면 그 값, 없으면 마지막 기록의 주행거리를 씁니다(FR-12).

### 5-3. 항목 이름 찾기 (`match_items`)

사용자 말과 점검표 이름이 다르기 때문에 다음 순서로 찾습니다.
1. 띄어쓰기와 기호를 지우고 비교 (`"엔진 오일"` = `"엔진오일"`)
2. `ALIASES`로 흔한 말 바꾸기 (`에어컨필터` → `공조장치용에어필터`, `미션오일` → `변속기오일` 등)
3. 같은 이름 → 포함 관계 → 비슷한 정도 0.6 이상 순서

확인된 연결: 엔진오일, 에어컨 필터, 브레이크액, 미션오일, 냉각수, 타이어 위치 교환, 에어필터, 점화플러그. **새 차종에서 못 찾는 이름이 생기면 `ALIASES`에 추가**하면 됩니다.

### 5-4. 시스템 프롬프트 (`system_prompt`)

- 차종, 파워트레인, **오늘 날짜**("오늘 갈았어" 처리용)
- 질문 유형 → 도구 고르는 기준 (AC-8에 직접 영향)
- 설명서 밖 질문은 도구 없이 R-6 문구
- R-1(숫자 지어내지 않기), R-2(계산 안 하기), R-5(안전 권고), R-9(존댓말, 부품 이름 그대로)

---

## 6. 평가

| 지표 | 무엇을 | 코드 | 실행 |
|---|---|---|---|
| AC-7 | 점검표 읽기 정확도: (구분, 항목, 적용, 동작)으로 행을 맞추고, 주기 종류·주기·최초 주기·조건 번호까지 모두 같으면 정답 | `schedule_eval.compare` | 평가 탭 "비교" |
| AC-8 | 처음 부른 도구 = 기대 도구 | `agent_eval._ac8` | 평가 탭 "에이전트 평가 실행" |
| AC-9 | 정답 쪽이 근거 이미지에 하나라도 / 모두 있는 비율 | `agent_eval._ac9` | 〃 |
| AC-10 | "문서에 없음" 3개에서 R-6 문구로 답함 | `agent_eval.score` | 〃 |

"에이전트 평가 실행"이 하는 일:
1. `my_maintenance`의 현재 최대 id를 기억
2. setup이 있는 질문(q08, q09)의 기록을 넣음
3. 질문 15개를 **대화 기록 없이 하나씩** 실행
4. 2~3 동안 생긴 기록을 모두 지움 (q10의 `add_maintenance` 포함)
5. `eval/agent_results/날짜.json`(원본), `날짜.csv`(채점표) 저장, 정답과 답을 나란히 보여 줌

**주의**: q08은 엔진 오일의 **가장 최근 기록**으로 계산합니다. 평가 전에 에이전트 탭에서 엔진 오일 기록을 더 최근 날짜로 넣어 두었다면 정답(2027-08-29 / 45,000km)과 달라집니다.

답 문장이 정답과 맞는지(정확도)는 자동 채점하지 않습니다. 화면의 "답 확인" 표를 사람이 봅니다.

---

## 7. 무엇을 고치려면 어디를

| 하고 싶은 것 | 파일·위치 |
|---|---|
| 도구를 잘못 고름 (AC-8) | `agent.py` `system_prompt`의 "도구 고르기", 각 도구의 docstring (모델이 읽는 설명) |
| 검색이 정답 쪽을 못 찾음 (AC-9) | `search_manual`의 `k=4`, `manual_utils.to_chunks`의 조각 크기 (바꾸면 **본문을 다시 저장**해야 함) |
| 정비 항목을 못 찾음 | `agent.py` `ALIASES`, `match_items` |
| 주기 계산 | `maintenance_calc.next_due` (+ `test_maintenance_calc.py`에 경우 추가) |
| 답 문장 형식, 안전 권고 | `system_prompt`의 "답하는 규칙" |
| 점검표 읽기 규칙 | `schedule_reader.py` `SYSTEM`, validator, `_fill_from_raw` |
| 사용하는 모델 | `.env`의 `BEDROCK_MODEL_ID`(에이전트), `BEDROCK_VISION_MODEL_ID`(점검표) |

---

## 8. 알려진 한계

- **최초 주기만 있는 행**: `next_due`가 기록이 없을 때 "최초"라는 말을 빼고, 기록이 있으면 아무 값도 내지 않습니다. 아반떼·EV6에는 이런 행이 없어서 지금은 영향이 없습니다.
- **pandas에서 나온 행**: 빈칸이 `NaN`이면 `next_due`에서 에러가 납니다. 도구는 psycopg 결과(빈칸 = `None`)를 그대로 넘기므로 문제없습니다.
- **가혹 조건 판단**: 사용자가 체크한 조건과 상관없이 가혹 조건 주기를 함께 보여 주고, "체크한 조건"을 따로 알려 줍니다. 어느 쪽을 기준으로 말할지는 모델이 판단합니다.
- **점검표 열 위치 오독**(AC-7의 ⑤): 3일 범위에서는 고치지 않습니다. 사람이 확인 화면에서 고쳐서 저장하는 것으로 대신합니다(FR-6).
- 에이전트는 실제 Bedrock·DB로 아직 검증하지 않았습니다. 처음 실행할 때 생기는 에러는 화면의 "에이전트 실행 중 오류" 메시지로 확인하세요.
