"""API 모드: 재무정보 API(fnlttSinglAcntAll)가 비어 있으면 공시원문(document.xml)에서 직접 추출.

재현 사례: 한화에어로스페이스 2026 3분기보고서(20261112000647)는 DART 에 공시됐지만
재무정보 API 가 '조회된 데이터 없음(013)'을 돌려줘 '작년 4Q~금년 3Q' 합계가 산출 불가였음.
실제 DartClient 를 쓰고 HTTP(_http)만 가짜로 바꾼다.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dart import DartClient, Store  # noqa: E402
from engine import CompanyRun  # noqa: E402
from test_scenarios import rows, TODAY  # noqa: E402
from fixture_dart_xml import quarterly_xml, zip_of  # noqa: E402

B = 1_000_000_000
Q3 = "20261112000647"
REPORTS = [("반기보고서 (2025.06)", "20250814000001"), ("분기보고서 (2025.09)", "20251113000661"),
           ("사업보고서 (2025.12)", "20260316001112"), ("분기보고서 (2026.03)", "20260513000860"),
           ("반기보고서 (2026.06)", "20260814000001"), ("분기보고서 (2026.09)", Q3)]
NO_DATA = {"status": "013", "message": "조회된 데이타가 없습니다."}
ALL3 = lambda c: {"revenue": c, "operating_income": c, "net_income": c}  # noqa: E731


def api_rows():
    """2026 3Q 를 뺀 기간의 재무정보 API 응답(연결)."""
    return {
        ("2025", "11014", "CFS"): rows("CFS", 300 * B, 30 * B, 20 * B, "q3_25", add={"rev": 870 * B, "op": 87 * B, "ni": 57 * B}),
        ("2025", "11011", "CFS"): rows("CFS", 1270 * B, 127 * B, 87 * B, "ar_25"),
        ("2026", "11013", "CFS"): rows("CFS", 310 * B, 31 * B, 21 * B, "q1_26"),
        ("2026", "11012", "CFS"): rows("CFS", 320 * B, 32 * B, 22 * B, "h_26", add={"rev": 630 * B, "op": 63 * B, "ni": 43 * B}),
    }


class FakeHttpClient(DartClient):
    def __init__(self, store, api, docs):
        super().__init__(lambda: "k" * 40, store)
        self.api, self.docs, self.calls = api, docs, []

    def _http(self, endpoint, params):
        self.calls.append((endpoint, params.get("bsns_year"), params.get("reprt_code"),
                           params.get("fs_div"), params.get("rcept_no")))
        if endpoint == "company.json":
            out = {"status": "000", "acc_mt": "12", "corp_cls": "Y"}
        elif endpoint == "list.json":
            lst = [{"report_nm": n, "rcept_no": r, "rcept_dt": r[:8]} for n, r in REPORTS] \
                if params.get("pblntf_ty") == "A" else []
            out = {"status": "000", "total_page": 1, "list": lst}
        elif endpoint == "fnlttSinglAcntAll.json":
            k = (str(params["bsns_year"]), params["reprt_code"], params["fs_div"])
            out = {"status": "000", "list": self.api[k]} if k in self.api else NO_DATA
        elif endpoint == "document.xml":
            return self.docs.get(params["rcept_no"], json.dumps({"status": "014", "message": "파일이 존재하지 않습니다."}).encode())
        else:
            raise AssertionError(endpoint)
        return json.dumps(out, ensure_ascii=False).encode("utf-8")

    def n(self, endpoint, **kw):
        idx = {"bsns_year": 1, "reprt_code": 2, "fs_div": 3, "rcept_no": 4}
        return sum(1 for c in self.calls if c[0] == endpoint and all(c[idx[k]] == v for k, v in kw.items()))


class ApiDocumentFallback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "t.sqlite"))

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def run_engine(self, client, periods=ALL3("TTM_Q3")):
        return CompanyRun(client, {"corp_code": "00126566", "corp_name": "한화에어로스페이스", "stock_code": "012450"},
                          TODAY).run(periods)

    def test_hanwha_q3_read_from_document(self):
        c = FakeHttpClient(self.store, api_rows(), {Q3: zip_of(Q3, quarterly_xml(Q3))})
        r = self.run_engine(c)
        it = r["items"]["revenue"]
        self.assertEqual(it["status"], "verified", it.get("message"))
        self.assertEqual(it["period_label"], "2025년 4Q~2026년 3Q")
        q3 = r["quarters"][-1]
        self.assertEqual(q3["items"]["revenue"]["value"], 6_500_000 * 10**6)        # 백만원 → 원
        self.assertEqual(q3["items"]["operating_income"]["value"], 900_000 * 10**6)
        self.assertEqual(q3["items"]["net_income"]["value"], 500_000 * 10**6)      # 지배기업 소유주 귀속
        self.assertEqual(it["value"], (400 + 310 + 320) * B + 6_500_000 * 10**6)
        self.assertIn("공시원문", q3["basis_evidence"])
        self.assertEqual(q3["items"]["revenue"]["sources"][0]["rcept_no"], Q3)
        self.assertTrue(any("직접 추출" in n and Q3 in n for n in r["notes"]))
        self.assertEqual(c.n("document.xml"), 1)                                    # 원문은 한 번만 내려받음

    def test_api_present_no_document_call(self):
        api = api_rows()
        api[("2026", "11014", "CFS")] = rows("CFS", 330 * B, 33 * B, 23 * B, "q3_26", add={"rev": 960 * B, "op": 96 * B, "ni": 66 * B})
        c = FakeHttpClient(self.store, api, {})
        r = self.run_engine(c)
        self.assertEqual(r["items"]["revenue"]["value"], 1360 * B)
        self.assertEqual(c.n("document.xml"), 0)
        self.assertFalse(any("직접 추출" in n for n in r["notes"]))

    def test_no_data_anywhere_reports_reason(self):
        c = FakeHttpClient(self.store, api_rows(), {})                              # 원문 수신도 실패
        r = self.run_engine(c)
        self.assertIsNone(r["items"]["revenue"]["value"])
        msg = r["quarters"][-1]["items"]["revenue"]["message"]
        self.assertIn(Q3, msg)
        self.assertIn("document.xml", msg)

    def test_api_ofs_only_document_has_consolidated(self):
        # 재무정보 API 엔 별도만 있고, 원문엔 연결이 있음 → 원문 연결 수치 사용
        api = api_rows()
        api[("2026", "11014", "OFS")] = rows("OFS", 2 * B, 1 * B, 1 * B, "q3_26o", add={"rev": 6 * B, "op": 3 * B, "ni": 2 * B})
        c = FakeHttpClient(self.store, api, {Q3: zip_of(Q3, quarterly_xml(Q3))})
        r = self.run_engine(c)
        q3 = r["quarters"][-1]
        self.assertEqual(q3["fs_div"], "CFS")
        self.assertEqual(q3["items"]["revenue"]["value"], 6_500_000 * 10**6)
        self.assertEqual(c.n("document.xml"), 1)

    def test_ofs_only_company_keeps_api_ofs(self):
        # 연결 대상 종속회사가 없는 회사: API 에 별도만 있고 원문에도 연결 없음 → 별도(API) 그대로
        from fixture_dart_xml import HEAD
        ofs_doc = (HEAD + '<BODY><SECTION-1><TITLE ATOC="Y">III. 재무에 관한 사항</TITLE>'
                   '<SECTION-2><TITLE ATOC="Y">2. 재무제표</TITLE><P>재무상태표</P><P>(단위 : 원)</P>'
                   '<TABLE><TR><TD>자산총계</TD><TE>1,000,000</TE><TE>900,000</TE></TR></TABLE>'
                   '</SECTION-2></SECTION-1></BODY></DOCUMENT>')
        api = {(y, rc, "OFS"): v for (y, rc, _), v in api_rows().items()}
        for k, v in list(api.items()):
            api[k] = [dict(r, account_nm="당기순이익") if r["account_id"] == "ifrs-full_ProfitLoss" else r
                      for r in v if "Attributable" not in (r["account_id"] or "")]
        api[("2026", "11014", "OFS")] = rows("OFS", 330 * B, 33 * B, 23 * B, "q3o", add={"rev": 960 * B, "op": 96 * B, "ni": 66 * B})
        docs = {r: zip_of(r, ofs_doc) for _, r in REPORTS}
        c = FakeHttpClient(self.store, api, docs)
        r = self.run_engine(c)
        self.assertEqual(r["quarters"][-1]["fs_div"], "OFS")
        self.assertEqual(r["items"]["revenue"]["value"], (400 + 310 + 320 + 330) * B)
        self.assertFalse(any("직접 추출" in n for n in r["notes"]))

    def test_empty_response_rechecked_after_an_hour(self):
        c = FakeHttpClient(self.store, api_rows(), {Q3: zip_of(Q3, quarterly_xml(Q3))})
        self.assertIsNone(c.api_statement("x", 2026, "11014", "CFS"))
        self.assertIsNone(c.api_statement("x", 2026, "11014", "CFS"))
        self.assertEqual(c.n("fnlttSinglAcntAll.json", reprt_code="11014", fs_div="CFS"), 1)   # 1시간 내 재사용
        with self.store.lock:
            self.store.db.execute("UPDATE raw_cache SET fetched_at = fetched_at - 3700 WHERE k LIKE 'fnlttSinglAcntAll%'")
        c.api[("2026", "11014", "CFS")] = rows("CFS", 1, 1, 1, "late")              # 이후 API 에 반영됨
        self.assertTrue(c.api_statement("x", 2026, "11014", "CFS"))
        self.assertEqual(c.n("fnlttSinglAcntAll.json", reprt_code="11014", fs_div="CFS"), 2)

    def test_annual_fy_from_document(self):
        from fixture_dart_xml import annual_xml
        api = {k: v for k, v in api_rows().items() if k[1] != "11011"}
        ar = "20260316001112"
        c = FakeHttpClient(self.store, api, {Q3: zip_of(Q3, quarterly_xml(Q3)), ar: zip_of(ar, annual_xml(ar))})
        r = self.run_engine(c, ALL3("FY"))
        self.assertEqual(r["items"]["revenue"]["value"], 25_000_000 * 10**6)
        self.assertEqual(r["items"]["net_income"]["value"], 1_900_000 * 10**6)
        self.assertIn("직접 추출", r["items"]["revenue"]["message"])


if __name__ == "__main__":
    unittest.main()
