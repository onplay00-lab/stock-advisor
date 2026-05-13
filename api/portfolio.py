"""Vercel Serverless Function — /api/portfolio
한국투자증권 OpenAPI로 국내/ISA/해외 잔고 통합 조회.
환경변수: KIS_APP_KEY, KIS_APP_SECRET, KIS_CANO, KIS_ACNT_PRDT_CD, KIS_ISA_CANO

응답 형태는 server.py의 /api/portfolio와 동일하므로 index.html 매핑 그대로 동작.
"""
import json
import os
import ssl
import time
import urllib.request
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler


KIS_BASE_URL = "https://openapi.koreainvestment.com:9443"
TIMEOUT = 8
UA = "Mozilla/5.0"

# 모듈 레벨 토큰 캐시 (warm 인스턴스 재사용 시 유지). KIS는 분당 1회 발급 제한.
_TOKEN = {"access_token": "", "expires_at": 0}

_SSL_CTX = ssl.create_default_context()


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _http(method: str, url: str, headers: dict | None = None, body: bytes | None = None) -> dict:
    req = urllib.request.Request(url, method=method, headers={"User-Agent": UA, **(headers or {})}, data=body)
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def get_token() -> str:
    if _TOKEN["access_token"] and time.time() < _TOKEN["expires_at"]:
        return _TOKEN["access_token"]
    payload = json.dumps({
        "grant_type": "client_credentials",
        "appkey": _env("KIS_APP_KEY"),
        "appsecret": _env("KIS_APP_SECRET"),
    }).encode("utf-8")
    data = _http("POST", f"{KIS_BASE_URL}/oauth2/tokenP",
                 headers={"content-type": "application/json"}, body=payload)
    if "access_token" not in data:
        raise RuntimeError(f"KIS token 발급 실패: {data}")
    _TOKEN["access_token"] = data["access_token"]
    _TOKEN["expires_at"] = time.time() + int(data.get("expires_in", 86400)) - 60
    return data["access_token"]


def kis_headers(tr_id: str) -> dict:
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {get_token()}",
        "appkey": _env("KIS_APP_KEY"),
        "appsecret": _env("KIS_APP_SECRET"),
        "tr_id": tr_id,
    }


def _kis_get(path: str, tr_id: str, params: dict) -> dict:
    qs = urllib.parse.urlencode(params)
    return _http("GET", f"{KIS_BASE_URL}{path}?{qs}", headers=kis_headers(tr_id))


def fetch_domestic_balance(cano: str) -> dict:
    params = {
        "CANO": cano, "ACNT_PRDT_CD": _env("KIS_ACNT_PRDT_CD", "01"),
        "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02", "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "01",
        "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
    }
    data = _kis_get("/uapi/domestic-stock/v1/trading/inquire-balance", "TTTC8434R", params)
    holdings = []
    for item in data.get("output1", []) or []:
        qty = int(float(item.get("hldg_qty", 0) or 0))
        if qty <= 0:
            continue
        holdings.append({
            "name": item.get("prdt_name", ""),
            "code": item.get("pdno", ""),
            "market": "KRX",
            "quantity": qty,
            "avgPrice": float(item.get("pchs_avg_pric", 0) or 0),
            "currentPrice": float(item.get("prpr", 0) or 0),
            "evalAmount": float(item.get("evlu_amt", 0) or 0),
            "evalPnl": float(item.get("evlu_pfls_amt", 0) or 0),
            "returnPct": float(item.get("evlu_pfls_rt", 0) or 0),
        })
    summary = data.get("output2") or [{}]
    if isinstance(summary, list):
        summary = summary[0] if summary else {}
    return {
        "holdings": holdings,
        "summary": {
            "totalEvalAmount": float(summary.get("tot_evlu_amt", 0) or 0),
            "totalPurchaseAmount": float(summary.get("pchs_amt_smtl_amt", 0) or 0),
            "totalPnl": float(summary.get("evlu_pfls_smtl_amt", 0) or 0),
            "availableCash": float(summary.get("dnca_tot_amt", 0) or 0),
            "totalAsset": float(summary.get("scts_evlu_amt", 0) or 0) + float(summary.get("dnca_tot_amt", 0) or 0),
        },
    }


def fetch_overseas_balance(cano: str) -> dict:
    all_h = []
    seen_codes = set()
    usd_cash = 0.0
    for exchange in ("NASD", "NYSE", "AMEX"):
        params = {
            "CANO": cano, "ACNT_PRDT_CD": _env("KIS_ACNT_PRDT_CD", "01"),
            "OVRS_EXCG_CD": exchange, "TR_CRCY_CD": "USD",
            "CTX_AREA_FK200": "", "CTX_AREA_NK200": "",
        }
        try:
            data = _kis_get("/uapi/overseas-stock/v1/trading/inquire-balance", "TTTS3012R", params)
        except Exception:
            continue
        # USD 외화예수금 (output2). 거래소별로 동일 값 반환되므로 최대값 사용.
        out2 = data.get("output2") or {}
        if isinstance(out2, list):
            out2 = out2[0] if out2 else {}
        for f in ("frcr_use_psbl_amt", "frcr_dncl_amt"):
            v = float(out2.get(f, 0) or 0)
            if v > usd_cash:
                usd_cash = v
        for item in data.get("output1", []) or []:
            qty = float(item.get("ovrs_cblc_qty", 0) or 0)
            if qty <= 0:
                continue
            code = item.get("ovrs_pdno", "")
            if code in seen_codes:
                continue
            seen_codes.add(code)
            avg = float(item.get("pchs_avg_pric", 0) or 0)
            cur = float(item.get("now_pric2", 0) or item.get("ovrs_now_pric", 0) or 0)
            eval_amt = float(item.get("ovrs_stck_evlu_amt", 0) or 0)
            all_h.append({
                "name": item.get("ovrs_item_name", ""),
                "code": code,
                "market": "US",
                "exchange": exchange,
                "quantity": qty,
                "avgPrice": avg,
                "currentPrice": cur,
                "evalAmount": eval_amt,
                "evalPnl": float(item.get("frcr_evlu_pfls_amt", 0) or 0),
                "returnPct": float(item.get("evlu_pfls_rt", 0) or 0),
                "currency": "USD",
            })
    return {"holdings": all_h, "summary": {"usdCash": usd_cash}}


def boost_overseas_prices(holdings: list):
    """해외 실시간 현재가 보정 (NAS/NYS/AMS)."""
    excg_map = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}
    for h in holdings:
        try:
            params = {"AUTH": "", "EXCD": excg_map.get(h.get("exchange", "NASD"), "NAS"), "SYMB": h["code"]}
            data = _kis_get("/uapi/overseas-price/v1/quotations/price", "HHDFS00000300", params)
            real = float((data.get("output") or {}).get("last", 0) or 0)
            if real > 0:
                h["currentPrice"] = real
                h["evalAmount"] = h["quantity"] * real
                h["evalPnl"] = h["evalAmount"] - (h["quantity"] * h["avgPrice"])
                h["returnPct"] = ((real - h["avgPrice"]) / h["avgPrice"] * 100) if h["avgPrice"] > 0 else 0
        except Exception:
            pass


def fetch_fx() -> dict:
    try:
        data = _http("GET", "https://quotation-api-cdn.dunamu.com/v1/forex/recent?codes=FRX.KRWUSD")
        if data:
            rate = float(data[0].get("basePrice", 0) or 0)
            if rate > 0:
                return {"usdKrw": rate, "source": "dunamu"}
    except Exception:
        pass
    try:
        data = _http("GET", "https://query1.finance.yahoo.com/v8/finance/chart/KRW=X?range=1d&interval=1d")
        rate = float((data.get("chart", {}).get("result", [{}])[0].get("meta") or {}).get("regularMarketPrice", 0) or 0)
        if rate > 0:
            return {"usdKrw": rate, "source": "yahoo"}
    except Exception:
        pass
    return {"usdKrw": 1450, "source": "fallback"}


def build_portfolio() -> dict:
    results = {"domestic": None, "isa": None, "overseas": None, "crypto": None, "exchangeRate": None, "errors": []}

    results["exchangeRate"] = fetch_fx()
    usd_krw = results["exchangeRate"]["usdKrw"]

    cano = _env("KIS_CANO")
    isa_cano = _env("KIS_ISA_CANO")

    if cano:
        try:
            results["domestic"] = fetch_domestic_balance(cano)
        except Exception as e:
            results["errors"].append(f"국내 조회 실패: {e}")
        try:
            ov = fetch_overseas_balance(cano)
            if ov["holdings"]:
                boost_overseas_prices(ov["holdings"])
            results["overseas"] = ov
        except Exception as e:
            results["errors"].append(f"해외 조회 실패: {e}")
    else:
        results["errors"].append("KIS_CANO 미설정")

    if isa_cano:
        try:
            results["isa"] = fetch_domestic_balance(isa_cano)
        except Exception as e:
            results["errors"].append(f"ISA 조회 실패: {e}")
    else:
        results["errors"].append("KIS_ISA_CANO 미설정")

    # 통합 자산
    total_asset = 0.0
    total_invested = 0.0
    if results["domestic"]:
        s = results["domestic"]["summary"]
        total_asset += s.get("availableCash", 0)
        for h in results["domestic"]["holdings"]:
            total_asset += h.get("evalAmount", 0)
            total_invested += h.get("quantity", 0) * h.get("avgPrice", 0)
    if results["isa"]:
        s = results["isa"]["summary"]
        total_asset += s.get("availableCash", 0)
        for h in results["isa"]["holdings"]:
            total_asset += h.get("evalAmount", 0)
            total_invested += h.get("quantity", 0) * h.get("avgPrice", 0)
    if results["overseas"]:
        for h in results["overseas"]["holdings"]:
            eval_krw = h.get("evalAmount", 0) * usd_krw
            cost_krw = h.get("quantity", 0) * h.get("avgPrice", 0) * usd_krw
            total_asset += eval_krw
            total_invested += cost_krw
            h["evalAmountKRW"] = eval_krw

    total_pnl = total_asset - total_invested
    ret_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0
    results["totalSummary"] = {
        "totalAsset": total_asset,
        "totalInvested": total_invested,
        "totalPnl": total_pnl,
        "totalReturnPct": round(ret_pct, 2),
        "exchangeRate": usd_krw,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    return results


class handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "private, max-age=20")
        self.end_headers()
        self.wfile.write(json.dumps(body, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self):
        self._send(200, {"ok": True})

    def do_GET(self):
        if not _env("KIS_APP_KEY") or not _env("KIS_APP_SECRET"):
            self._send(400, {"error": "KIS_APP_KEY/KIS_APP_SECRET 환경변수 미설정"})
            return
        try:
            self._send(200, build_portfolio())
        except Exception as e:
            self._send(500, {"error": str(e)})
