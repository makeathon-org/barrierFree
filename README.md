# 배리어프리 보행 경로 추천 (성신여대 일대)

사용자 유형(휠체어 · 어르신 · 다리부상 · 무거운 짐)에 따라 계단·급경사·거친 노면·
좁은 길 등 보행 장애물을 다르게 평가해 **안전 / 균형 / 빠름** 3가지 경로를 동시에
추천하는 웹앱입니다. 1박 2일 해커톤 데모용.

- **백엔드**: FastAPI + osmnx + networkx (OSM 보행 네트워크 기반 경로 탐색)
- **프론트엔드**: React + Vite + 카카오맵 JavaScript SDK
- **데모 구역**: 성신여대입구역 중심 (37.5925, 127.0163), 반경 800m

```
barrierfree/
├── backend/
│   ├── main.py            # FastAPI 엔드포인트
│   ├── routing.py         # 경로 비용/탐색 로직 (osmnx 비의존, 테스트 가능)
│   ├── graph.py           # OSM 그래프 적재 + 특성 분류 + 수동 태깅
│   ├── test_routing.py    # routing 단위 테스트 (networkx만 필요)
│   ├── requirements.txt
│   └── sungshin_walk.graphml   # (최초 실행 시 자동 생성되는 OSM 캐시)
└── frontend/
    ├── index.html
    ├── src/{App.jsx, api.js, geo.js, useKakao.js, styles.css, main.jsx}
    ├── .env.example
    └── package.json
```
<img width="1369" height="739" alt="메인 화면 - 사용자 유형 선택, 지점 지정, 계단·제보 레이어" src="https://github.com/user-attachments/assets/4603083b-16d2-40e3-be84-2546ac0cd6f4" />
<img width="2880" height="1524" alt="경사도 오버레이 - 구간별 경사도 4단계 색상 표시" src="https://github.com/user-attachments/assets/c959c7a4-dae8-4374-b0c4-cf1b9e24aa22" />

---

## 1. 백엔드 실행 (포트 8000)

요구사항: Python 3.10+

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

- **최초 실행 시** OSM 에서 성신여대 일대 보행 네트워크를 내려받아
  `sungshin_walk.graphml` 로 캐싱합니다(인터넷 필요, 수십 초 소요).
  이후 실행부터는 캐시를 사용합니다.
- osmnx 설치가 불가하거나 OSM 다운로드가 실패해도, 데모가 멈추지 않도록
  동일 좌표계의 **합성 격자 그래프**로 자동 대체됩니다.
- 단위 테스트: `python test_routing.py` (또는 `pytest`)

### AI 경로 추천 (Gemini, 하이브리드)

경로 자체는 **그래프 알고리즘**이 계산해 실제 보행로를 따라가도록 보장하고,
**Gemini**는 그 3개 후보의 데이터 요약(거리·시간·계단/경사/노면 개수·최대경사)을 받아
사용자 유형에 가장 적합한 경로를 **추천하고 이유를 설명**합니다.
(LLM이 좌표를 직접 생성하면 도로를 벗어나므로, 추천·설명 역할만 맡깁니다.)

설정: `backend/.env` 에 키를 넣습니다 (`.env.example` 참고).
```
GEMINI_API_KEY=...        # https://aistudio.google.com/apikey
GEMINI_MODEL=gemini-3.5-flash   # (선택, 기본값)
```
- 키가 없거나 호출이 실패하면 **규칙 기반 폴백 추천**으로 자동 대체되어 데모가 멈추지 않습니다
  (응답의 `recommendation.source` 가 `gemini` / `fallback` 로 표시됨).
- ⚠️ 보안: API 키는 `backend/.env`(gitignore됨)에 두세요. `.env.example` 에는 실제 키를 넣지 마세요.

### 주요 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/route?orig_lat&orig_lng&dest_lat&dest_lng&user_type` | safe·balanced·fast 3경로 반환 |
| GET | `/recommend?orig_lat&orig_lng&dest_lat&dest_lng&user_type` | **AI(Gemini) 경로 추천** — 3경로 요약을 분석해 추천+이유 |
| GET | `/stairs` | 계단 구간 목록 |
| GET | `/reports` | 위험 제보 목록 |
| POST | `/reports` | 위험 제보 추가 `{lat,lng,type,desc}` |
| GET | `/weather` | 날씨(표시 전용 스텁) |
| GET | `/health` | 헬스체크 |

`user_type` ∈ `wheelchair`(휠체어) · `elderly`(어르신·노약자) ·
`leg_injury`(다리부상) · `heavy_luggage`(무거운 짐)

예시:
```
http://localhost:8000/route?orig_lat=37.5894&orig_lng=127.0197&dest_lat=37.5916&dest_lng=127.018&user_type=wheelchair
```

---

## 2. 프론트엔드 실행 (포트 5173)

요구사항: Node.js 18+

### 2-1. 카카오맵 JavaScript 키 발급

1. [카카오 개발자 콘솔](https://developers.kakao.com) 로그인 → **내 애플리케이션** → 애플리케이션 추가
2. 생성한 앱 → **앱 키** 에서 **JavaScript 키** 복사
3. **앱 설정 → 플랫폼 → Web** 에서 사이트 도메인에 아래를 등록
   - `http://localhost:5173`
   - (필요 시 `http://127.0.0.1:5173`)
4. (검색 기능용) **카카오맵 / 로컬** API 활성화 — 보통 JavaScript 키로 바로 사용 가능

### 2-2. 환경변수 설정 & 실행

```bash
cd frontend
cp .env.example .env
# .env 를 열어 발급받은 JavaScript 키를 넣습니다.
#   VITE_KAKAO_KEY=발급받은_자바스크립트_키
#   VITE_API_BASE=http://localhost:8000

npm install
npm run dev
```

브라우저에서 **http://localhost:5173** 접속.

> 키가 비어있거나 도메인 미등록이면 지도가 표시되지 않고 안내 메시지가 뜹니다.
> 그 경우 위 도메인 등록과 `.env` 의 `VITE_KAKAO_KEY` 를 다시 확인하세요.

---

## 3. 사용법 (데모 흐름)

1. 상단에서 **날씨**가 표시됩니다(표시 전용).
2. **사용자 유형** 칩 선택 (휠체어 / 어르신·노약자 / 다리부상 / 무거운 짐).
3. **지점 지정**: "출발지 찍기 / 도착지 찍기" 모드 선택 후 지도 클릭, 또는
   장소 검색 결과의 "출발 / 도착" 버튼으로 지정. (기본 출발/도착이 미리 찍혀 있어 바로 데모 가능)
4. **경로 찾기** → safe(초록) · balanced(주황) · fast(파랑) 3경로가 지도에 동시에 그려지고
   카드에 거리·시간·경고가 표시됩니다. 카드를 클릭하면 해당 경로가 강조됩니다.
5. **제보 추가** 모드로 지도를 클릭하면 위험 제보를 등록(POST)할 수 있습니다(보라색 마커).
6. **안내 시작 (시뮬)** 을 누르면 선택한 경로를 따라 마커가 이동하며,
   위험 제보 마커 **30m 이내**에 접근하면 경고 토스트가 뜹니다.
<img width="1407" height="791" alt="경로추천" src="https://github.com/user-attachments/assets/29e3dea2-f7e4-4a2a-a7d9-f5c1963f622b" />

---

## 4. 라우팅 개념 요약

간선 비용:
```
cost = length × dist_weight[유형] × (1 + strategy_scale × Σ penalty[유형][특성])
```
- **하드 배제(exclude)**: 해당 특성 간선은 그래프에서 제거 (전략 무관, 항상 적용).
  예) 휠체어는 `stairs`, `wheelchair_no` 간선을 사용하지 않음.
- **strategy_scale**: safe=1.0, balanced=0.5, fast=0.1 — 클수록 장애물을 더 강하게 회피.
- 배제 때문에 경로가 끊기면, 배제를 ×1000 페널티로 완화해 재탐색하고 `warning` 을 함께 반환.
- 보행 속도 72 m/min 으로 소요 시간(분) 계산.

OSM 태그 → 특성: `highway=steps`→stairs,
`surface`(unpaved/gravel/cobblestone 등)→rough, `width`/`est_width`<1.2m→narrow,
`wheelchair=no`→wheelchair_no. (듬성한 OSM 보강용 수동 태깅 `HAND_TAGS` 포함)

### 경사도(steep) — 서울시 표고 데이터 기반

`backend/data/sungshin_height.json` (성신여대 일대 표고 점군 약 3만 개,
`{lat,lng,HEIGHT}`)를 사용합니다. 서버 시작 시:
1. 각 그래프 노드 고도 = 가장 가까운 표고점 3개의 평균으로 보간
2. 간선 경사 `grade = Δ고도 / 길이` 계산 → `grade_abs > 8%` 면 `steep` 으로 분류

성신여대 일대는 실제로 언덕이 많아 전체 보행 간선의 약 **22%(≈560개)** 가
경사 구간으로 잡히며, 유형별 `steep` 페널티(휠체어 20 · 어르신 5 · 다리부상 3 ·
무거운 짐 6)에 따라 경로가 서로 다르게 우회합니다.
OSM `incline` 태그는 보조로만 사용됩니다(한국 데이터가 희소).
표고 파일이 없으면 경사 보간은 자동으로 생략됩니다.
```
