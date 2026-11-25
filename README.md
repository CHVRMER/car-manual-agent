# car-manual-agent

자동차 취급설명서 PDF를 올리면, 그 설명서를 근거로 질문에 답하고 정비 시기를 알려 주는 차량 관리 비서 에이전트입니다.

> 🚧 개발 중입니다. 설명서 등록과 점검표 읽기, 점검표 읽기 평가(AC-7)까지 구현했습니다. 에이전트 탭은 아직 비어 있습니다.

## 왜 만드나

취급설명서는 수백 쪽이라 대부분 읽지 않습니다. 그래서 소모품 교체 주기나 경고등의 의미를 인터넷에서 찾게 되는데, 검색 결과가 내 차에 맞는 정보인지 알기 어렵습니다. 이 프로젝트는 **내 차의 설명서**를 근거로 답하고, 근거가 된 쪽을 함께 보여 주는 것을 목표로 합니다.

## 주요 기능

- **설명서 등록**: 취급설명서 PDF를 한 번 올리면 본문을 검색할 수 있게 저장합니다.
- **정기 점검표 읽기**: 설명서의 점검표 쪽을 찾아 항목별 교체·점검 주기를 읽고, 사용자가 확인한 뒤 저장합니다.
- **설명서 Q&A**: 기능 사용법, 경고, 주의사항을 물으면 설명서를 검색해 답합니다.
- **근거 이미지**: 답 아래에 근거가 된 설명서 쪽을 이미지로 보여 줍니다.
- **정비 기록과 다음 정비 시기**: "오늘 엔진오일 갈았어"처럼 기록을 남기면, 점검표 주기와 비교해 다음 정비 시점을 알려 줍니다.

## 구조

```
PDF 업로드
 ├─ 본문 → 조각 나누기 → 임베딩 → pgvector
 └─ 점검표 쪽 → 비전 모델로 읽기 → 사용자 확인 → 점검표 테이블

질문 → LangGraph 에이전트 (model ⇄ tools)
         ├─ search_manual         설명서 본문 검색
         ├─ maintenance_info      점검표 주기 조회, 다음 정비 시기 계산
         ├─ add_maintenance       정비 기록 등록
         └─ list_maintenance      정비 기록 조회
     → 답 + 근거 쪽 이미지
```

## 기술 스택

| 구분 | 사용 기술 |
|---|---|
| 화면 | Streamlit |
| 에이전트 | LangChain, LangGraph |
| 모델 | Amazon Bedrock (Nova Lite, 비전 모델), Titan Text Embeddings V2 |
| 저장소 | Amazon RDS for PostgreSQL + pgvector |
| PDF 처리 | pypdfium2 |
| 배포 | Amazon EC2 |

## 실행 방법

로컬 PC에서 Streamlit 앱을 실행하는 방법입니다. DB(RDS)는 외부에서 직접 접속할 수 없어서 EC2를 거치는 SSH 터널이 필요합니다.

### 0. 준비물

팀장에게 아래를 따로 받으세요. **저장소나 채팅방 공개 채널에 올리지 않습니다.**

- `.env`에 넣을 실제 값 (모델 ID, DB 이름·계정·비밀번호, RDS 엔드포인트)
- EC2 접속용 키 파일(`.pem`)과 EC2 공인 IP (EC2를 껐다 켜면 IP가 바뀝니다)
- Bedrock을 호출할 수 있는 AWS 계정 권한

필요한 프로그램: Python 3.12, Git, AWS CLI v2

### 1. 코드 받기와 패키지 설치

```bash
git clone https://github.com/CHVRMER/car-manual-agent.git
cd car-manual-agent
python -m venv .venv
```

가상환경 켜기:

```powershell
# Windows (PowerShell)
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS
source .venv/bin/activate
```

패키지 설치:

```bash
python -m pip install -r requirements.txt
```

### 2. `.env` 만들기

`.env.example`을 복사해 `.env`를 만들고, 팀장에게 받은 값을 넣습니다. `.env`는 `.gitignore`에 들어 있어 커밋되지 않습니다.

로컬 실행에서는 아래 세 값이 EC2 배포 때와 다릅니다.

```
PG_HOST=localhost
PG_PORT=15432
AWS_PROFILE=본인의 AWS CLI 프로필 이름
```

### 3. AWS 로그인

```bash
aws login --profile 프로필이름
aws sts get-caller-identity --profile 프로필이름
```

두 번째 명령에서 계정 ID가 나오면 됩니다. 로그인 세션은 시간이 지나면 만료됩니다. 앱에서 `NoCredentialsError`나 `ExpiredToken`이 나오면 다시 로그인한 뒤 앱을 재시작하세요.

### 4. SSH 터널 열기 (터미널 A)

```bash
ssh -i 키파일경로.pem -N -L 15432:RDS엔드포인트:5432 ec2-user@EC2공인IP
```

- 처음 접속하면 `Are you sure you want to continue connecting`이 나옵니다. `yes`를 입력하세요.
- **아무 출력 없이 멈춰 있으면 성공입니다.** 이 창은 앱을 쓰는 동안 닫지 마세요.
- `Connection closed`가 나오면 EC2가 막 켜진 상태일 수 있으니 1~2분 뒤 다시 실행하세요.
- macOS에서 키 권한 오류가 나면 `chmod 400 키파일경로.pem`을 먼저 실행하세요.

### 5. 앱 실행 (터미널 B)

가상환경을 켠 뒤 실행합니다.

```bash
python -m streamlit run app.py
```

브라우저에서 http://localhost:8501 이 열립니다.

### 6. 사용 순서

1. **설명서 등록** 탭: PDF를 올리고 설명서 이름, 차종, 파워트레인을 입력합니다. 평가에 쓰는 설명서는 이름을 정답 CSV와 똑같이 입력해야 합니다(아반떼 `CN8_2027`, EV6 `CV1_2027`).
2. 본문 저장이 끝나면 점검표 쪽 범위를 확인하고 **점검표 읽기**를 누릅니다.
3. 읽은 결과를 설명서 이미지와 대조해 고치고, 해당하는 가혹 조건을 체크한 뒤 **확인하고 저장**을 누릅니다.
4. **평가** 탭에서 설명서를 고르고 **비교**를 누르면 점검표 읽기 정확도(AC-7)가 나옵니다. 모델이 처음 읽은 결과(`eval/extracted/설명서이름.csv`)로 채점하므로, 3번에서 고친 값은 점수에 반영되지 않습니다.

### 자주 나는 오류

| 오류 | 원인 | 해결 |
|---|---|---|
| `ConnectionTimeout` | SSH 터널이 꺼졌거나 `.env`가 RDS 주소를 직접 가리킴 | 터미널 A 확인, `PG_HOST=localhost`, `PG_PORT=15432` |
| `NoCredentialsError` | AWS 자격 증명 없음 | `.env`에 `AWS_PROFILE` 추가, `aws login` 후 앱 재시작 |
| `ModuleNotFoundError` | 가상환경이 꺼져 있거나 설치 실패 | 가상환경을 켜고 `python -m pip install -r requirements.txt` |
| `UnicodeDecodeError` (설치 중) | `requirements.txt`에 한글이 들어감 | `requirements.txt`는 영문으로만 작성 |

## 진행 상황

- [x] PRD 작성 ([PRD.md](PRD.md))
- [x] 점검표 정답 데이터, 평가 질문 세트
- [x] 설명서 업로드와 본문 저장
- [x] 점검표 읽기와 확인 화면
- [ ] 에이전트 (도구 4개, LangGraph)
- [ ] 근거 이미지 표시
- [ ] 평가 (점검표 읽기 정확도, 도구 선택 정확도, 근거 정확도)
- [ ] EC2 배포

## 시연 시나리오

에이전트 탭에서 아래 다섯 질문을 순서대로 보여 줍니다.

1. **타이어 적정 공기압은 얼마야?** 설명서를 검색하고 15쪽 근거 이미지를 보여 줍니다. 규격이 세 가지라 모두 말하거나 규격을 되묻습니다.
2. **엔진오일은 얼마마다 갈아?** 점검표에서 통상 15,000km 또는 12개월, 가혹 7,500km 또는 6개월을 같이 말합니다.
3. **오늘 엔진오일 갈았어. 30,000km야. 기록해 줘.** 정비 기록을 하나 저장합니다.
4. **다음 엔진오일 교체는 언제야?** 방금 기록과 통상 주기로 2027년 같은 날 또는 45,000km 중 먼저 오는 시점을 계산합니다.
5. **이 차 중고 시세는 얼마야?** 도구를 부르지 않고 "취급설명서에서 찾을 수 없는 내용입니다."라고 답합니다.

## 평가 결과

숫자는 측정 후에 채웁니다. AC-9는 정답 쪽 중 하나라도 보여 준 비율과, 정답 쪽을 모두 보여 준 비율을 같이 적습니다.

| 지표 | 대상 | 맞은 수 | 비율 |
|---|---|---|---|
| AC-7 아반떼 수정 전 | 아반떼 점검표 행 | | |
| AC-7 아반떼 수정 후 | 아반떼 점검표 행 | | |
| AC-7 EV6 | EV6 점검표 행 | | |
| AC-8 도구 선택 | 평가 질문 15개 | | |
| AC-9 근거 쪽 (하나라도) | 정답 쪽이 있는 질문 | | |
| AC-9 근거 쪽 (모두) | 정답 쪽이 있는 질문 | | |
| AC-10 거절 | 문서에 없음 3개 | | |

## 참고

- 개발과 평가에는 현대자동차·기아 공식 사이트에서 공개한 취급설명서를 사용합니다. 설명서 PDF는 제조사 저작물이므로 이 저장소에 포함하지 않습니다.
- 산업AI 부트캠프 「Vibe to Production: 바이브코딩 기반 풀스택 AI 에이전트 구축」 과정의 실습 내용을 바탕으로 진행하는 프로젝트입니다.
