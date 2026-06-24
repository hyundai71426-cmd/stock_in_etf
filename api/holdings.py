"""
ETF 구성종목 API
GET /api/holdings?source=KODEX|TIGER&id=<fId 또는 ISIN>

KRX 대신 각 운용사 사이트에서 직접 구성종목(PDF, Portfolio Deposit File)을 가져옵니다.
- KODEX(삼성자산운용): gijunYMD(기준일, YYYYMMDD) 파라미터가 필요하며, 휴장일 대비
  오늘부터 최대 10일 전까지 거슬러 올라가며 데이터가 있는 날짜를 찾습니다.
- TIGER(미래에셋자산운용): 별도 날짜 파라미터 없이 항상 최신 기준일 데이터를 반환합니다.
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

KODEX_PDF_URL = "https://www.samsungfund.com/api/v1/kodex/product-pdf/{fid}.do"
TIGER_PDF_URL = "https://investments.miraeasset.com/tigeretf/ko/product/search/detail/pdfListAjax.ajax"


def _to_float(v):
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def _strip_tags(s):
    return re.sub(r"<[^>]+>", "", s).strip()


def fetch_kodex_holdings(fid, max_back=10):
    d = datetime.now()
    for _ in range(max_back):
        ymd = d.strftime("%Y%m%d")
        res = requests.get(
            KODEX_PDF_URL.format(fid=fid),
            params={"gijunYMD": ymd},
            headers=HEADERS_COMMON,
            timeout=10,
        )
        if res.status_code == 200:
            pdf = res.json().get("pdf", {})
            rows = pdf.get("list", [])
            if rows:
                holdings = []
                for row in rows:
                    holdings.append(
                        {
                            "code": row.get("itmNo", ""),
                            "name": row.get("secNm", ""),
                            "shares": row.get("applyQ", ""),
                            "amount": row.get("evalA", ""),
                            "weight": row.get("ratio") or "0",
                        }
                    )
                return ymd, holdings
        d -= timedelta(days=1)
    return None, []


def fetch_tiger_holdings(isin):
    headers = dict(HEADERS_COMMON)
    headers["X-Requested-With"] = "XMLHttpRequest"
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    headers["Referer"] = (
        "https://investments.miraeasset.com/tigeretf/ko/product/search/detail/index.do"
        f"?ksdFund={isin}"
    )

    res = requests.post(TIGER_PDF_URL, data={"ksdFund": isin}, headers=headers, timeout=10)
    res.raise_for_status()
    html = res.text

    holdings = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.DOTALL)
        if len(cells) < 5:
            continue
        code = _strip_tags(cells[0])
        name = _strip_tags(cells[1])
        shares = _strip_tags(cells[2])
        amount = _strip_tags(cells[3])
        weight = _strip_tags(cells[4])
        if not code and not name:
            continue
        holdings.append(
            {
                "code": code,
                "name": name,
                "shares": shares,
                "amount": amount,
                "weight": weight,
            }
        )
    return datetime.now().strftime("%Y%m%d"), holdings


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            source = (qs.get("source", [""])[0]).strip().upper()
            item_id = (qs.get("id", [""])[0]).strip()

            if not source or not item_id:
                self._send(400, {"error": "source, id 파라미터가 필요합니다."})
                return

            if source == "KODEX":
                used_date, holdings = fetch_kodex_holdings(item_id)
            elif source == "TIGER":
                used_date, holdings = fetch_tiger_holdings(item_id)
            else:
                self._send(400, {"error": "지원하지 않는 운용사입니다."})
                return

            if not holdings:
                self._send(404, {"error": "구성종목 데이터를 찾을 수 없습니다."})
                return

            holdings.sort(key=lambda x: _to_float(x["weight"]), reverse=True)

            self._send(
                200,
                {"date": used_date, "count": len(holdings), "holdings": holdings},
            )
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
