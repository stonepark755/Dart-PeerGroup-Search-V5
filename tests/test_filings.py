"""정정공시 접수본 선택 + 한국시간 표기 검증.

재현 사례: 한화에어로스페이스(012450)
  사업보고서 (2025.12)            20260316001112  ← 본문(재무제표) 있음
  [첨부정정]사업보고서 (2025.12)  20260319000633  ← 첨부서류만 정정. 접수번호가 더 커서 기존 로직은 이것을 읽음
→ 2025년 4Q(= 연간 − 3Q 누적)를 못 구해 '작년 4Q~금년 3Q' 합계가 산출 불가였음.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dart import DocParseError, KST, now_kst, today_kst  # noqa: E402
from engine import CompanyRun, build_reports  # noqa: E402
from test_scenarios import rows, TODAY  # noqa: E402
from test_windows import stmts  # noqa: E402

ALL3 = lambda c: {"revenue": c, "operating_income": c, "net_income": c}  # noqa: E731
ORIG, ATTACH, FIX = "20260316001112", "20260319000633", "20260401000001"
BASE = [("반기보고서 (2025.06)", "20250814000001"), ("분기보고서 (2025.09)", "20251113000661"),
        ("분기보고서 (2026.03)", "20260513000860"), ("반기보고서 (2026.06)", "20260814000001"),
        ("분기보고서 (2026.09)", "20261112000647")]
SUMMARY = rows("CFS", 9999, 999, 99, ATTACH)   # 첨부정정 문서에서 엉뚱한 표(요약재무 등)를 읽은 경우를 가정


class RcptFake:
    """접수번호별로 원문 파싱 결과가 다른 가짜 웹 클라이언트."""

    def __init__(self, reports, by_period, by_rcept):
        self.reports, self.by_period, self.by_rcept = reports, by_period, by_rcept
        self.calls = []

    def company(self, cc):
        return {"acc_mt": "12", "corp_cls": "Y"}

    def list_periodic(self, cc, b, e):
        return [{"report_nm": n, "rcept_no": r, "rcept_dt": r[:8]} for n, r in self.reports]

    def list_major(self, cc, b, e):
        return []

    def statement(self, cc, y, rc, fs, rcept_no=None):
        self.calls.append(rcept_no)
        if rcept_no in self.by_rcept:
            v = self.by_rcept[rcept_no]
            if isinstance(v, Exception):
                raise v
            return v if fs == "CFS" else None
        return self.by_period.get((y, rc, fs))

    def doc_has_consolidated(self, rn):
        return True


def run(reports, by_rcept, periods):
    f = RcptFake(reports, stmts(True), by_rcept)
    r = CompanyRun(f, {"corp_code": "012450", "corp_name": "한화에어로스페이스", "stock_code": "012450"},
                   TODAY).run(periods)
    return r, f


ANNUAL = rows("CFS", 1270, 127, 87, ORIG)     # 4Q25 = 1270 − 870 = 400


class Filings(unittest.TestCase):
    def test_build_reports_prefers_body_over_attachment_correction(self):
        reps = build_reports([{"report_nm": "사업보고서 (2025.12)", "rcept_no": ORIG},
                              {"report_nm": "[첨부정정]사업보고서 (2025.12)", "rcept_no": ATTACH},
                              {"report_nm": "[첨부추가]사업보고서 (2025.12)", "rcept_no": "20260320000001"}], 12)
        r = reps[(2025, 4)]
        self.assertEqual(r.rcept_no, ORIG)
        self.assertEqual([a.rcept_no for a in r.alternates], ["20260320000001", ATTACH])

    def test_build_reports_still_prefers_body_correction(self):
        reps = build_reports([{"report_nm": "사업보고서 (2025.12)", "rcept_no": ORIG},
                              {"report_nm": "[기재정정]사업보고서 (2025.12)", "rcept_no": FIX}], 12)
        self.assertEqual(reps[(2025, 4)].rcept_no, FIX)

    def test_hanwha_attachment_correction(self):
        reports = BASE + [("사업보고서 (2025.12)", ORIG), ("[첨부정정]사업보고서 (2025.12)", ATTACH)]
        for attach_doc in (DocParseError("손익계산서를 찾지 못함"), SUMMARY):
            r, f = run(reports, {ORIG: ANNUAL, ATTACH: attach_doc}, ALL3("TTM_Q3"))
            it = r["items"]["revenue"]
            self.assertEqual(it["status"], "verified", it.get("message"))
            self.assertEqual(it["period_label"], "2025년 4Q~2026년 3Q")
            self.assertEqual(it["value"], 400 + 310 + 320 + 330)
            self.assertNotIn(ATTACH, f.calls)                        # 첨부정정본은 아예 읽지 않음
            q4 = r["quarters"][0]["items"]["revenue"]
            self.assertIn(ORIG, [s["rcept_no"] for s in q4["sources"]])
        fy, _ = run(reports, {ORIG: ANNUAL, ATTACH: SUMMARY}, ALL3("FY"))
        self.assertEqual(fy["items"]["revenue"]["value"], 1270)

    def test_fallback_when_correction_unreadable(self):
        reports = BASE + [("사업보고서 (2025.12)", ORIG), ("[기재정정]사업보고서 (2025.12)", FIX)]
        r, f = run(reports, {ORIG: ANNUAL, FIX: DocParseError("원문 구조 변경")}, ALL3("TTM_Q3"))
        self.assertEqual(r["items"]["revenue"]["value"], 1360)
        self.assertEqual(f.calls.count(FIX), 1)
        q4 = r["quarters"][0]
        self.assertEqual(q4["report"]["rcept_no"], ORIG)
        self.assertIn("[기재정정]", q4["report"]["replaced"])
        self.assertTrue(any("손익계산서를 읽지 못해" in n and ORIG in n for n in r["notes"]))
        fy, _ = run(reports, {ORIG: ANNUAL, FIX: DocParseError("원문 구조 변경")}, ALL3("FY"))
        self.assertEqual(fy["items"]["revenue"]["value"], 1270)
        self.assertIn(ORIG, fy["items"]["revenue"]["message"])

    def test_all_filings_unreadable(self):
        reports = BASE + [("사업보고서 (2025.12)", ORIG), ("[첨부정정]사업보고서 (2025.12)", ATTACH)]
        r, _ = run(reports, {ORIG: DocParseError("A 실패"), ATTACH: DocParseError("B 실패")}, ALL3("TTM_Q3"))
        self.assertIsNone(r["items"]["revenue"]["value"])
        msg = r["quarters"][0]["items"]["revenue"]["message"]
        self.assertIn("A 실패", msg)
        self.assertIn("B 실패", msg)


class KoreanTime(unittest.TestCase):
    def test_kst_helpers(self):
        self.assertEqual(now_kst().utcoffset(), timedelta(hours=9))
        self.assertEqual(today_kst(), now_kst().date())

    def test_search_time_is_kst_even_on_utc_server(self):
        import server
        utc = datetime(2026, 11, 15, 23, 5, 0, tzinfo=timezone.utc)      # 해외 서버 시계: 11/15 23:05 UTC
        with mock.patch.object(server, "now_kst", lambda: utc.astimezone(KST)), \
             mock.patch.object(server, "today_kst", lambda: utc.astimezone(KST).date()):
            d = server.run_query({"items": []})
        self.assertEqual(d["searched_at"], "2026-11-16T08:05:00")          # 한국시간 11/16 08:05
        self.assertEqual(d["base_year"], 2026)


if __name__ == "__main__":
    unittest.main()
