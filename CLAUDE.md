# Stock Advisor

## 프로젝트 목적
한국투자증권(KIS) + 업비트 API + Anthropic Claude를 연동한 실시간 주식/코인 포트폴리오 대시보드. FastAPI 백엔드(`server.py`) + 단일 페이지 프론트(`index.html`). Vercel Serverless 함수는 `api/` 아래(`analyze.py`, `portfolio.py`, `price.py`).

## 도구 / 스택
- Python 3, FastAPI, httpx, pyjwt, python-dotenv
- 단일 HTML/JS 프론트 (빌드 도구 없음)
- 배포: Vercel (`api/*.py` = Serverless), GitHub Pages는 정적 페이지용
- 비밀키는 프로젝트 루트 `.env` (gitignored). `server.py`가 `os.getenv`로 로드.

## 검증 방법
변경 후 반드시 확인:
1. **로컬 실행**: `python server.py` → `http://localhost:8001` 에서 정상 응답
2. **Python 문법**: `python -m py_compile server.py api/*.py`
3. **민감정보 누출 금지**: `.env`, API 키, 토큰을 코드/로그에 하드코딩하지 말 것

## 절대 규칙
- `.env` 파일은 절대 커밋하지 않는다 (`.gitignore` 확인).
- API 키·시크릿·토큰을 응답/로그/주석에 출력하지 않는다. 디버깅 시에도 마스킹(앞 4자리만).
- `main` 브랜치에 직접 force-push 금지. PR로 머지.
- 매매 주문 API(`/uapi/domestic-stock/v1/trading/*`)는 **사용자가 명시적으로 요청한 경우에만** 호출 코드를 작성한다. 자동/추측으로 추가 금지.
- 응답 데이터는 한국어 키/메시지 유지 (기존 컨벤션).

## 트리거 단어
- "서버 켜줘" / "run" → `python server.py` (백그라운드)
- "배포" → Vercel 배포 흐름 확인 (`/api` 변경 후)
- "검증" → 위 검증 3단계 실행

## 컨벤션
- 한국어 주석/문서 OK. 변수명은 영어.
- 새 엔드포인트는 `api/` 하위 단일 파일로 추가 (Vercel 서버리스 패턴).
- 외부 API 호출은 `httpx.AsyncClient` 사용, 타임아웃 명시.
