"""
Stock Advisor API Server
========================
한국투자증권 OpenAPI + 업비트 API 연동 FastAPI 서버

Requirements:
  pip install fastapi uvicorn httpx pyjwt python-dotenv --break-system-packages

실행: python server.py
또는: uvicorn server:app --reload --port 8001
"""

import os
from dotenv import load_dotenv
load_dotenv()  # .env 파일에서 환경변수 로드
import time
import hashlib
import uuid
import pathlib
from typing import Optional
from datetime import datetime

import httpx
import jwt
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

# ─── 설정 ─────────────────────────────────────────────────────
# 한국투자증권 API
KIS_APP_KEY = os.getenv("KIS_APP_KEY", "")
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "")
KIS_CANO = os.getenv("KIS_CANO", "")
KIS_ACNT_PRDT_CD = os.getenv("KIS_ACNT_PRDT_CD", "01")
KIS_ISA_CANO = os.getenv("KIS_ISA_CANO", "")
KIS_BASE_URL = "https://openapi.koreainvestment.com:9443"

# 업비트 API
UPBIT_ACCESS_KEY = os.getenv("UPBIT_ACCESS_KEY", "")
UPBIT_SECRET_KEY = os.getenv("UPBIT_SECRET_KEY", "")
UPBIT_BASE_URL = "https://api.upbit.com"

# GitHub Models API (gpt-4o-mini via GitHub)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_MODELS_URL = "https://models.github.ai/inference/chat/completions"
GITHUB_MODEL = "openai/gpt-4o-mini"

# ─── 토큰 관리 ────────────────────────────────────────────────
_kis_token = {"access_token": "", "expires_at": 0}


app = FastAPI(title="Stock Advisor API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── 정적 파일 서빙 ──────────────────────────────────────────

@app.get("/")
async def serve_html():
    """index.html 서빙"""
    html_path = pathlib.Path(__file__).parent / "index.html"
    return FileResponse(html_path, media_type="text/html")


# ─── 한국투자증권 API ─────────────────────────────────────────

async def get_kis_token() -> str:
    """한투 OAuth 토큰 발급 (캐싱)"""
    global _kis_token
    if _kis_token["access_token"] and time.time() < _kis_token["expires_at"]:
        return _kis_token["access_token"]

    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.post(
            f"{KIS_BASE_URL}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": KIS_APP_KEY,
                "appsecret": KIS_APP_SECRET,
            },
        )
        data = resp.json()
        if "access_token" not in data:
            raise HTTPException(status_code=401, detail=f"한투 토큰 발급 실패: {data}")
        _kis_token["access_token"] = data["access_token"]
        _kis_token["expires_at"] = time.time() + int(data.get("expires_in", 86400)) - 60
        return data["access_token"]


def kis_headers(token: str, tr_id: str) -> dict:
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": tr_id,
    }


@app.get("/api/kis/domestic/balance")
async def kis_domestic_balance():
    """국내 주식 잔고 조회"""
    token = await get_kis_token()
    headers = kis_headers(token, "TTTC8434R")  # 실전: TTTC8434R
    params = {
        "CANO": KIS_CANO,
        "ACNT_PRDT_CD": KIS_ACNT_PRDT_CD,
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "01",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }
    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.get(
            f"{KIS_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance",
            headers=headers,
            params=params,
        )
        data = resp.json()

    holdings = []
    for item in data.get("output1", []):
        holdings.append({
            "name": item.get("prdt_name", ""),
            "code": item.get("pdno", ""),
            "market": "KRX",
            "quantity": int(item.get("hldg_qty", 0)),
            "avgPrice": float(item.get("pchs_avg_pric", 0)),
            "currentPrice": float(item.get("prpr", 0)),
            "evalAmount": float(item.get("evlu_amt", 0)),
            "evalPnl": float(item.get("evlu_pfls_amt", 0)),
            "returnPct": float(item.get("evlu_pfls_rt", 0)),
        })

    # output2에서 계좌 요약
    summary = data.get("output2", [{}])
    if isinstance(summary, list) and summary:
        summary = summary[0]

    return {
        "holdings": holdings,
        "summary": {
            "totalEvalAmount": float(summary.get("tot_evlu_amt", 0)),
            "totalPurchaseAmount": float(summary.get("pchs_amt_smtl_amt", 0)),
            "totalPnl": float(summary.get("evlu_pfls_smtl_amt", 0)),
            "availableCash": float(summary.get("dnca_tot_amt", 0)),
            "totalAsset": float(summary.get("scts_evlu_amt", 0)) + float(summary.get("dnca_tot_amt", 0)),
        },
    }


@app.get("/api/kis/isa/balance")
async def kis_isa_balance():
    """ISA 계좌 잔고 조회"""
    token = await get_kis_token()
    headers = kis_headers(token, "TTTC8434R")  # 실전: TTTC8434R
    params = {
        "CANO": KIS_ISA_CANO,
        "ACNT_PRDT_CD": KIS_ACNT_PRDT_CD,
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "01",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }
    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.get(
            f"{KIS_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance",
            headers=headers,
            params=params,
        )
        data = resp.json()

    holdings = []
    for item in data.get("output1", []):
        holdings.append({
            "name": item.get("prdt_name", ""),
            "code": item.get("pdno", ""),
            "market": "KRX",
            "quantity": int(item.get("hldg_qty", 0)),
            "avgPrice": float(item.get("pchs_avg_pric", 0)),
            "currentPrice": float(item.get("prpr", 0)),
            "evalAmount": float(item.get("evlu_amt", 0)),
            "evalPnl": float(item.get("evlu_pfls_amt", 0)),
            "returnPct": float(item.get("evlu_pfls_rt", 0)),
        })

    # output2에서 계좌 요약
    summary = data.get("output2", [{}])
    if isinstance(summary, list) and summary:
        summary = summary[0]

    return {
        "holdings": holdings,
        "summary": {
            "totalEvalAmount": float(summary.get("tot_evlu_amt", 0)),
            "totalPurchaseAmount": float(summary.get("pchs_amt_smtl_amt", 0)),
            "totalPnl": float(summary.get("evlu_pfls_smtl_amt", 0)),
            "availableCash": float(summary.get("dnca_tot_amt", 0)),
            "totalAsset": float(summary.get("scts_evlu_amt", 0)) + float(summary.get("dnca_tot_amt", 0)),
        },
    }


@app.get("/api/kis/overseas/balance")
async def kis_overseas_balance():
    """해외 주식 잔고 조회"""
    token = await get_kis_token()
    headers = kis_headers(token, "TTTS3012R")  # 실전: TTTS3012R (미국)
    params = {
        "CANO": KIS_CANO,
        "ACNT_PRDT_CD": KIS_ACNT_PRDT_CD,
        "OVRS_EXCG_CD": "NASD",  # NASD, NYSE, AMEX
        "TR_CRCY_CD": "USD",
        "CTX_AREA_FK200": "",
        "CTX_AREA_NK200": "",
    }

    all_holdings = []

    # NASD + NYSE 모두 조회
    for exchange in ["NASD", "NYSE", "AMEX"]:
        params["OVRS_EXCG_CD"] = exchange
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.get(
                f"{KIS_BASE_URL}/uapi/overseas-stock/v1/trading/inquire-balance",
                headers=headers,
                params=params,
            )
            data = resp.json()

        for item in data.get("output1", []):
            qty = float(item.get("ovrs_cblc_qty", 0))
            if qty <= 0:
                continue
            all_holdings.append({
                "name": item.get("ovrs_item_name", ""),
                "code": item.get("ovrs_pdno", ""),
                "market": "US",
                "exchange": exchange,
                "quantity": qty,
                "avgPrice": float(item.get("pchs_avg_pric", 0)),
                "currentPrice": float(item.get("now_pric2", 0) or item.get("ovrs_now_pric", 0)),
                "evalAmount": float(item.get("ovrs_stck_evlu_amt", 0)),
                "evalPnl": float(item.get("frcr_evlu_pfls_amt", 0)),
                "returnPct": float(item.get("evlu_pfls_rt", 0)),
                "currency": "USD",
            })

    # 요약
    summary_data = data.get("output2", {})
    if isinstance(summary_data, list) and summary_data:
        summary_data = summary_data[0]

    return {
        "holdings": all_holdings,
        "summary": {
            "totalEvalAmount": float(summary_data.get("tot_evlu_pfls_amt", 0)),
            "availableForeignCurrency": float(summary_data.get("frcr_use_psbl_amt", 0)),
        },
    }


@app.get("/api/kis/domestic/price/{stock_code}")
async def kis_domestic_price(stock_code: str):
    """국내 주식 현재가 조회"""
    token = await get_kis_token()
    headers = kis_headers(token, "FHKST01010100")
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": stock_code,
    }
    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.get(
            f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=headers,
            params=params,
        )
        data = resp.json()

    output = data.get("output", {})
    return {
        "code": stock_code,
        "name": output.get("hts_kor_isnm", ""),
        "currentPrice": float(output.get("stck_prpr", 0)),
        "change": float(output.get("prdy_vrss", 0)),
        "changePct": float(output.get("prdy_ctrt", 0)),
        "volume": int(output.get("acml_vol", 0)),
        "high": float(output.get("stck_hgpr", 0)),
        "low": float(output.get("stck_lwpr", 0)),
        "open": float(output.get("stck_oprc", 0)),
        "per": float(output.get("per", 0)),
        "pbr": float(output.get("pbr", 0)),
        "eps": float(output.get("eps", 0)),
        "bps": float(output.get("bps", 0)),
        "w52High": float(output.get("stck_dryy_hgpr", 0)),
        "w52Low": float(output.get("stck_dryy_lwpr", 0)),
        "marketCap": int(output.get("hts_avls", 0)),
    }


@app.get("/api/kis/exchange-rate")
async def kis_exchange_rate():
    """환율 조회 (USD/KRW)"""
    token = await get_kis_token()
    headers = kis_headers(token, "FHKST01010100")
    # 환율 종목 코드로 조회 (대안: 외환 API)
    # 간단히 하나금융 환율 정보 사용
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get("https://quotation-api-cdn.dunamu.com/v1/forex/recent?codes=FRX.KRWUSD")
            data = resp.json()
            if data:
                rate = data[0].get("basePrice", 1450)
                return {"usdKrw": rate, "source": "dunamu", "timestamp": datetime.now().isoformat()}
        except Exception:
            pass
    return {"usdKrw": 1450, "source": "fallback", "timestamp": datetime.now().isoformat()}


# ─── 업비트 API ───────────────────────────────────────────────

def upbit_auth_header(query_params: dict = None) -> dict:
    """업비트 JWT 인증 헤더 생성"""
    if not UPBIT_ACCESS_KEY or not UPBIT_SECRET_KEY:
        raise HTTPException(status_code=400, detail="업비트 API 키가 설정되지 않았습니다")

    payload = {
        "access_key": UPBIT_ACCESS_KEY,
        "nonce": str(uuid.uuid4()),
        "timestamp": int(time.time() * 1000),
    }

    if query_params:
        query_string = "&".join([f"{k}={v}" for k, v in query_params.items()])
        m = hashlib.sha512()
        m.update(query_string.encode())
        payload["query_hash"] = m.hexdigest()
        payload["query_hash_alg"] = "SHA512"

    jwt_token = jwt.encode(payload, UPBIT_SECRET_KEY, algorithm="HS256")
    return {"Authorization": f"Bearer {jwt_token}"}


@app.get("/api/upbit/accounts")
async def upbit_accounts():
    """업비트 계좌 잔고 조회"""
    if not UPBIT_ACCESS_KEY:
        return {"holdings": [], "summary": {"message": "업비트 API 키 미설정"}}

    headers = upbit_auth_header()
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{UPBIT_BASE_URL}/v1/accounts", headers=headers)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=f"업비트 API 오류: {resp.text}")
        data = resp.json()

    holdings = []
    krw_balance = 0
    for item in data:
        currency = item.get("currency", "")
        balance = float(item.get("balance", 0))
        avg_buy_price = float(item.get("avg_buy_price", 0))

        if currency == "KRW":
            krw_balance = balance
            continue

        if balance <= 0:
            continue

        holdings.append({
            "name": currency,
            "code": f"KRW-{currency}",
            "market": "CRYPTO",
            "quantity": balance,
            "avgPrice": avg_buy_price,
            "currency": "KRW",
        })

    # 현재가 조회
    if holdings:
        codes = ",".join([h["code"] for h in holdings])
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{UPBIT_BASE_URL}/v1/ticker", params={"markets": codes})
            if resp.status_code == 200:
                tickers = resp.json()
                ticker_map = {t["market"]: t for t in tickers}
                for h in holdings:
                    ticker = ticker_map.get(h["code"], {})
                    h["currentPrice"] = float(ticker.get("trade_price", 0))
                    h["evalAmount"] = h["quantity"] * h["currentPrice"]
                    h["evalPnl"] = h["evalAmount"] - (h["quantity"] * h["avgPrice"])
                    h["returnPct"] = (
                        ((h["currentPrice"] - h["avgPrice"]) / h["avgPrice"] * 100)
                        if h["avgPrice"] > 0 else 0
                    )
                    h["changePct"] = float(ticker.get("signed_change_rate", 0)) * 100
                    h["high24h"] = float(ticker.get("high_price", 0))
                    h["low24h"] = float(ticker.get("low_price", 0))

    total_eval = sum(h.get("evalAmount", 0) for h in holdings)
    total_pnl = sum(h.get("evalPnl", 0) for h in holdings)

    return {
        "holdings": holdings,
        "summary": {
            "krwBalance": krw_balance,
            "totalEvalAmount": total_eval,
            "totalPnl": total_pnl,
            "totalAsset": krw_balance + total_eval,
        },
    }


@app.get("/api/upbit/ticker/{market}")
async def upbit_ticker(market: str):
    """업비트 특정 코인 시세 조회"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{UPBIT_BASE_URL}/v1/ticker", params={"markets": market})
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="시세 조회 실패")
        data = resp.json()
        if not data:
            raise HTTPException(status_code=404, detail="종목 없음")
        return data[0]


# ─── 통합 포트폴리오 ──────────────────────────────────────────

@app.get("/api/portfolio")
async def get_full_portfolio():
    """전체 포트폴리오 통합 조회 (국내 + ISA + 해외 + 코인)"""
    results = {"domestic": None, "isa": None, "overseas": None, "crypto": None, "exchangeRate": None, "errors": []}

    # 환율 먼저
    try:
        fx = await kis_exchange_rate()
        results["exchangeRate"] = fx
    except Exception as e:
        results["exchangeRate"] = {"usdKrw": 1450, "source": "fallback"}
        results["errors"].append(f"환율 조회 실패: {str(e)}")

    usd_krw = results["exchangeRate"]["usdKrw"]

    # 국내 주식
    try:
        results["domestic"] = await kis_domestic_balance()
    except Exception as e:
        results["errors"].append(f"국내 주식 조회 실패: {str(e)}")

    # ISA 계좌
    try:
        results["isa"] = await kis_isa_balance()
    except Exception as e:
        results["errors"].append(f"ISA 계좌 조회 실패: {str(e)}")

    # 해외 주식
    try:
        results["overseas"] = await kis_overseas_balance()
        # 각 해외 종목별 실시간 현재가 API로 가격 보정
        if results["overseas"] and results["overseas"].get("holdings"):
            for h in results["overseas"]["holdings"]:
                try:
                    token = await get_kis_token()
                    price_headers = kis_headers(token, "HHDFS76200200")
                    excg_map = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}
                    excg_cd = excg_map.get(h.get("exchange", "NASD"), "NAS")
                    price_params = {
                        "AUTH": "",
                        "EXCD": excg_cd,
                        "SYMB": h["code"],
                    }
                    async with httpx.AsyncClient(verify=False, timeout=5) as client:
                        price_resp = await client.get(
                            f"{KIS_BASE_URL}/uapi/overseas-price/v1/quotations/price",
                            headers=price_headers, params=price_params,
                        )
                        price_data = price_resp.json()
                        real_price = float(price_data.get("output", {}).get("last", 0))
                        if real_price > 0:
                            h["currentPrice"] = real_price
                            h["evalAmount"] = h["quantity"] * real_price
                            h["evalPnl"] = h["evalAmount"] - (h["quantity"] * h["avgPrice"])
                            h["returnPct"] = ((real_price - h["avgPrice"]) / h["avgPrice"] * 100) if h["avgPrice"] > 0 else 0
                except Exception:
                    pass
    except Exception as e:
        results["errors"].append(f"해외 주식 조회 실패: {str(e)}")

    # 코인
    try:
        results["crypto"] = await upbit_accounts()
    except Exception as e:
        results["errors"].append(f"코인 조회 실패: {str(e)}")

    # 통합 자산 계산
    total_asset = 0
    total_invested = 0

    if results["domestic"] and results["domestic"].get("summary"):
        s = results["domestic"]["summary"]
        total_asset += s.get("availableCash", 0)
        for h in results["domestic"].get("holdings", []):
            total_asset += h.get("evalAmount", 0)
            total_invested += h.get("quantity", 0) * h.get("avgPrice", 0)

    if results["isa"] and results["isa"].get("summary"):
        s = results["isa"]["summary"]
        total_asset += s.get("availableCash", 0)
        for h in results["isa"].get("holdings", []):
            total_asset += h.get("evalAmount", 0)
            total_invested += h.get("quantity", 0) * h.get("avgPrice", 0)

    if results["overseas"] and results["overseas"].get("holdings"):
        for h in results["overseas"]["holdings"]:
            eval_krw = h.get("evalAmount", 0) * usd_krw
            cost_krw = h.get("quantity", 0) * h.get("avgPrice", 0) * usd_krw
            total_asset += eval_krw
            total_invested += cost_krw
            h["evalAmountKRW"] = eval_krw

    if results["crypto"] and results["crypto"].get("summary"):
        cs = results["crypto"]["summary"]
        total_asset += cs.get("totalAsset", 0)
        for h in results["crypto"].get("holdings", []):
            total_invested += h.get("quantity", 0) * h.get("avgPrice", 0)

    total_pnl = total_asset - total_invested
    total_return_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0

    results["totalSummary"] = {
        "totalAsset": total_asset,
        "totalInvested": total_invested,
        "totalPnl": total_pnl,
        "totalReturnPct": round(total_return_pct, 2),
        "exchangeRate": usd_krw,
        "timestamp": datetime.now().isoformat(),
    }

    return results


# ─── 설정 업데이트 ─────────────────────────────────────────────

class ApiKeyUpdate(BaseModel):
    kisAppKey: Optional[str] = None
    kisAppSecret: Optional[str] = None
    kisCano: Optional[str] = None
    kisIsaCano: Optional[str] = None
    kisAcntPrdtCd: Optional[str] = None
    upbitAccessKey: Optional[str] = None
    upbitSecretKey: Optional[str] = None


@app.post("/api/settings")
async def update_settings(keys: ApiKeyUpdate):
    """API 키 업데이트 (런타임)"""
    global KIS_APP_KEY, KIS_APP_SECRET, KIS_CANO, KIS_ISA_CANO, KIS_ACNT_PRDT_CD
    global UPBIT_ACCESS_KEY, UPBIT_SECRET_KEY, _kis_token

    if keys.kisAppKey:
        KIS_APP_KEY = keys.kisAppKey
    if keys.kisAppSecret:
        KIS_APP_SECRET = keys.kisAppSecret
    if keys.kisCano:
        KIS_CANO = keys.kisCano
    if keys.kisIsaCano:
        KIS_ISA_CANO = keys.kisIsaCano
    if keys.kisAcntPrdtCd:
        KIS_ACNT_PRDT_CD = keys.kisAcntPrdtCd
    if keys.upbitAccessKey:
        UPBIT_ACCESS_KEY = keys.upbitAccessKey
    if keys.upbitSecretKey:
        UPBIT_SECRET_KEY = keys.upbitSecretKey

    # 토큰 리셋 (새 키로 재발급)
    _kis_token = {"access_token": "", "expires_at": 0}

    return {"status": "ok", "message": "설정이 업데이트되었습니다"}


@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "kisConfigured": bool(KIS_APP_KEY),
        "upbitConfigured": bool(UPBIT_ACCESS_KEY),
        "githubModelsConfigured": bool(GITHUB_TOKEN),
        "timestamp": datetime.now().isoformat(),
    }


# ─── LLM 종목분석/리밸런싱 ─────────────────────────────────────
import json as _json


class AnalyzeRequest(BaseModel):
    holdings: list
    isa: Optional[dict] = None
    cash: Optional[dict] = None
    usdKrw: float = 1450
    totalAsset: float = 0


ANALYZE_SYSTEM_PROMPT = """당신은 한국 개인투자자를 위한 포트폴리오 어드바이저입니다.
제공된 포트폴리오(한국/미국 주식, 암호화폐, ISA ETF, 현금)를 분석하여
1) 보유 종목별 매매 의사결정과 근거
2) 포트폴리오 전체 관점의 리밸런싱 권고
를 JSON으로만 응답하세요. 마크다운 불허. 순수 JSON만.

출력 스키마:
{
  "marketOverview": "현재 시장 전반에 대한 2-3문장 요약 (한국어)",
  "analyses": [
    {
      "code": "종목코드",
      "name": "종목명",
      "action": "적극매수|매수|분할매수|보유유지|비중축소|부분매도|매도권장|손절매도",
      "color": "green|blue|gray|yellow|red",
      "score": 0-100 정수,
      "fundamentalScore": 0-100 정수,
      "technicalScore": 0-100 정수,
      "reasons": ["근거1", "근거2", "근거3"]
    }
  ],
  "rebalancing": {
    "summary": "포트폴리오 전체 평가 2-3문장",
    "targetAllocation": {"국내주식": 30, "미국주식": 40, "암호화폐": 10, "ETF": 15, "현금": 5},
    "actions": [
      {"priority": "high|medium|low", "type": "매수|매도|비중조정|현금확보", "target": "대상 종목/섹터", "description": "구체적 실행 방안"}
    ]
  }
}

반드시 현재 시장 상황(2026년 4월 기준)과 각 종목의 최신 추세를 반영하세요.
매번 새로운 분석을 제공하세요 — 기계적 반복 금지."""


@app.post("/api/analyze")
async def analyze_portfolio(req: AnalyzeRequest):
    """포트폴리오를 LLM으로 분석하여 종목별 매매의견과 리밸런싱 권고 반환"""
    if not GITHUB_TOKEN:
        raise HTTPException(status_code=500, detail="GITHUB_TOKEN 환경변수 미설정")

    # LLM에 전달할 포트폴리오 요약 구성
    portfolio_data = {
        "총자산_원": int(req.totalAsset),
        "환율_USD_KRW": req.usdKrw,
        "현금": req.cash or {},
        "보유종목": [
            {
                "code": h.get("code"),
                "name": h.get("name"),
                "market": h.get("market"),
                "sector": h.get("sector"),
                "qty": h.get("qty"),
                "avgPrice": h.get("avg"),
                "currentPrice": h.get("price"),
                "returnPct": round(((h.get("price", 0) - h.get("avg", 0)) / h.get("avg", 1)) * 100, 2) if h.get("avg") else 0,
                "per": h.get("per"),
                "pbr": h.get("pbr"),
                "roe": h.get("roe"),
                "rsi": h.get("rsi"),
                "macd": h.get("macd"),
                "note": h.get("note"),
            }
            for h in (req.holdings or [])
        ],
        "ISA_ETF": [
            {
                "code": h.get("code"),
                "name": h.get("name"),
                "qty": h.get("qty"),
                "avgPrice": h.get("avg"),
                "currentPrice": h.get("price"),
                "returnPct": h.get("ret"),
                "sector": h.get("sector"),
            }
            for h in ((req.isa or {}).get("holdings") or [])
        ],
    }

    user_content = (
        f"현재 날짜: {datetime.now().strftime('%Y-%m-%d')}\n\n"
        f"포트폴리오 데이터(JSON):\n{_json.dumps(portfolio_data, ensure_ascii=False, indent=2)}\n\n"
        "위 포트폴리오를 분석하여 스키마에 맞는 순수 JSON만 반환하세요."
    )

    payload = {
        "model": GITHUB_MODEL,
        "messages": [
            {"role": "system", "content": ANALYZE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.7,
        "max_tokens": 4000,
        "response_format": {"type": "json_object"},
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                GITHUB_MODELS_URL,
                headers={
                    "Authorization": f"Bearer {GITHUB_TOKEN}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            parsed = _json.loads(content)
            return {
                "status": "ok",
                "model": GITHUB_MODEL,
                "timestamp": datetime.now().isoformat(),
                "result": parsed,
            }
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"LLM API 오류: {e.response.status_code} {e.response.text[:300]}")
    except _json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail=f"LLM 응답 파싱 실패: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"분석 실패: {str(e)}")


# ─── 서버 실행 ─────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("  Stock Advisor API Server")
    print("  http://localhost:8000")
    print("  API Docs: http://localhost:8000/docs")
    print("=" * 50)
    print(f"  한투 API: {'✅ 설정됨' if KIS_APP_KEY else '❌ 미설정'}")
    print(f"  업비트 API: {'✅ 설정됨' if UPBIT_ACCESS_KEY else '❌ 미설정'}")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8001)
