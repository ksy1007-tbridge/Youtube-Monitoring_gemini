import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "")
import main
from person_facts import normalize_report_text, label_homonyms, remove_banned_words


class QA1027(unittest.TestCase):
    def test_pct_requires_prev_standalone(self):
        self.assertNotIn("%", main.person_trend(16310, {"views": 999}, False)[0])          # 기준값 없음
        self.assertNotIn("%", main.person_trend(16310, {"views_standalone": 999}, False)[0])  # 기준값 작음(김민석 +1531%)
        self.assertIn("%", main.person_trend(20000, {"views_standalone": 10000}, False)[0])
        self.assertIn("전일 기록 없음", main.person_trend(5, None, False)[0])

    def test_new_clip(self):
        self.assertIn("신작", main.person_trend(37, {"views_standalone": 3661}, True)[0])

    def test_prev_lookup_auto_key(self):   # 유시민 증감 누락
        k, rec = main.find_prev_person("유시민", {"유시민 (자동)": {"views_standalone": 5000}})
        self.assertEqual(k, "유시민 (자동)")

    def test_display(self):
        self.assertEqual(main.person_display_name("장동혁"), "국민의힘 장동혁 대표")
        self.assertEqual(main.person_display_name("주진우 (자동)"), "주진우 기자 (자동)")

    def test_text(self):
        self.assertEqual(normalize_report_text("장동혁 의원이 말했다"), "국민의힘 장동혁 대표가 말했다")
        self.assertEqual(normalize_report_text("국민의힘 최고위원 장동혁"), "국민의힘 장동혁 대표")
        self.assertEqual(label_homonyms("주진우가", "spectrum"), "국민의힘 주진우 의원이")
        self.assertEqual(remove_banned_words("갈등 구조가 심화"), "갈등 양상이 심화")
        self.assertEqual(remove_banned_words("인명 구조대"), "인명 구조대")

    def test_conc_label(self):
        c = {"top_ch": "A", "top_share": 72.7, "top2_vid_share": 99.5, "flagged": True}
        self.assertNotIn("99.5", main.format_concentration_ai(c))


if __name__ == "__main__":
    unittest.main()
