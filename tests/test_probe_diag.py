"""공시목록(list.json)에 없는 분기를 재무정보 API로 확인 + 진단 엔드포인트.

재현 사례: 한화에어로스페이스 상세조회에서 2026 3Q 가 '보고서 미공시(최근 공시: 2026년 2Q)'로 표시
→ 공시목록 단계에서 3분기보고서를 못 받아온 상황(실제 원인은 /api/diag 로 확인 예정).
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dart import Store  # noqa: E402
from engine import CompanyRun  # noqa: E402
from test_scenarios import rows, TODAY  # noqa: E402
from test_api_document_fallback import FakeHttpClient, REPORTS, Q3, B, ALL3, api_rows  # noqa: E402

LIST_WITHOUT_Q3 = [r for r in REPORTS if r[1] != Q3]


def q3_rows():
    out = rows("CFS", 330 * B, 33 * B, 23 * B, Q3, add={"rev": 960 * B, "op": 96 * B, "ni": 66 * B})
    for r in out:
        r["rcept_no"] = Q3                    # 재무정보 API 행에는 보고서 접수번호가 들어 있음
    return out


class Probe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "t.sqlite"))

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def run_engine(self, client, periods=ALL3("TTM_Q3"), today=TODAY):
        return CompanyRun(client, {"corp_code": "00126566", "corp_name": "한화에어로스페이스", "stock_code": "012450"},
                          today).run(periods)

    def test_missing_in_list_found_via_fnltt(self):
        api = api_rows()
        api[("2026", "11014", "CFS")] = q3_rows()
        c = FakeHttpClient(self.store, api, {}, reports=LIST_WITHOUT_Q3)
        r = self.run_engine(c)
        it = r["items"]["revenue"]
        self.assertEqual(it["status"], "verified", it.get("message"))
        self.assertEqual(it["value"], 1360 * B)
        q3 = r["quarters"][-1]
        self.assertEqual(q3["report"]["rcept_no"], Q3)
        self.assertEqual(q3["report"]["found_via"], "재무정보 API")
        self.assertTrue(any("공시목록(list.json)에서 보고서를 찾지 못해" in n for n in r["notes"]))

    def test_missing_everywhere_stays_unfiled(self):
        c = FakeHttpClient(self.store, api_rows(), {}, reports=LIST_WITHOUT_Q3)
        r = self.run_engine(c)
        self.assertIsNone(r["items"]["revenue"]["value"])
        self.assertTrue(r["quarters"][-1]["unfiled"])
        self.assertEqual(c.n("fnlttSinglAcntAll.json", reprt_code="11014", bsns_year=2026), 2)   # CFS·OFS 확인

    def test_quarter_not_ended_is_not_probed(self):
        from datetime import date
        c = FakeHttpClient(self.store, api_rows(), {}, reports=LIST_WITHOUT_Q3)
        self.run_engine(c, today=date(2026, 9, 20))            # 3분기 말(9/30) 이전
        self.assertEqual(c.n("fnlttSinglAcntAll.json", reprt_code="11014", bsns_year=2026), 0)


class Diag(unittest.TestCase):
    def test_diag_shows_raw_list_and_fnltt(self):
        import server
        tmp = tempfile.TemporaryDirectory()
        store = Store(os.path.join(tmp.name, "t.sqlite"))
        api = api_rows()
        c = FakeHttpClient(store, api, {}, reports=LIST_WITHOUT_Q3)
        c.corp_list = lambda: [{"corp_code": "00126566", "corp_name": "한화에어로스페이스", "stock_code": "012450"}]
        with mock.patch.object(server, "CLIENT", c), mock.patch.object(server, "SOURCE", "api"), \
                mock.patch.object(server, "today_kst", lambda: TODAY):
            d = server.diag("012450")
        store.db.close()
        tmp.cleanup()
        self.assertEqual(d["company"]["acc_mt"], "12")
        self.assertEqual(len(d["list_5y"]["rows"]), len(LIST_WITHOUT_Q3))
        self.assertIn("list_recent", d)
        self.assertIn("2026년 2Q", d["recognized"])
        self.assertNotIn("2026년 3Q", d["recognized"])
        self.assertEqual(d["fnltt"]["2026년 3Q"]["CFS"]["status"], "013")
        self.assertEqual(d["fnltt"]["2026년 2Q"]["CFS"]["status"], "000")
        self.assertEqual(len(d["fnltt"]), 6)


if __name__ == "__main__":
    unittest.main()
