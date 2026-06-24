"""
ETF 검색 API
GET /api/search?q=검색어

종목명(예: KODEX 200) 또는 종목코드(예: 069500)로 한국 상장 ETF를 검색합니다.
KRX 데이터(data.krx.co.kr)가 로그인을 요구하도록 바뀌어 더 이상 사용할 수 없으므로,
각 자산운용사(KODEX/삼성자산운용, TIGER/미래에셋자산운용)가 공개하는 데이터를
직접 수집합니다.
"""

import json
import re
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import requests

HEADERS_COMMON = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}

KODEX_LIST_URL = "https://www.samsungfund.com/api/v1/kodex/product.do"
TIGER_LIST_URL = "https://investments.miraeasset.com/tigeretf/ko/product/search/list.ajax"

# 같은 서버리스 인스턴스 내에서 짧게 재사용하는 캐시 (콜드스타트마다 초기화됨)
_cache = {"date": None, "data": None}


def fetch_kodex_list():
    """KODEX(삼성자산운용) 전체 ETF 목록. 페이지당 20개, 검색 파라미터는 동작하지 않아 전체를 받아온다."""
    items = []
    page = 1
    while page <= 20:  # 안전상 상한
        params = {"srchTerm": "", "ordrSort": "desc", "ordrColm": "", "pageNo": str(page)}
        res = requests.get(KODEX_LIST_URL, params=params, headers=HEADERS_COMMON, timeout=10)
        res.raise_for_status()
        rows = res.json()
        if not rows:
            break
        for row in rows:
            items.append(
                {
                    "source": "KODEX",
                    "id": row.get("fId", ""),
                    "isin": "",
                    "code": row.get("stkTicker", ""),
                    "name": row.get("fNm", ""),
                    "company": "삼성자산운용",
                }
            )
        if len(rows) < 20:
            break
        page += 1
    return items


def fetch_tiger_list():
    """TIGER(미래에셋자산운용) 전체 ETF 목록. listCnt를 크게 주면 한 번에 전체가 온다."""
    payload = {
        "pdfNameYn": "N",
        "pageIndex": "1",
        "listCnt": "300",
        "periodType": "short",
        "listType": "table",
        "etfTemaCode": "",
        "cateNameYn": "N",
        "inCateNationNot": "",
        "inCateFundNot": "",
        "q": "",
        "prfPrd": "1w",
        "orderA": "Month03",
        "orderB": "descending",
    }
    headers = dict(HEADERS_COMMON)
    headers["X-Requested-With"] = "XMLHttpRequest"
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    headers["Referer"] = "https://investments.miraeasset.com/tigeretf/ko/product/search/list.do"

    res = requests.post(TIGER_LIST_URL, data=payload, headers=headers, timeout=10)
    res.raise_for_status()
    html = res.text

    items = []
    for m in re.finditer(
        r'name="cmprPrdctKsdFund"[^>]*value="([^"]+)"[^>]*data-ksd-fund-nm="([^"]+)"',
        html,
    ):
        isin, name = m.group(1), m.group(2)
        items.append(
            {
                "source": "TIGER",
                "id": isin,
                "isin": isin,
                "code": "",
                "name": name,
                "company": "미래에셋자산운용",
            }
        )
    return items


def fetch_all_etfs():
    now = datetime.utcnow()
    if _cache["data"] is not None and now - _cache["date"] < timedelta(minutes=10):
        return _cache["data"]

    data = []
    for fetcher in (fetch_kodex_list, fetch_tiger_list):
        try:
            data.extend(fetcher())
        except Exception:
            # 한 운용사 사이트가 일시적으로 응답하지 않아도 나머지는 보여준다.
            continue

    if data:
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

            etfs = fetch_all_etfs()
            q_lower = q.lower()
            results = []

            for row in etfs:
                name = row["name"]
                code = row["code"]
                if (code and q == code) or q_lower in name.lower():
                    results.append(row)

            results.sort(key=lambda r: (r["code"] != q, r["name"] != q))
            results = results[:30]

            self._send(200, {"count": len(results), "results": results})
        except requests.exceptions.RequestException as e:
            self._send(502, {"error": f"데이터 조회 실패: {e}"})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def _send(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(body, ensure_ascii=False).encode("utf-8"))
