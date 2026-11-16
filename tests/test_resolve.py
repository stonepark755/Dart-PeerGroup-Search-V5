"""기업명 붙여넣기(일괄 유사매칭) 검증: server.resolve_names / norm_name."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402


class FakeClient:
    CORPS = [
        {"corp_code": "00126380", "corp_name": "삼성전자", "stock_code": "005930"},
        {"corp_code": "00126381", "corp_name": "삼성전자우", "stock_code": "005935"},
        {"corp_code": "00164779", "corp_name": "SK하이닉스", "stock_code": "000660"},
        {"corp_code": "00111722", "corp_name": "현대자동차", "stock_code": "005380"},
        {"corp_code": "00999999", "corp_name": "코넥스기업", "stock_code": "900000"},
    ]
    CLS = {"005930": "Y", "005935": "Y", "000660": "Y", "005380": "Y", "900000": "N"}

    def corp_list(self):
        return self.CORPS

    def company(self, cc):
        sc = next(c["stock_code"] for c in self.CORPS if c["corp_code"] == cc)
        return {"corp_cls": self.CLS[sc]}


class R(unittest.TestCase):
    def setUp(self):
        self._orig = server.CLIENT
        server.CLIENT = FakeClient()

    def tearDown(self):
        server.CLIENT = self._orig

    def _by_input(self, names):
        return {r["input"]: r for r in server.resolve_names(names)}

    def test_exact_match(self):
        r = self._by_input(["삼성전자"])["삼성전자"]
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["match"]["stock_code"], "005930")
        self.assertEqual(r["match"]["market"], "KOSPI")
        self.assertEqual(r["score"], 1.0)

    def test_normalization_space_and_case(self):
        r = self._by_input(["sk 하이닉스"])["sk 하이닉스"]
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["match"]["stock_code"], "000660")

    def test_approximate_match_surfaces_candidate(self):
        r = self._by_input(["현대차"])["현대차"]
        self.assertEqual(r["status"], "approx")
        self.assertEqual(r["match"]["corp_name"], "현대자동차")
        self.assertTrue(0 < r["score"] < 1)
        self.assertTrue(any(c["corp_name"] == "현대자동차" for c in r["candidates"]))

    def test_unlisted_market_excluded(self):
        # KONEX(N) 는 상장(KOSPI/KOSDAQ)으로 보지 않으므로 매칭 대상에서 제외
        r = self._by_input(["코넥스기업"])["코넥스기업"]
        self.assertEqual(r["status"], "notfound")
        self.assertIsNone(r["match"])

    def test_not_found(self):
        r = self._by_input(["존재하지않는회사명ABC"])["존재하지않는회사명ABC"]
        self.assertEqual(r["status"], "notfound")
        self.assertEqual(r["candidates"], [])

    def test_dedup_and_blank_lines(self):
        res = server.resolve_names(["삼성전자", "  삼성전자 ", "", "   "])
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["input"], "삼성전자")

    def test_norm_name_strips_corp_suffixes(self):
        self.assertEqual(server.norm_name("삼성전자(주)"), server.norm_name("삼성전자"))
        self.assertEqual(server.norm_name("㈜카카오"), "카카오")


if __name__ == "__main__":
    unittest.main()
