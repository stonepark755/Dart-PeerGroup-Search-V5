"""OpenDART 클라이언트 + SQLite 저장소.

저장 계층 (업무지시서 11.4)
  raw_cache      : DART API 원본 응답 (Raw Data)
  quarterly_data : 분기별 추출/계산 결과 (Quarterly Data)  - 조회 때마다 append, 덮어쓰지 않음
  ttm_data       : 검증된 TTM 결과 (TTM Data)
  basis_history  : 연결·개별 구분 이력 (Basis Change History) - 최초 확인값을 보존(INSERT OR IGNORE)

외부 라이브러리 없이 표준 라이브러리만 사용한다.
"""
import io
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone

# 한국시간(KST, UTC+9). 서버가 해외 리전(UTC)에서 돌아도 검색일·검색시점은 한국 기준.
# slim 이미지엔 tzdata 가 없을 수 있어 ZoneInfo 대신 고정 오프셋 사용(한국은 서머타임 없음).
KST = timezone(timedelta(hours=9), "KST")


def now_kst():
    return datetime.now(KST)


def today_kst():
    return now_kst().date()

BASE = "https://opendart.fss.or.kr/api/"
REPRT_KO = {"11013": "1분기보고서", "11012": "반기보고서", "11014": "3분기보고서", "11011": "사업보고서"}
DAY = 86400


class DartError(Exception):
    """DART API 오류 (키 오류, 한도 초과, 네트워크 오류 등)."""


class DocParseError(DartError):
    """공시원문에서 재무제표를 읽지 못함 (해당 보고서만 '검증 필요'로 처리, 전체 조회는 계속)."""


# --------------------------------------------------------------------------- #
# 저장소
# --------------------------------------------------------------------------- #
class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS raw_cache(
                    k TEXT PRIMARY KEY, fetched_at REAL, body BLOB);
                CREATE TABLE IF NOT EXISTS quarterly_data(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, searched_at TEXT,
                    corp_code TEXT, corp_name TEXT, stock_code TEXT,
                    bsns_year INTEGER, quarter INTEGER,
                    period_start TEXT, period_end TEXT, fs_div TEXT,
                    item TEXT, value INTEGER, account_id TEXT, account_nm TEXT,
                    rcept_no TEXT, rcept_dt TEXT, method TEXT, formula TEXT,
                    status TEXT, payload TEXT);
                CREATE TABLE IF NOT EXISTS ttm_data(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, searched_at TEXT,
                    corp_code TEXT, item TEXT, value INTEGER, status TEXT,
                    basis TEXT, period_label TEXT);
                CREATE TABLE IF NOT EXISTS basis_history(
                    corp_code TEXT, bsns_year INTEGER, quarter INTEGER,
                    rcept_no TEXT, fs_div TEXT, evidence TEXT, first_seen_at TEXT,
                    PRIMARY KEY(corp_code, bsns_year, quarter, rcept_no));
                """
            )
            self.db.commit()

    # raw cache -------------------------------------------------------------
    def cache_get(self, key, ttl):
        with self.lock:
            row = self.db.execute(
                "SELECT fetched_at, body FROM raw_cache WHERE k=?", (key,)
            ).fetchone()
        if not row:
            return None
        if ttl is not None and time.time() - row[0] > ttl:
            return None
        return row[1]

    def cache_put(self, key, body):
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO raw_cache VALUES(?,?,?)",
                (key, time.time(), body),
            )
            self.db.commit()

    # 분기/TTM/이력 -----------------------------------------------------------
    def save_run(self, company, searched_at, quarters, ttm):
        with self.lock:
            for q in quarters or []:
                rep = q.get("report") or {}
                if q.get("fs_div") and rep.get("rcept_no"):
                    self.db.execute(
                        "INSERT OR IGNORE INTO basis_history VALUES(?,?,?,?,?,?,?)",
                        (company["corp_code"], q["bsns_year"], q["quarter"],
                         rep["rcept_no"], q["fs_div"], q.get("basis_evidence"), searched_at),
                    )
                for item, r in (q.get("items") or {}).items():
                    src = (r.get("sources") or [{}])[0]
                    self.db.execute(
                        "INSERT INTO quarterly_data(searched_at,corp_code,corp_name,stock_code,"
                        "bsns_year,quarter,period_start,period_end,fs_div,item,value,account_id,"
                        "account_nm,rcept_no,rcept_dt,method,formula,status,payload) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (searched_at, company["corp_code"], company.get("corp_name"),
                         company.get("stock_code"), q["bsns_year"], q["quarter"],
                         q["period_start"], q["period_end"], q.get("fs_div"), item,
                         r.get("value"), src.get("account_id"), src.get("account_nm"),
                         src.get("rcept_no"), src.get("rcept_dt"), r.get("method"),
                         r.get("formula"), r.get("status"), json.dumps(r, ensure_ascii=False)),
                    )
            for item, t in (ttm or {}).items():
                self.db.execute(
                    "INSERT INTO ttm_data(searched_at,corp_code,item,value,status,basis,period_label)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (searched_at, company["corp_code"], item, t.get("value"),
                     t.get("status"), t.get("basis"), t.get("period_label")),
                )
            self.db.commit()


# --------------------------------------------------------------------------- #
# 클라이언트
# --------------------------------------------------------------------------- #
class DartClient:
    def __init__(self, key_getter, store):
        self.key_getter = key_getter
        self.store = store
        self.sem = threading.Semaphore(6)
        self._corps = None
        self._corps_at = 0
        self._corps_lock = threading.Lock()
        self._doc_raw, self._doc_lock = {}, threading.Lock()   # 공시원문 zip (최근 몇 건만 메모리 보관)

    # low level ---------------------------------------------------------------
    def _http(self, endpoint, params):
        key = self.key_getter()
        if not key:
            raise DartError("DART API 키가 설정되지 않았습니다.")
        q = dict(params)
        q["crtfc_key"] = key
        url = BASE + endpoint + "?" + urllib.parse.urlencode(q)
        last = None
        for attempt in range(3):
            try:
                with self.sem:
                    with urllib.request.urlopen(url, timeout=60) as r:
                        return r.read()
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last = e
                time.sleep(1.2 * (attempt + 1))
        raise DartError(f"DART 서버 통신 실패: {last}")

    def _json(self, endpoint, params, ttl, empty_ttl=None):
        """empty_ttl: '조회된 데이터 없음(013)' 응답의 재사용 시간(초). 지정하면 ttl 대신 이 값을 적용."""
        ck = endpoint + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        raw = self.store.cache_get(ck, ttl) if self.store else None
        if raw is not None and empty_ttl is not None and len(raw) < 400 \
                and json.loads(raw.decode("utf-8")).get("status") == "013":
            raw = self.store.cache_get(ck, empty_ttl)      # 오래된 '데이터 없음'은 다시 조회
        if raw is None:
            raw = self._http(endpoint, params)
            try:
                data = json.loads(raw.decode("utf-8"))
            except ValueError:
                raise DartError("DART 응답을 해석할 수 없습니다.")
            if data.get("status") in ("000", "013") and self.store:
                self.store.cache_put(ck, raw)
        else:
            data = json.loads(raw.decode("utf-8"))
        st = data.get("status")
        if st not in ("000", "013"):
            raise DartError(f"DART 오류 {st}: {data.get('message')}")
        return data

    # 기업 목록 ---------------------------------------------------------------
    def corp_list(self):
        """상장(종목코드 보유) 기업 목록: corpCode.xml"""
        with self._corps_lock:
            if self._corps is not None and time.time() - self._corps_at < DAY:
                return self._corps
            ck = "corpCode.xml"
            raw = self.store.cache_get(ck, 7 * DAY)
            if raw is None:
                raw = self._http("corpCode.xml", {})
                if not raw.startswith(b"PK"):
                    raise DartError("corpCode.xml 수신 실패: API 키를 확인하세요.")
                self.store.cache_put(ck, raw)
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                xml = z.read(z.namelist()[0])
            out = []
            for el in ET.fromstring(xml).iter("list"):
                sc = (el.findtext("stock_code") or "").strip()
                if sc:
                    out.append({
                        "corp_code": el.findtext("corp_code").strip(),
                        "corp_name": (el.findtext("corp_name") or "").strip(),
                        "stock_code": sc,
                    })
            self._corps, self._corps_at = out, time.time()
            return out

    def company(self, corp_code):
        d = self._json("company.json", {"corp_code": corp_code}, 7 * DAY)
        return d if d.get("status") == "000" else {}

    # 공시 목록 ---------------------------------------------------------------
    def _list(self, corp_code, bgn, end, pblntf_ty, ttl):
        rows, page = [], 1
        while True:
            d = self._json("list.json", {
                "corp_code": corp_code, "bgn_de": bgn, "end_de": end,
                "pblntf_ty": pblntf_ty, "last_reprt_at": "N",
                "page_no": page, "page_count": 100}, ttl)
            rows += d.get("list") or []
            if page >= int(d.get("total_page") or 1):
                return rows
            page += 1

    def list_periodic(self, corp_code, bgn, end):
        return self._list(corp_code, bgn, end, "A", 3600)

    def list_major(self, corp_code, bgn, end):
        return self._list(corp_code, bgn, end, "B", 3600)

    # 재무제표 ----------------------------------------------------------------
    def api_statement(self, corp_code, bsns_year, reprt_code, fs_div):
        """OpenDART 재무정보(단일회사 전체 재무제표). 없으면 None.
        공시 직후엔 아직 반영 전일 수 있어 '없음' 응답은 1시간만 재사용."""
        d = self._json("fnlttSinglAcntAll.json", {
            "corp_code": corp_code, "bsns_year": bsns_year,
            "reprt_code": reprt_code, "fs_div": fs_div}, DAY, empty_ttl=3600)
        return (d.get("list") or None) if d.get("status") == "000" else None

    def probe_report(self, corp_code, bsns_year, reprt_code):
        """공시목록(list.json)에 없는 분기를 재무정보 API로 직접 확인 → 접수번호(없으면 None).
        재무정보 API 응답의 각 행에는 해당 정기보고서의 rcept_no 가 들어 있다."""
        for fs in ("CFS", "OFS"):
            rows = self.api_statement(corp_code, bsns_year, reprt_code, fs)
            rn = next((r.get("rcept_no") for r in rows or [] if r.get("rcept_no")), None)
            if rn:
                return rn
        return None

    # 진단(캐시 미사용) -------------------------------------------------------
    def list_raw(self, corp_code, bgn, end, pblntf_ty="A"):
        rows, page = [], 1
        while True:
            d = json.loads(self._http("list.json", {
                "corp_code": corp_code, "bgn_de": bgn, "end_de": end, "pblntf_ty": pblntf_ty,
                "last_reprt_at": "N", "page_no": page, "page_count": 100}).decode("utf-8"))
            meta = {k: d.get(k) for k in ("status", "message", "total_count", "total_page")}
            rows += d.get("list") or []
            if d.get("status") != "000" or page >= int(d.get("total_page") or 1):
                return rows, meta
            page += 1

    def list_cached(self, corp_code, bgn, end, pblntf_ty="A"):
        """서버 캐시에 저장된 공시목록 1페이지(없으면 None) — 캐시가 낡았는지 확인용."""
        params = {"corp_code": corp_code, "bgn_de": bgn, "end_de": end, "pblntf_ty": pblntf_ty,
                  "last_reprt_at": "N", "page_no": 1, "page_count": 100}
        ck = "list.json?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        with self.store.lock:
            row = self.store.db.execute("SELECT fetched_at, body FROM raw_cache WHERE k=?", (ck,)).fetchone()
        if not row:
            return None
        d = json.loads(row[1])
        return {"fetched_at": row[0], "rows": d.get("list") or [], "status": d.get("status")}

    def statement_raw(self, corp_code, bsns_year, reprt_code, fs_div):
        d = json.loads(self._http("fnlttSinglAcntAll.json", {
            "corp_code": corp_code, "bsns_year": bsns_year,
            "reprt_code": reprt_code, "fs_div": fs_div}).decode("utf-8"))
        lst = d.get("list") or []
        return {"status": d.get("status"), "message": d.get("message"), "rows": len(lst),
                "rcept_no": lst[0].get("rcept_no") if lst else None}

    def statement(self, corp_code, bsns_year, reprt_code, fs_div, rcept_no=None):
        """재무정보 API 우선. API에 해당 보고서 수치가 없으면 공시원문(document.xml)에서 직접 추출.
          - API에 연결·별도 모두 없음 → 원문에서 추출(공시는 됐지만 재무정보 API 반영 전인 경우)
          - API에 별도만 있고 연결 없음 → 원문에 연결재무제표가 실제로 있을 때만 원문의 연결 수치 사용
          - API에 연결만 있고 별도 없음 → 연결을 쓰므로 원문 불필요
        원문에서도 손익계산서를 못 찾으면 DocParseError(엔진이 같은 기간 다른 접수본으로 재시도)."""
        rows = self.api_statement(corp_code, bsns_year, reprt_code, fs_div)
        if rows or not rcept_no:
            return rows
        other = self.api_statement(corp_code, bsns_year, reprt_code, "OFS" if fs_div == "CFS" else "CFS")
        if other:
            if fs_div == "OFS" or not self.doc_has_consolidated(rcept_no):
                return None
            return self.doc_statements(rcept_no)["CFS"] or None
        doc = self.doc_statements(rcept_no)
        if not doc["CFS"] and not doc["OFS"]:
            raise DocParseError(
                f"OpenDART 재무정보 API에 {bsns_year}년 {REPRT_KO.get(reprt_code, reprt_code)} 수치가 없고, "
                f"공시원문(접수번호 {rcept_no})에서도 손익계산서를 찾지 못함")
        return doc[fs_div] or None

    def document_zip(self, rcept_no):
        """공시원문 원본(zip). 같은 보고서를 연결 여부 확인·수치 추출에 함께 쓰도록 메모리에 잠시 보관."""
        with self._doc_lock:
            if rcept_no in self._doc_raw:
                return self._doc_raw[rcept_no]
        body = self._http("document.xml", {"rcept_no": rcept_no})
        if not body.startswith(b"PK"):
            try:
                msg = json.loads(body.decode("utf-8")).get("message")
            except ValueError:
                msg = body[:120].decode("utf-8", "replace")
            raise DocParseError(f"공시원문(document.xml, 접수번호 {rcept_no}) 수신 실패: {msg}")
        with self._doc_lock:
            self._doc_raw[rcept_no] = body
            while len(self._doc_raw) > 6:
                self._doc_raw.pop(next(iter(self._doc_raw)))
        return body

    def doc_statements(self, rcept_no):
        """공시원문에서 연결/별도 손익계산서 추출 → {"CFS": rows, "OFS": rows, "cfs_present": bool}. 성공 결과는 영구 캐시."""
        ck = "doc_statements:v1:" + rcept_no
        raw = self.store.cache_get(ck, None) if self.store else None
        if raw is not None:
            return json.loads(raw)
        from dart_web import parse_document_zip     # 순환 import 방지(dart_web → dart)
        res = parse_document_zip(self.document_zip(rcept_no), rcept_no)
        if (res["CFS"] or res["OFS"]) and self.store:
            self.store.cache_put(ck, json.dumps(res, ensure_ascii=False).encode())
        return res

    # 공시원문 확인 -----------------------------------------------------------
    _CONS = re.compile(r"연결\s*(재무상태표|손익계산서|포괄손익계산서)")

    def doc_has_consolidated(self, rcept_no):
        """공시원문(document.xml)에 연결재무제표가 실제로 있는지 확인한다."""
        ck = "doc_has_consolidated:" + rcept_no
        raw = self.store.cache_get(ck, None)
        if raw is not None:
            return json.loads(raw)["has"]
        try:
            body = self.document_zip(rcept_no)
        except DocParseError as ex:
            raise DartError(str(ex))
        has = False
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            for name in z.namelist():
                data = z.read(name)
                for enc in ("utf-8", "cp949"):
                    try:
                        text = data.decode(enc)
                    except UnicodeDecodeError:
                        continue
                    if "재무상태표" in text or "손익계산서" in text:
                        has = has or bool(self._CONS.search(text))
                        break
                if has:
                    break
        self.store.cache_put(ck, json.dumps({"has": has}).encode())
        return has
