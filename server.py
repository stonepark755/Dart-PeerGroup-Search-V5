"""DART 재무실적 조회 에이전트 - 웹 서버 (표준 라이브러리만 사용).

실행:  python server.py        →  http://localhost:8000
API 키: 환경변수 DART_API_KEY 또는 같은 폴더의 .env (DART_API_KEY=...), 혹은 화면에서 입력
"""
import json
import mimetypes
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import base64

from dart import DartClient, DartError, Store
from dart_web import WebClient
from dart import KST, now_kst, today_kst
from engine import (CompanyRun, ITEMS, PERIOD_CODES, NI_FALLBACK_ORDERS, DEFAULT_NI_ORDER,
                    REPRT, build_reports, prev_quarter, period_info, qlabel, month_end, shift_month_end)
from xlsx import build_workbook

ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(ROOT, ".env")
STATIC = os.path.join(ROOT, "static")


def load_key():
    k = os.environ.get("DART_API_KEY", "").strip()
    if k:
        return k
    if os.path.exists(ENV_PATH):
        for line in open(ENV_PATH, encoding="utf-8"):
            if line.strip().startswith("DART_API_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'")
    return ""


KEY = {"v": load_key()}
STORE = Store(os.path.join(ROOT, "data", "dart_agent.sqlite"))
SOURCE = os.environ.get("DATA_SOURCE", "web").lower()      # web: 공시원문 스크래핑(기본) / api: OpenDART API
CLIENT = WebClient(STORE) if SOURCE == "web" else DartClient(lambda: KEY["v"], STORE)
PASSWORD = os.environ.get("APP_PASSWORD", "")               # 설정하면 접속 시 비밀번호(Basic 인증) 요구
POOL = ThreadPoolExecutor(max_workers=8)
MARKET = {"Y": "KOSPI", "K": "KOSDAQ"}


# ------------------------------------------------------------------ 종목 검색
def search_corp(q):
    q = q.strip()
    if not q:
        return []
    ql = q.lower()
    scored = []
    for c in CLIENT.corp_list():
        if q.isdigit():
            if c["stock_code"].startswith(q):
                scored.append((0 if c["stock_code"] == q else 1, c))
        else:
            n = c["corp_name"].lower()
            if n == ql:
                scored.append((0, c))
            elif n.startswith(ql):
                scored.append((1, c))
            elif ql in n:
                scored.append((2, c))
    scored.sort(key=lambda x: (x[0], len(x[1]["corp_name"])))
    cands = [c for _, c in scored[:25]]

    def resolve(c):
        if c.get("market"):
            return c
        try:
            info = CLIENT.company(c["corp_code"])
        except DartError:
            return None
        mk = MARKET.get(info.get("corp_cls"))     # KONEX(N)·기타(E)는 제외
        return dict(c, market=mk) if mk else None

    return [r for r in POOL.map(resolve, cands) if r][:10]


# ------------------------------------------------------------------ 조회 실행
def norm_base_year(v):
    """기준연도(=금년). 미지정·이상값이면 검색일의 연도."""
    this = today_kst().year
    try:
        y = int(v)
    except (TypeError, ValueError):
        return this
    return y if 2000 <= y <= this else this


def run_one(item, searched_at, base_year, ni_order=DEFAULT_NI_ORDER):
    company = {k: item.get(k) for k in ("corp_code", "corp_name", "stock_code", "market")}
    req = item.get("periods") or {}
    periods = {i: (req.get(i) if req.get(i) in PERIOD_CODES else "FY") for i in ITEMS}
    try:
        res = CompanyRun(CLIENT, company, today_kst(), ni_order).run(periods, base_year)
        res["periods"] = periods
        res["ni_order"] = ni_order
        if res.get("has_ttm"):
            # 구간이 여러 개면 분기는 중복 없이, 합계는 구간별로 저장
            seen, qs, ttm_rows = set(), [], []
            for w in res["windows"]:
                for q in w["quarters"] or []:
                    if (q["bsns_year"], q["quarter"]) not in seen:
                        seen.add((q["bsns_year"], q["quarter"]))
                        qs.append(q)
                ttm_rows += [(i, w["ttm"][i]) for i in w["items"]]
            STORE.save_run(company, searched_at, qs, dict(ttm_rows))
        return res
    except DartError as e:
        return {"corp_code": company["corp_code"], "corp_name": company["corp_name"],
                "stock_code": company["stock_code"], "market": company["market"],
                "periods": periods, "items": {}, "error": str(e), "has_ttm": False}
    except Exception as e:   # 예기치 못한 오류도 0원이 아닌 오류 상태로 노출
        return {"corp_code": company["corp_code"], "corp_name": company["corp_name"],
                "stock_code": company["stock_code"], "market": company["market"],
                "periods": periods, "items": {}, "error": f"처리 중 오류: {e!r}", "has_ttm": False}


def run_query(payload):
    searched_at = now_kst().strftime("%Y-%m-%dT%H:%M:%S")   # 한국시간(오프셋 표기 없이 저장·표시)
    items = payload.get("items") or []
    base_year = norm_base_year(payload.get("base_year"))
    ni_order = payload.get("ni_order")
    if ni_order not in NI_FALLBACK_ORDERS:
        ni_order = DEFAULT_NI_ORDER
    results = list(POOL.map(lambda it: run_one(it, searched_at, base_year, ni_order), items))   # 추가한 순서 유지
    return {"searched_at": searched_at, "unit": "원", "base_year": base_year,
            "ni_order": ni_order, "results": results}


# ------------------------------------------------------------------ 진단
def diag(code):
    """한 종목의 OpenDART 원응답(캐시 미사용)을 그대로 보여 준다. 예: /api/diag?code=012450"""
    from datetime import date, datetime
    today = today_kst()
    end = today.strftime("%Y%m%d")
    bgn5 = date(today.year - 5, today.month, min(today.day, 28)).strftime("%Y%m%d")
    out = {"source": SOURCE, "today_kst": today.isoformat(), "code": code}
    corps = [c for c in CLIENT.corp_list() if code in (c.get("stock_code"), c.get("corp_code"))]
    out["corp_matches"] = corps
    if not corps:
        return out
    cc = corps[0]["corp_code"]
    info = CLIENT.company(cc) or {}
    out["company"] = {k: info.get(k) for k in ("corp_name", "stock_code", "acc_mt", "corp_cls")}
    fye = int(info.get("acc_mt") or 12)
    brief = lambda rows: [{k: r.get(k) for k in ("report_nm", "rcept_no", "rcept_dt", "corp_name")} for r in rows]  # noqa: E731
    if SOURCE == "api":
        for name, bgn in (("list_5y", bgn5), ("list_recent", f"{today.year - 1}0101")):
            rows, meta = CLIENT.list_raw(cc, bgn, end)
            out[name] = {"bgn_de": bgn, "end_de": end, **meta, "rows": brief(rows)}
        c = CLIENT.list_cached(cc, bgn5, end)
        out["list_5y_server_cache"] = None if c is None else {
            "fetched_at_kst": datetime.fromtimestamp(c["fetched_at"], KST).isoformat(timespec="seconds"),
            "status": c["status"], "rows": brief(c["rows"])}
        rows5 = out["list_5y"]["rows"]
    else:
        rows5 = brief(CLIENT.list_periodic(cc, bgn5, end))
        out["list_5y"] = {"bgn_de": bgn5, "end_de": end, "rows": rows5}
    reps = build_reports(rows5, fye)
    out["recognized"] = {qlabel(*k): {"report_nm": r.report_nm, "rcept_no": r.rcept_no}
                         for k, r in sorted(reps.items())}
    if SOURCE == "api":
        d = month_end(today.year, today.month)             # 검색일 이전에 끝난 가장 최근 회계분기
        while not (period_info(d, fye)[1] and d < today):
            d = shift_month_end(d, -1)
        k = period_info(d, fye)
        fn = {}
        for _ in range(6):
            fn[qlabel(*k)] = {fs: CLIENT.statement_raw(cc, k[0], REPRT[k[1]], fs) for fs in ("CFS", "OFS")}
            k = prev_quarter(*k)
        out["fnltt"] = fn
    return out


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % a))

    def _json(self, obj, code=200, pretty=False):
        body = json.dumps(obj, ensure_ascii=False, indent=1 if pretty else None).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        if not PASSWORD:
            return True
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                if base64.b64decode(h[6:]).decode().split(":", 1)[1] == PASSWORD:
                    return True
            except Exception:
                pass
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="dart-agent"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self):
        if not self._authorized():
            return
        u = urlparse(self.path)
        try:
            if u.path == "/api/status":
                return self._json({"has_key": SOURCE == "web" or bool(KEY["v"]), "source": SOURCE})
            if u.path == "/api/diag":
                code = (parse_qs(u.query).get("code") or [""])[0].strip()
                return self._json(diag(code), pretty=True) if code else self._json({"error": "code 파라미터 필요"}, 400)
            if u.path == "/api/search":
                q = (parse_qs(u.query).get("q") or [""])[0]
                return self._json({"results": search_corp(q)})
        except DartError as e:
            return self._json({"error": str(e)}, 502)
        rel = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
        fp = os.path.normpath(os.path.join(STATIC, rel))
        if not fp.startswith(STATIC) or not os.path.isfile(fp):
            self.send_error(404)
            return
        data = open(fp, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", (mimetypes.guess_type(fp)[0] or "text/plain") + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if not self._authorized():
            return
        n = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json({"error": "잘못된 요청"}, 400)
        path = urlparse(self.path).path
        if path == "/api/key":
            k = (payload.get("key") or "").strip()
            if len(k) != 40:
                return self._json({"error": "DART API 키는 40자리입니다."}, 400)
            KEY["v"] = k
            with open(ENV_PATH, "w", encoding="utf-8") as f:
                f.write(f"DART_API_KEY={k}\n")
            return self._json({"has_key": True})
        if path == "/api/export":
            data = build_workbook(payload)
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", "attachment; filename=dart_result.xlsx")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/query":
            if SOURCE == "api" and not KEY["v"]:
                return self._json({"error": "DART API 키가 설정되지 않았습니다."}, 400)
            return self._json(run_query(payload))
        self.send_error(404)


def main():
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "127.0.0.1")
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"DART 재무실적 조회 에이전트 [{SOURCE} 모드]: http://{host}:{port}")
    if SOURCE == "api" and not KEY["v"]:
        print("※ DART_API_KEY 가 없습니다. 화면에서 입력하거나 .env 에 설정하세요.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
