# -*- coding: utf-8 -*-
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "")
from hanun_card import (
    classify_issue, is_new_issue, build_hanun_card, issues_snapshot,
    HANUN_BIAS_PCT, strip_auto,
)


class HanunCardRules(unittest.TestCase):
    def test_bias_threshold_60(self):
        self.assertEqual(HANUN_BIAS_PCT, 60.0)
        # 38% + 8채널 → 확산
        self.assertEqual(classify_issue(channels=8, top_share=38.2, is_new=False), ("spread", False))
        # 70% → 내부
        self.assertEqual(classify_issue(channels=3, top_share=69.7, is_new=False), ("internal", False))
        # 56.6% + 3채널 → 확산 (60% 미만, 3채널은 소수 채널 예외 아님)
        self.assertEqual(classify_issue(channels=3, top_share=56.6, is_new=False), ("spread", False))
        # 2채널 → 내부 (소수 채널 예외)
        self.assertEqual(classify_issue(channels=2, top_share=50.0, is_new=False), ("internal", False))
        # 10/29 김어준 논란: 9건·3채널·최대 49.2% → 확산
        self.assertEqual(classify_issue(channels=3, top_share=49.2, is_new=False), ("spread", False))

    def test_new_exception_overrides(self):
        bucket, exc = classify_issue(channels=2, top_share=56.6, is_new=True)
        self.assertEqual(bucket, "spread")
        self.assertTrue(exc)
        bucket2, exc2 = classify_issue(channels=3, top_share=70.0, is_new=True)
        self.assertEqual((bucket2, exc2), ("spread", True))

    def test_is_new_issue(self):
        self.assertTrue(is_new_issue("김건희 구속 연장 (자동)", {}, is_auto=True))
        self.assertFalse(is_new_issue("조희대/사법부", {"조희대/사법부": {}}, is_auto=False))
        self.assertTrue(is_new_issue("김건희 구속 연장 (자동)", {"조희대/사법부": {}}, is_auto=True))
        # 마이그레이션: 빈 prev → 힌트+자동만
        self.assertTrue(is_new_issue("김건희 구속 연장 (자동)", None, is_auto=True))
        self.assertFalse(is_new_issue("조희대/사법부", None, is_auto=False))

    def test_card_places_kim_and_handong(self):
        issues = [
            {"이슈명": "조희대/사법부", "건수": 24, "채널수": 8, "조회수": 796743,
             "concentration": {"top_share": 38.2}, "is_auto": False},
            {"이슈명": "김건희 구속 연장 (자동)", "건수": 4, "채널수": 3, "조회수": 344966,
             "concentration": {"top_share": 56.6}, "is_auto": True},
            {"이슈명": "한동훈 딸 수사 의혹 (자동)", "건수": 4, "채널수": 3, "조회수": 172965,
             "concentration": {"top_share": 69.7}, "is_auto": True},
            {"이슈명": "유시민 논란", "건수": 4, "채널수": 2, "조회수": 363733,
             "concentration": {"top_share": 74.5}, "is_auto": False},
        ]
        persons = [
            {"name": "주진우 (자동)", "display": "주진우 기자 (자동)", "views_standalone": 984609,
             "top_share": 32.9, "n_ch": 5, "pct_change": 168.7, "trend_short": "+169%"},
            {"name": "한동훈", "display": "한동훈", "views_standalone": 389404,
             "top_share": 32.6, "n_ch": 5, "pct_change": -28.6, "trend_short": "-29%", "from_issue": True},
            {"name": "오세훈", "display": "오세훈", "views_standalone": 294592,
             "top_share": 76.6, "n_ch": 3, "pct_change": -78.3, "trend_short": "-78%"},
        ]
        prev = {"조희대/사법부": {}, "유시민 논란": {}, "한동훈 딸 수사 의혹": {}}
        card = build_hanun_card(
            now_str="2026-10-28 14:44",
            issue_rows=issues,
            prev_issues=prev,
            person_rows=persons,
            df_top_rows=[{"프레임": "종합방송", "조회수": 1183015, "채널명": "뉴스공장", "제목": "풀버전"}],
            unclassified_rows=[{"조회수": 490588, "제목": "프랑스 사태 한편으로 완벽 이해! [코너별 다시보기]"}],
        )
        # 김건희 = 3채널·56.6% → 규칙상 확산 (예외 불필요)
        spread_part = card.split("내부·편중")[0]
        self.assertIn("김건희 구속 연장", spread_part)
        self.assertNotIn("신규 속보 예외", spread_part)
        self.assertIn("조희대", spread_part)
        # 한동훈·유시민은 내부
        internal_part = card.split("내부·편중")[1]
        self.assertIn("한동훈", internal_part)
        self.assertIn("유시민", internal_part)
        self.assertIn("주진우", internal_part)
        self.assertIn("종합방송", card)
        self.assertIn("원문 리포트가 이어집니다", card)

    def test_josa(self):
        from hanun_card import josa
        self.assertEqual(josa("이태원 참사 4주기", "이/가"), "이태원 참사 4주기가")
        self.assertEqual(josa("김민석", "은/는"), "김민석은")
        self.assertEqual(josa("유시민", "이/가"), "유시민이")
        self.assertEqual(josa("오세훈", "과/와"), "오세훈과")
        self.assertEqual(josa("조희대/사법부", "이/가"), "조희대/사법부가")

    def _card_1029(self):
        issues = [
            {"이슈명": "이태원 참사 4주기", "건수": 12, "채널수": 7, "조회수": 520000,
             "concentration": {"top_share": 35.0}, "is_auto": True},
            {"이슈명": "김어준 논란", "건수": 9, "채널수": 3, "조회수": 419000,
             "concentration": {"top_share": 49.2}, "is_auto": False},
        ]
        persons = [
            {"name": "김민석", "display": "김민석", "views_standalone": 300000,
             "top_share": 91.4, "n_ch": 2, "pct_change": 131.0},
            {"name": "유시민", "display": "유시민", "views_standalone": 500000,
             "top_share": 80.0, "n_ch": 3, "pct_change": 385.7},
            {"name": "오세훈", "display": "오세훈", "views_standalone": 400000,
             "top_share": 40.0, "n_ch": 5, "pct_change": 122.0},
            {"name": "김어준", "display": "김어준", "views_standalone": 200000,
             "top_share": 45.0, "n_ch": 4, "pct_change": -33.7},
        ]
        return build_hanun_card(now_str="2026-10-29 14:50", issue_rows=issues,
                                prev_issues={"이태원 참사 4주기": {}, "김어준 논란": {}},
                                person_rows=persons)

    def test_1029_kim_eojun_issue_spread(self):
        card = self._card_1029()
        spread_part = card.split("내부·편중")[0]
        self.assertIn("<b>김어준 논란</b>", spread_part)
        self.assertNotIn("김어준 논란", card.split("내부·편중")[1])

    def test_1029_one_liner(self):
        card = self._card_1029()
        one = card.split("<b>한 줄</b>")[1].split("<b>확산")[0]
        self.assertIn("이태원 참사 4주기와 김어준 논란이 넓게 깔린다", one)
        self.assertNotIn("4주기이", one)
        self.assertNotIn("끌어올린다", one)
        self.assertNotIn("김민석는", one)
        # 급등 상위는 유시민·오세훈, 편중 표기
        self.assertIn("유시민 +386%(편중 80%)", one)
        self.assertIn("오세훈 +122%", one)
        self.assertNotIn("김어준 +", one)

    def test_one_liner_biased_only(self):
        from hanun_card import build_one_liner
        one = build_one_liner([], [{"name": "김민석", "pct_change": 131.0, "top_share": 91.4}])
        self.assertEqual(one, "인물은 전일 대비 김민석 +131%(편중 91%)로 특정 채널 몰림이다.")

    def test_snapshot_keys(self):
        snap = issues_snapshot([
            {"이슈명": "김건희 구속 연장 (자동)", "건수": 1, "채널수": 3, "조회수": 100,
             "concentration": {"top_share": 50.0}, "is_auto": True},
        ])
        self.assertIn("김건희 구속 연장", snap)
        self.assertEqual(snap["김건희 구속 연장"]["channels"], 3)


if __name__ == "__main__":
    unittest.main()
