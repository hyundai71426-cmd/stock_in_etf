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

# 운용사별로 따로 캐시한다. 한 운용사가 일시적으로 실패해도 다른 운용사 캐시는
# 그대로 유지되고, 실패한 운용사도 "마지막으로 성공했던 데이터"를 계속 보여준다
# (서버리스 인스턴스가 재사용되는 동안에는 유지되고, 콜드스타트마다 초기화됨).
_cache = {
    "KODEX": {"date": None, "data": None},
    "TIGER": {"date": None, "data": None},
    "ACE": {"date": None, "data": None},
}
_CACHE_TTL = timedelta(minutes=30)

# KODEX 목록은 페이지당 20개씩 내려오며 검색 파라미터가 동작하지 않아 전체(현재 약 12페이지)를
# 받아와야 한다. 순차로 받으면 느려서 병렬로 요청한다.
KODEX_MAX_PAGES = 15
KODEX_WORKERS = 8

# TIGER 목록은 한 번에 전부(listCnt=300) 요청하면 응답이 2MB에 달해 해외 리전
# 서버리스 환경에서 전송이 느려 타임아웃나기 쉽다. 페이지를 작게 나눠 병렬로 받는다.
TIGER_PAGE_SIZE = 30
TIGER_MAX_PAGES = 12
TIGER_WORKERS = 8


def _isin_to_code(isin):
    """한국 ISIN(KR7XXXXXX###)에서 6자리 단축코드를 뽑아낸다. 예: KR7292150000 -> 292150."""
    if isin and isin.startswith("KR") and len(isin) >= 9:
        return isin[3:9]
    return ""


def _fetch_kodex_page(session, page):
    params = {"srchTerm": "", "ordrSort": "desc", "ordrColm": "", "pageNo": str(page)}
    res = session.get(KODEX_LIST_URL, params=params, headers=HEADERS_COMMON, timeout=12)
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


def _fetch_tiger_page(session, page):
    payload = {
        "pdfNameYn": "N",
        "pageIndex": str(page),
        "listCnt": str(TIGER_PAGE_SIZE),
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

    res = session.post(TIGER_LIST_URL, data=payload, headers=headers, timeout=12)
    res.raise_for_status()
    html = res.text

    rows = []
    for m in re.finditer(
        r'name="cmprPrdctKsdFund"[^>]*value="([^"]+)"[^>]*data-ksd-fund-nm="([^"]+)"',
        html,
    ):
        rows.append((m.group(1), m.group(2)))
    return rows


def fetch_tiger_list():
    """TIGER(미래에셋자산운용) 전체 ETF 목록. 한 번에 통째로 받으면 응답이 너무 커서
    타임아웃나기 쉬우므로 작은 페이지 단위로 나눠 병렬로 받는다."""
    pages = {}
    with requests.Session() as session, ThreadPoolExecutor(max_workers=TIGER_WORKERS) as pool:
        futures = {
            pool.submit(_fetch_tiger_page, session, p): p
            for p in range(1, TIGER_MAX_PAGES + 1)
        }
        for future in as_completed(futures):
            page = futures[future]
            try:
                pages[page] = future.result()
            except Exception:
                pages[page] = []

    seen = set()
    items = []
    for page in sorted(pages):
        rows = pages[page]
        if not rows:
            continue
        for isin, name in rows:
            if isin in seen:
                continue
            seen.add(isin)
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

    res = requests.get(ACE_LIST_URL, params=params, headers=headers, timeout=12)
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


_FETCHERS = {
    "KODEX": fetch_kodex_list,
    "TIGER": fetch_tiger_list,
    "ACE": fetch_ace_list,
}


def _fetch_source(name):
    """캐시가 신선하면 그대로 쓰고, 아니면 새로 받아온다.
    새로 받아오는 데 실패(혹은 빈 결과)하면 직전에 성공했던 데이터를 그대로 유지해서
    한 운용사의 일시적 오류가 해당 운용사 검색을 완전히 막아버리지 않게 한다."""
    now = datetime.utcnow()
    entry = _cache[name]
    if entry["data"] is not None and entry["date"] is not None and now - entry["date"] < _CACHE_TTL:
        return entry["data"]

    try:
        data = _FETCHERS[name]()
    except Exception:
        data = []

    if data:
        entry["data"] = data
        entry["date"] = now
        return data

    # 실패했거나 빈 결과면 과거 캐시(있다면)를 그대로 반환한다.
    return entry["data"] or []


def fetch_all_etfs():
    data = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_fetch_source, name): name for name in _FETCHERS}
        for future in as_completed(futures):
            try:
                data.extend(future.result())
            except Exception:
                continue
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
