"""
ETF 검색 API
GET /api/search?q=검색어

종목명(예: KODEX 200) 또는 종목코드(예: 069500)로 한국 상장 ETF를 검색합니다.
KRX 데이터(data.krx.co.kr)가 로그인을 요구하도록 바뀌어 더 이상 사용할 수 없으므로,
각 자산운용사(KODEX/삼성자산운용, TIGER/미래에셋자산운용, ACE/한국투자신탁운용)가
공개하는 데이터를 직접 수집합니다.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
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
ACE_LIST_URL = "https://papi.aceetf.co.kr/api/funds"

# 같은 서버리스 인스턴스 내에서 재사용하는 캐시 (콜드스타트마다 초기화됨)
_cache = {"date": None, "data": None}
_CACHE_TTL = timedelta(minutes=30)

# KODEX 목록은 페이지당 20개씩 내려오며 검색 파라미터가 동작하지 않아 전체(현재 약 12페이지)를
# 받아와야 한다. 순차로 받으면 느려서 병렬로 요청한다.
KODEX_MAX_PAGES = 15
KODEX_WORKERS = 8


def _isin_to_code(isin):
    """한국 ISIN(KR7XXXXXX###)에서 6자리 단축코드를 뽑아낸다. 예: KR7292150000 -> 292150."""
    if isin and isin.startswith("KR") and len(isin) >= 9:
        return isin[3:9]
    return ""


def _fetch_kodex_page(session, page):
    params = {"srchTerm": "", "ordrSort": "desc", "ordrColm": "", "pageNo": str(page)}
    res = session.get(KODEX_LIST_URL, params=params, headers=HEADERS_COMMON, timeout=10)
    res.raise_for_status()
    return res.json()


def fetch_kodex_list():
    """KODEX(삼성자산운용) 전체 ETF 목록을 병렬로 받아온다."""
    pages = {}
    with requests.Session() as session, ThreadPoolExecutor(max_workers=KODEX_WORKERS) as pool:
        futures = {
            pool.submit(_fetch_kodex_page, session, p): p
            for p in range(1, KODEX_MAX_PAGES + 1)
        }
        for future in as_completed(futures):
            page = futures[future]
            try:
                pages[page] = future.result()
            except Exception:
                pages[page] = []

    items = []
    for page in sorted(pages):
        rows = pages[page]
        if not rows:
            continue
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
                "code": _isin_to_code(isin),
                "name": name,
                "company": "미래에셋자산운용",
            }
        )
    return items


def fetch_ace_list():
    """ACE(한국투자신탁운용) 전체 ETF 목록. size를 크게 주면 한 번에 전체가 온다."""
    params = {
        "isAceEtfPlus": "false",
        "page": "1",
        "pensionType": "",
        "searchValue": "",
        "size": "500",
        "sort": "MM1_ERN_RT_DESC",
    }
    headers = dict(HEADERS_COMMON)
    headers["Accept"] = "application/json"
    headers["Referer"] = "https://www.aceetf.co.kr/"

    res = requests.get(ACE_LIST_URL, params=params, headers=headers, timeout=10)
    res.raise_for_status()
    rows = res.json().get("data", [])

    items = []
    for row in rows:
        isin = row.get("stockCd", "")
        items.append(
            {
                "source": "ACE",
                "id": row.get("fundCd", ""),
                "isin": isin,
                "code": _isin_to_code(isin),
                "name": row.get("fundNm", ""),
                "company": "한국투자신탁운용",
            }
        )
    return items


def fetch_all_etfs():
    now = datetime.utcnow()
    if _cache["data"] is not None and now - _cache["date"] < _CACHE_TTL:
        return _cache["data"]

    data = []
    # 세 운용사 목록은 서로 독립적이므로 동시에 받아온다.
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(fetcher): fetcher.__name__
            for fetcher in (fetch_kodex_list, fetch_tiger_list, fetch_ace_list)
        }
        for future in as_completed(futures):
            try:
                data.extend(future.result())
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
