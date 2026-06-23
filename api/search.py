"""
ETF 검색 API
GET /api/search?q=검색어

종목명(예: KODEX 200) 또는 종목코드(예: 069500)로 한국 상장 ETF를 검색합니다.
KRX 정보데이터시스템의 공개 데이터(전종목 기본정보, MDCSTAT04601)를 사용합니다.
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

# 같은 서버리스 인스턴스 내에서 짧게 재사용하는 캐시 (콜드스타트마다 초기화됨)
_cache = {"date": None, "data": None}


def fetch_etf_list():
    """KRX 전종목 기본정보(ETF) 목록을 가져온다. 약 5분간 메모리 캐시."""
    now = datetime.utcnow()
    if _cache["data"] is not None and now - _cache["date"] < timedelta(minutes=5):
        return _cache["data"]

    payload = {"bld": "dbms/MDC/STAT/standard/MDCSTAT04601"}
    res = requests.post(KRX_URL, data=payload, headers=HEADERS, timeout=10)
    res.raise_for_status()
    data = res.json().get("output", [])

    _cache["data"] = data
    _cache["date"] = now
    return data


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            q = (qs.get("q", [""])[0]).strip()

            if not q:
                self._send(400, {"error": "검색어(q)를 입력해주세요."})
                return

            etfs = fetch_etf_list()
            q_lower = q.lower()
            results = []

            for row in etfs:
                name = row.get("ISU_ABBRV", "")
                full_name = row.get("ISU_NM", "")
                code = row.get("ISU_SRT_CD", "")
                isin = row.get("ISU_CD", "")

                if q == code or q_lower in name.lower() or q_lower in full_name.lower():
                    results.append(
                        {
                            "code": code,
                            "isin": isin,
                            "name": name,
                            "fullName": full_name,
                            "company": row.get("COM_ABBRV", ""),
                            "indexName": row.get("ETF_OBJ_IDX_NM", ""),
                            "assetClass": row.get("IDX_ASST_CLSS_NM", ""),
                        }
                    )

            # 정확히 일치하는 코드/이름을 우선 정렬
            results.sort(key=lambda r: (r["code"] != q, r["name"] != q))
            results = results[:30]

            self._send(200, {"count": len(results), "results": results})
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
