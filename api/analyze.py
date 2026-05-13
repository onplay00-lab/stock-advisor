"""Vercel Serverless Function — /api/analyze
Anthropic Claude Opus 4.7로 포트폴리오 분석 + 리밸런싱 권고를 JSON으로 반환.

환경변수 (Vercel Dashboard → Project → Settings → Environment Variables):
  ANTHROPIC_API_KEY  : Anthropic API 키 (sk-ant-... 형식)
"""
import json
import os
import socket
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler
import urllib.request
import urllib.error


ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MODEL = "claude-opus-4-7"
REQUEST_TIMEOUT = 90  # Opus는 응답이 더 길 수 있음
MAX_RETRIES = 2


SYSTEM_PROMPT = """당신은 한국 개인투자자를 위한 포트폴리오 어드바이저입니다.
제공된 포트폴리오를 분석하여 종목별 매매 의사결정과 리밸런싱 권고를 JSON으로만 응답하세요.
마크다운, 코드펜스, 설명 모두 불허. 순수 JSON 객체 하나만.

스키마:
{
  "marketOverview": "시장 개요 2-3문장",
  "analyses": [{"code":"...","name":"...","action":"적극매수|매수|분할매수|보유유지|비중축소|부분매도|매도권장|손절매도","color":"green|blue|gray|yellow|red","score":0-100,"fundamentalScore":0-100,"technicalScore":0-100,"reasons":["...","..."]}],
  "rebalancing": {"summary":"...","targetAllocation":{"국내주식":30,"미국주식":40,"암호화폐":10,"ETF":15,"현금":5},"actions":[{"priority":"high|medium|low","type":"매수|매도|비중조정|현금확보","target":"...","description":"..."}]}
}
현재 시장(2026년)을 반영. 매번 새로운 분석."""


def _extract_json(text: str) -> dict:
    """Claude 응답에서 JSON 객체 추출. 코드펜스/잡음 제거."""
    text = text.strip()
    # ```json ... ``` 또는 ``` ... ``` 제거
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    # 첫 { 부터 마지막 } 까지
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


class handler(BaseHTTPRequestHandler):
    def _send(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(body, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self):
        self._send(200, {"ok": True})

    def do_POST(self):
        try:
            api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
            if not api_key:
                self._send(500, {
                    "status": "error",
                    "error": "ANTHROPIC_API_KEY 미설정",
                    "hint": "Vercel 환경변수에 ANTHROPIC_API_KEY 추가 (https://console.anthropic.com/settings/keys)",
                })
                return

            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            req = json.loads(raw.decode("utf-8") or "{}")

            portfolio_data = {
                "총자산_원": int(req.get("totalAsset", 0) or 0),
                "환율_USD_KRW": req.get("usdKrw", 1450),
                "현금": req.get("cash") or {},
                "보유종목": [
                    {
                        "code": h.get("code"), "name": h.get("name"), "market": h.get("market"),
                        "sector": h.get("sector"), "qty": h.get("qty"),
                        "avgPrice": h.get("avg"), "currentPrice": h.get("price"),
                        "returnPct": round(((h.get("price", 0) - h.get("avg", 0)) / h.get("avg", 1)) * 100, 2) if h.get("avg") else 0,
                        "per": h.get("per"), "pbr": h.get("pbr"), "roe": h.get("roe"),
                        "rsi": h.get("rsi"), "macd": h.get("macd"), "note": h.get("note"),
                    } for h in (req.get("holdings") or [])
                ],
                "ISA_ETF": [
                    {"code": h.get("code"), "name": h.get("name"), "qty": h.get("qty"),
                     "avgPrice": h.get("avg"), "currentPrice": h.get("price"),
                     "returnPct": h.get("ret"), "sector": h.get("sector")}
                    for h in ((req.get("isa") or {}).get("holdings") or [])
                ],
            }

            user_content = (
                f"현재 날짜: {datetime.now().strftime('%Y-%m-%d')}\n\n"
                f"포트폴리오:\n{json.dumps(portfolio_data, ensure_ascii=False, indent=2)}\n\n"
                "스키마에 맞는 순수 JSON 객체 하나만 반환."
            )

            payload = json.dumps({
                "model": MODEL,
                "max_tokens": 4096,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_content}],
            }).encode("utf-8")

            result, err = self._call_with_retry(payload, api_key)
            if err:
                self._send(err["status"], {"status": "error", "error": err["msg"], "hint": err.get("hint")})
                return

            try:
                blocks = result.get("content", [])
                text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                parsed = _extract_json(text)
            except (KeyError, json.JSONDecodeError, IndexError, AttributeError) as e:
                self._send(502, {"status": "error", "error": f"AI 응답 파싱 실패: {e}", "hint": "다시 시도해주세요."})
                return

            self._send(200, {
                "status": "ok",
                "model": MODEL,
                "timestamp": datetime.now().isoformat(),
                "result": parsed,
            })
        except json.JSONDecodeError as e:
            self._send(400, {"status": "error", "error": f"잘못된 요청 형식: {e}"})
        except Exception as e:
            self._send(500, {"status": "error", "error": f"내부 오류: {e}"})

    def _call_with_retry(self, payload: bytes, api_key: str):
        """Anthropic API 호출. 4xx는 즉시 실패, 5xx/네트워크/429는 재시도."""
        last_err = None
        for attempt in range(MAX_RETRIES + 1):
            req = urllib.request.Request(
                ANTHROPIC_URL, data=payload, method="POST",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                    return json.loads(resp.read().decode("utf-8")), None
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="ignore")[:400]
                # 디버그 정보 (키 자체는 노출 X, 길이/접두사만)
                key_len = len(api_key)
                key_prefix = api_key[:12] if api_key else "(empty)"
                if e.code == 401:
                    return None, {
                        "status": 401,
                        "msg": f"Anthropic 401: body={body!r} keylen={key_len} prefix={key_prefix}",
                        "hint": "Vercel 환경변수 ANTHROPIC_API_KEY를 확인하세요.",
                    }
                if e.code == 400:
                    return None, {"status": 400, "msg": f"요청 형식 오류 (400): {body}"}
                if e.code == 403:
                    return None, {"status": 403, "msg": f"권한 거부 (403): {body}"}
                if e.code == 404:
                    return None, {"status": 404, "msg": f"모델 미존재 ({MODEL}): {body}", "hint": "model 식별자를 확인하세요."}
                if e.code == 413:
                    return None, {"status": 413, "msg": "요청이 너무 큼 (413)", "hint": "포트폴리오 데이터 축소 필요"}
                if e.code == 429:
                    last_err = {"status": 429, "msg": "Rate limit (429)", "hint": "잠시 후 재시도"}
                    if attempt < MAX_RETRIES:
                        time.sleep(2 * (attempt + 1))
                        continue
                if 500 <= e.code < 600:
                    last_err = {"status": 502, "msg": f"Anthropic 일시 오류 ({e.code})", "hint": "잠시 후 재시도"}
                    if attempt < MAX_RETRIES:
                        time.sleep(1.5 * (attempt + 1))
                        continue
                return None, {"status": 502, "msg": f"HTTP {e.code}: {body}"}
            except (socket.timeout, TimeoutError):
                last_err = {"status": 504, "msg": "AI 응답 타임아웃", "hint": "다시 시도해주세요."}
                if attempt < MAX_RETRIES:
                    continue
            except urllib.error.URLError as e:
                last_err = {"status": 502, "msg": f"네트워크 오류: {e.reason}"}
                if attempt < MAX_RETRIES:
                    time.sleep(1)
                    continue
        return None, last_err or {"status": 500, "msg": "원인 미상"}
