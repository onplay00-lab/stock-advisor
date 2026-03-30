# Stock Advisor - 통합 포트폴리오 대시보드

한국투자증권 + 업비트 API 연동 실시간 주식/코인 포트폴리오 관리 도구

## 기능
- 한투 API 연동: 국내주식, 해외주식(미국), ISA 계좌 실시간 조회
- 업비트 API 연동: 암호화폐 실시간 시세
- 종합 분석: 기본적 분석(PER/PBR/ROE) + 기술적 분석(RSI/MACD)
- 공격형 투자 스타일 매매 판단
- ISA 세제혜택 관리 (비과세 한도, 손익통산)

## 실행 방법

### 1. 패키지 설치
```bash
pip install fastapi uvicorn httpx pyjwt python-dotenv
```

### 2. 환경변수 설정
```bash
cp .env.example .env
```
`.env` 파일을 열어 API 키를 입력하세요.

### 3. 서버 실행
```bash
python server.py
```

### 4. 브라우저 접속
`http://localhost:8001` 접속

## 웹 버전 (서버 없이)
`stock-advisor.html`을 브라우저에서 직접 열면 Yahoo Finance + Upbit 공개 API로 시세를 가져옵니다 (한투 계좌 데이터 제외).

## GitHub Pages
이 저장소의 GitHub Pages가 활성화되어 있으면 웹에서 바로 대시보드를 볼 수 있습니다.
