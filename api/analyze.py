"""Vercel Serverless Function — /api/analyze
GitHub Models(gpt-4o-mini)로 포트폴리오 분석 + 리밸런싱 권고를 JSON으로 반환.

환경변수 (Vercel Dashboard → Project → Settings → Environment Variables):
  GITHUB_TOKEN  : GitHub Personal Access Token (models 권한 포함)
"""
import json
import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler
import urllib.request
import urllib.error


GITHUB_MODELS_URL = "https://models.github.ai/inference/chat/completions"
GITHUB_MODEL = "openai/gpt-4o-mini"


SYSTEM_PROMPT = """당신은 한국 개인투자자를 위한 포트폴리오 어드바이저입니다.
제공된 포트폴리오를 분석하여 종목별 매매 의사결정과 리밸런싱 권고를 JSON으로만 응답하세요.
마크다운 불허, 순수 JSON만.

스키마:
{
  "marketOverview": "시장 개요 2-3문장",
  "analyses": [{"code":"...","name":"...","action":"적극매수|매수|분할매수|보유유지|비중축소|부분매도|매도권장|손절매도","color":"green|blue|gray|yellow|red","score":0-100,"fundamentalScore":0-100,"technicalScore":0-100,"reasons":["...","..."]}],
  "rebalancing": {"summary":"...","targetAllocation":{"국내주식":30,"미국주식":40,"암호화폐":10,"ETF":15,"현금":5},"actions":[{"priority":"high|medium|low","type":"매수|매도|비중조정|현금확보","target":"...","description":"..."}]}
}
현재 시장(2026.04)을 반영. 매번 새로운 분석."""


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
            token = os.environ.get("GITHUB_TOKEN", "").strip()
            if not token:
                self._send(500, {"status": "error", "error": "GITHUB_TOKEN 미설정 (Vercel 환경변수)"} )
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
                "스키마에 맞는 순수 JSON만 반환."
            )

            payload = json.dumps({
                "model": GITHUB_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                "temperature": 0.7,
                "max_tokens": 4000,
                "response_format": {"type": "json_object"},
            }).encode("utf-8")

            gh_req = urllib.request.Request(
                GITHUB_MODELS_URL,
                data=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )

            with urllib.request.urlopen(gh_req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                content = data["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                self._send(200, {
                    "status": "ok",
                    "model": GITHUB_MODEL,
                    "timestamp": datetime.now().isoformat(),
                    "result": parsed,
                })
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")[:300]
            self._send(502, {"status": "error", "error": f"HTTP {e.code}: {err_body}"})
        except Exception as e:
            self._send(500, {"status": "error", "error": str(e)})
