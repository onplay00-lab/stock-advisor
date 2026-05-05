"""Vercel Serverless Function — /api/price
종목 시세 프록시. CORS 우회 + 다중 소스 폴백.

쿼리 파라미터:
  market : KRX | US | CRYPTO
  code   : 종목코드 (예: 000660, ARM, ETH)

응답: { "price": 1447000, "source": "naver", "currency": "KRW" }
"""
import json
import os
import re
import urllib.request
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler


UA = "Mozilla/5.0"
TIMEOUT = 5


def _http_get(url: str, headers: dict | None = None) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def fetch_naver(code: str):
    """네이버 모바일 주식 API → 현재가 (KRW)"""
    try:
        text = _http_get(f"https://m.stock.naver.com/api/stock/{code}/basic")
        data = json.loads(text)
        raw = data.get("closePrice") or data.get("currentPrice")
        if raw:
            price = float(str(raw).replace(",", ""))
            if price > 0:
                return price, "naver"
    except Exception:
        pass
    return None, None


def fetch_yahoo(symbol: str):
    """Yahoo Finance v8 chart API → regularMarketPrice"""
    for path in (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1d",
        f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1d",
    ):
        try:
            text = _http_get(path)
            data = json.loads(text)
            meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
            price = meta.get("regularMarketPrice")
            currency = meta.get("currency", "USD")
            if price and price > 0:
                return float(price), currency, "yahoo"
        except Exception:
            continue
    return None, None, None


def fetch_upbit(market: str):
    """업비트 KRW-XXX 현재가"""
    try:
        text = _http_get(f"https://api.upbit.com/v1/ticker?markets={market}")
        data = json.loads(text)
        if data and data[0].get("trade_price"):
            return float(data[0]["trade_price"]), "upbit"
    except Exception:
        pass
    return None, None


class handler(BaseHTTPRequestHandler):
    def _send(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "public, max-age=30")
        self.end_headers()
        self.wfile.write(json.dumps(body, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self):
        self._send(200, {"ok": True})

    def do_GET(self):
        try:
            qs = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(qs)
            market = (params.get("market", [""])[0] or "").upper()
            code = (params.get("code", [""])[0] or "").strip()

            if not code:
                self._send(400, {"error": "code 파라미터 필수"})
                return

            if market == "KRX":
                price, src = fetch_naver(code)
                if price:
                    self._send(200, {"price": price, "source": src, "currency": "KRW"})
                    return
                # 폴백: Yahoo (예: 000660.KS)
                ysym = f"{code}.KS"
                price, currency, src = fetch_yahoo(ysym)
                if price:
                    self._send(200, {"price": price, "source": src, "currency": currency})
                    return
            elif market == "US":
                price, currency, src = fetch_yahoo(code)
                if price:
                    self._send(200, {"price": price, "source": src, "currency": currency})
                    return
            elif market == "CRYPTO":
                m = code if code.startswith("KRW-") else f"KRW-{code}"
                price, src = fetch_upbit(m)
                if price:
                    self._send(200, {"price": price, "source": src, "currency": "KRW"})
                    return
            else:
                self._send(400, {"error": "market은 KRX|US|CRYPTO 중 하나"})
                return

            self._send(404, {"error": "시세 조회 실패", "market": market, "code": code})
        except Exception as e:
            self._send(500, {"error": str(e)})
