"""4개 분기 합산 구간 고정(작년 2Q~금년 1Q / 작년 3Q~금년 2Q / 작년 4Q~금년 3Q) 검증.

문제 상황: '최근 4개 분기'를 회사별 최신 공시로 잡으면 3분기보고서를 아직 내지 않은 회사(예: 한텍)는
2025 3Q~2026 2Q, 이미 낸 회사(예: 비에이치아이)는 2025 4Q~2026 3Q 가 되어 합산 기간이 달라진다.
→ 검색자가 고른 구간으로 모든 종목을 고정한다. 미공시 분기는 다른 분기로 대체하지 않고 '산출 불가'.
"""
import io
import os
import sys
import unittest
import zipfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import CompanyRun  # noqa: E402
from xlsx import build_workbook  # noqa: E402
from test_scenarios import Fake, rows, TODAY  # noqa: E402

ALL3 = lambda c: {"revenue": c, "operating_income": c, "net_income": c}  # noqa: E731

REPORTS_FILED = [("반기보고서 (2025.06)", "20250814000001"), ("분기보고서 (2025.09)", "20251114000001"),
                 ("사업보고서 (2025.12)", "20260320000001"), ("분기보고서 (2026.03)", "20260515000001"),
                 ("반기보고서 (2026.06)", "20260814000001"), ("분기보고서 (2026.09)", "20261113000001")]
REPORTS_NOT_YET = REPORTS_FILED[:-1]          # 2026 3분기보고서 미제출


def stmts(with_q3_26=True):
    s = {(2025, "11012", "CFS"): rows("CFS", 290, 29, 19, "h_25", add={"rev": 570, "op": 57, "ni": 37}),
         (2025, "11014", "CFS"): rows("CFS", 300, 30, 20, "q3_25", add={"rev": 870, "op": 87, "ni": 57}),
         (2025, "11011", "CFS"): rows("CFS", 1270, 127, 87, "ar_25"),        # 4Q25 = 1270-870 = 400
         (2026, "11013", "CFS"): rows("CFS", 310, 31, 21, "q1_26"),
         (2026, "11012", "CFS"): rows("CFS", 320, 32, 22, "h_26", add={"rev": 630, "op": 63, "ni": 43})}
    if with_q3_26:
        s[(2026, "11014", "CFS")] = rows("CFS", 330, 33, 23, "q3_26", add={"rev": 960, "op": 96, "ni": 66})
    return s


def run(reports, s, periods, base_year=None, fye="12"):
    f = Fake(reports, s)
    f.company = lambda cc: {"acc_mt": fye, "corp_cls": "Y"}
    return CompanyRun(f, {"corp_code": "1", "corp_name": "테스트", "stock_code": "000001", "market": "KOSDAQ"},
                      TODAY).run(periods, base_year)


class Windows(unittest.TestCase):
    def test_same_window_regardless_of_filing(self):
        bhi = run(REPORTS_FILED, stmts(True), ALL3("TTM_Q3"))           # 3Q 공시 완료 (비에이치아이형)
        hantec = run(REPORTS_NOT_YET, stmts(False), ALL3("TTM_Q3"))     # 3Q 미공시 (한텍형)
        for r in (bhi, hantec):
            self.assertEqual([q["label"] for q in r["quarters"]],
                             ["2025년 4Q", "2026년 1Q", "2026년 2Q", "2026년 3Q"])
            self.assertEqual(r["items"]["revenue"]["period_label"], "2025년 4Q~2026년 3Q")
            self.assertEqual(r["items"]["revenue"]["window"], "TTM_Q3")
        self.assertEqual(bhi["items"]["revenue"]["value"], 400 + 310 + 320 + 330)
        self.assertEqual(bhi["quarters"][0]["period_start"], "2025-10-01")   # 4Q 행은 3개월 기간 표기
        # 한텍형: 구간을 앞으로 당겨 합산하지 않고 '산출 불가' + 미공시 사유
        it = hantec["items"]["revenue"]
        self.assertIsNone(it["value"])
        self.assertEqual(it["status"], "unavailable")
        q3 = hantec["quarters"][-1]["items"]["revenue"]
        self.assertIn("미공시", q3["message"])
        self.assertIn("2026년 2Q", q3["message"])            # 최근 공시 분기 안내
        self.assertEqual(hantec["basis_display"], "연결")    # 미공시 분기가 아닌, 판정된 마지막 분기 기준
        self.assertTrue(hantec["quarters"][-1]["unfiled"])
        self.assertEqual(hantec["quarters"][-1]["period_end"], "2026-09-30")
        self.assertIn("미공시", it["message"])
        self.assertFalse(any(q["unfiled"] for q in bhi["quarters"]))

    def test_q2_window_both_companies_comparable(self):
        a = run(REPORTS_FILED, stmts(True), ALL3("TTM_Q2"))
        b = run(REPORTS_NOT_YET, stmts(False), ALL3("TTM_Q2"))
        for r in (a, b):
            self.assertEqual(r["items"]["revenue"]["period_label"], "2025년 3Q~2026년 2Q")
            self.assertEqual(r["items"]["revenue"]["value"], 300 + 400 + 310 + 320)
            self.assertEqual(r["items"]["net_income"]["value"], 20 + 30 + 21 + 22)   # 4Q25 NI = 87-57

    def test_q1_window(self):
        r = run(REPORTS_FILED, stmts(True), ALL3("TTM_Q1"))
        self.assertEqual(r["items"]["revenue"]["period_label"], "2025년 2Q~2026년 1Q")
        self.assertEqual(r["items"]["revenue"]["value"], 290 + 300 + 400 + 310)
        self.assertEqual(r["windows"][0]["label"], "4개분기(작년 2Q~금년 1Q)")

    def test_items_with_different_windows(self):
        r = run(REPORTS_FILED, stmts(True),
                {"revenue": "TTM_Q3", "operating_income": "FY", "net_income": "TTM_Q2"})
        self.assertEqual([w["code"] for w in r["windows"]], ["TTM_Q3", "TTM_Q2"])
        self.assertEqual(r["items"]["revenue"]["value"], 1360)
        self.assertEqual(r["items"]["net_income"]["value"], 93)
        self.assertEqual(r["items"]["net_income"]["period_label"], "2025년 3Q~2026년 2Q")
        self.assertEqual(r["items"]["operating_income"]["period_label"], "2025년 연간")
        self.assertEqual(r["windows"][1]["items"], ["net_income"])

    def test_base_year_previous(self):
        # 기준연도 2025: 작년 4Q~금년 3Q = 2024 4Q~2025 3Q → 2024 보고서가 없으므로 산출 불가(미공시 아님)
        r = run(REPORTS_FILED, stmts(True), ALL3("TTM_Q3"), base_year=2025)
        self.assertEqual(r["items"]["revenue"]["period_label"], "2024년 4Q~2025년 3Q")
        self.assertEqual(r["items"]["revenue"]["status"], "unavailable")
        self.assertNotIn("미공시", r["quarters"][0]["items"]["revenue"]["message"])

    def test_non_december_fye_maps_calendar_quarter(self):
        # 3월 결산: 달력 2026-09-30 은 2026 사업연도 2Q (DART bsns_year 규칙)
        f = Fake([("반기보고서 (2026.09)", "20261114000001")], {})
        f.company = lambda cc: {"acc_mt": "3", "corp_cls": "Y"}
        c = CompanyRun(f, {"corp_code": "1", "corp_name": "x", "stock_code": "1"}, TODAY)
        self.assertEqual(c.window_end("TTM_Q3", 2026), ((2026, 2), None))
        f.company = lambda cc: {"acc_mt": "11", "corp_cls": "Y"}       # 분기말과 안 맞는 결산월
        c = CompanyRun(f, {"corp_code": "1", "corp_name": "x", "stock_code": "1"}, TODAY)
        key, err = c.window_end("TTM_Q3", 2026)
        self.assertIsNone(key)
        self.assertIn("결산월", err)

    def test_workbook_multiple_windows(self):
        r = run(REPORTS_FILED, stmts(True), {"revenue": "TTM_Q3", "operating_income": "TTM_Q3", "net_income": "TTM_Q2"})
        h = run(REPORTS_NOT_YET, stmts(False), ALL3("TTM_Q3"))
        data = build_workbook({"searched_at": "2026-11-13T10:00:00", "unit": "원", "base_year": 2026, "results": [r, h]})
        z = zipfile.ZipFile(io.BytesIO(data))
        for n in z.namelist():
            ET.fromstring(z.read(n))
        s1 = z.read("xl/worksheets/sheet1.xml").decode()
        s2 = z.read("xl/worksheets/sheet2.xml").decode()
        self.assertIn("기준연도(금년): 2026년", s1)
        self.assertIn("<v>1360</v>", s1)
        self.assertIn("2025년 3Q~2026년 2Q", s1)
        self.assertIn("4개분기(작년 4Q~금년 3Q)", s2)
        self.assertIn("4개분기(작년 3Q~금년 2Q)", s2)
        self.assertIn("미공시", s2)


if __name__ == "__main__":
    unittest.main()
