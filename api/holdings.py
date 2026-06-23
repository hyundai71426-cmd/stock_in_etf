"""
ETF 구성종목 API
GET /api/holdings?isin=KR7069500007&date=YYYYMMDD(선택)

KRX 정보데이터시스템의 PDF(Portfolio Deposit File, MDCSTAT05001) 데이터를 사용합니다.
date를 지정하지 않으면 오늘부터 최대 10일 전까지 거슬러 올라가며
데이터가 존재하는 가장 최근 거래일을 찾아 반환합니다(휴장일 대비).
"""

import json
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import requests

KRX_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://data.krx.co.kr/contents/MDC/MDI/outerLoader/index.cmd",
    "X-Requested-With": "XMLHttpRequest",
}


def fetch_pdf(isin, date_str):
    payload = {
        "bld": "dbms/MDC/STAT/standard/MDCSTAT05001",
        "trdDd": date_str,
        "isuCd": isin,
    }
    res = requests.post(KRX_URL, data=payload, headers=HEADERS, timeout=10)
    res.raise_for_status()
    return res.json().get("output", [])


def find_latest_holdings(isin, start_date=None, max_back=10):
    d = start_date or datetime.now()
    for _ in range(max_back):
        date_str = d.strftime("%Y%m%d")
        rows = fetch_pdf(isin, date_str)
        if rows:
            return date_str, rows
        d -= timedelta(days=1)
    return None, []


def _to_float(v):
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            isin = (qs.get("isin", [""])[0]).strip()
            date_param = (qs.get("date", [""])[0]).strip()

            if not isin:
                self._send(400, {"error": "isin 파라미터가 필요합니다."})
                return

            start_date = None
            if date_param:
                try:
                    start_date = datetime.strptime(date_param, "%Y%m%d")
                except ValueError:
                    self._send(400, {"error": "date는 YYYYMMDD 형식이어야 합니다."})
                    return

            used_date, rows = find_latest_holdings(isin, start_date)

            if not rows:
                self._send(404, {"error": "구성종목 데이터를 찾을 수 없습니다. ISIN을 확인해주세요."})
                return

            holdings = []
            for row in rows:
                holdings.append(
                    {
                        "code": row.get("COMPST_ISU_CD", ""),
                        "name": row.get("COMPST_ISU_NM", ""),
                        "shares": row.get("COMPST_ISU_CU1_SHRS", ""),
                        "value": row.get("VALU_AMT", ""),
                        "amount": row.get("COMPST_AMT", ""),
                        "weight": row.get("COMPST_RTO", ""),
                    }
                )

            holdings.sort(key=lambda x: _to_float(x["weight"]), reverse=True)

            self._send(
                200,
                {
                    "date": used_date,
                    "count": len(holdings),
                    "holdings": holdings,
                },
            )
        except requests.exceptions.RequestException as e:
            self._send(502, {"error": f"KRX 데이터 조회 실패: {e}"})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def _send(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(body, ensure_ascii=False).encode("utf-8"))
