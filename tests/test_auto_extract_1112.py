# -*- coding: utf-8 -*-
"""11/12 자동 추출 전환·Q&A 정의 회귀 (오프라인)."""
import os, sys, unittest
from unittest import mock
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import issue_defs as d
import issue_aliases as al
import main


class AliasLabel(unittest.TestCase):
    def test_seongnam_alone_not_ganghoonsik(self):
        self.assertEqual(al.label_issue("이용철 검증", ["이용철", "성남라인"]), "이용철 검증")
        self.assertEqual(al.label_issue("성남라인 의혹", ["성남라인"]), "성남라인 의혹")

    def test_ganghoonsik_bundle_gets_display(self):
        self.assertEqual(al.label_issue("강훈식 공천", ["강훈식"]), "강훈식·성남라인")

    def test_kimeojun_and_yeoron_labels(self):
        self.assertEqual(al.label_issue("김어준 열폭", ["김어준"]), "김어준 논란")
        self.assertEqual(al.label_issue("여론조작 송치", ["여론조작"]), "여론조사 조작 공방")

    def test_label_does_not_steal_unrelated(self):
        self.assertEqual(al.label_issue("김민석 신천지", ["김민석", "신천지"]), "김민석 신천지")
        self.assertEqual(al.label_issue("조작기소", ["조작기소"]), "조작기소")
        self.assertEqual(al.label_issue("조희대 탄핵", ["조희대", "탄핵"]), "조희대/사법부")


class QaDefs1112(unittest.TestCase):
    def test_ellipsis_not_sentence_end(self):
        text = '이용철 "공소취소, 법·원칙 따라.. 지금은 진상규명 집중"'
        line = d.summarize_one_liner(text, issue_name="이용철 검증")
        self.assertIsNotNone(line)
        self.assertIn("지금은", line)
        self.assertFalse(line.endswith("따라..") and "지금은" not in line)

    def test_yiyongcheol_sentence_not_used_for_ganghoonsik(self):
        text = '이용철 "공소취소, 법·원칙 따라.. 지금은 진상규명 집중"'
        line = d.summarize_one_liner(text, issue_name="강훈식·성남라인")
        # 핵심어(강훈식) 없음 → 약하면 채택되어도 _is_weak가 거부
        if line:
            self.assertTrue(d._is_weak_one_liner(line, "강훈식·성남라인", ["강훈식"]))

    def test_title_copy_skipped_for_kimeojun(self):
        title = "위기의 팀김어준 열폭중..이재명 대통령 감동 행보 SNS 강타..이상호의 뉴스비평 2026년 11월 10일 1,530회"
        self.assertTrue(d._is_title_copy(title, title))
        self.assertTrue(d._is_title_copy(title[:59] + "…", title))
        v = {"채널명": "고발뉴스TV", "제목": title, "조회수": 68469,
             "링크": "https://youtu.be/0sHmF0H6nFg", "프레임": "당내/인물", "설명": title + "\n후원 계좌 안내"}
        out = d.build_defs([{"이슈명": "김어준 논란"}], {"김어준 논란": [v]}, source="yeogwon",
                           caption=lambda x: None, news=lambda n: None, aliases={"김어준 논란": ["김어준"]})
        # 제목 복붙 설명란은 거부. 제목 폴백이면 evidence_kind는 title
        if out["issues"]:
            self.assertNotEqual(out["issues"][0]["evidence_kind"], "description")
            if out["issues"][0]["evidence_kind"] == "title":
                self.assertNotEqual(out["issues"][0]["definition_one_liner"].rstrip("…"), title[:60].rstrip())

    def test_news_requires_core_in_title(self):
        news = {"channel": "서울신문",
                "title": "한덕수 석방 가능성에…與 내부서 고개드는 ‘조희대 책임론’",
                "url": "https://www.seoul.co.kr/x",
                "text": "이성윤 최고위원은 유튜브 ‘김어준의 겸손은 힘들다 뉴스공장’에서 조 대법원장 탄핵 문제를 두고 말했다."}
        # 제목에 김어준 없음 → 보도 거절
        out = d.build_defs([{"이슈명": "김어준 논란"}], {"김어준 논란": []}, source="spectrum",
                           caption=lambda x: None, news=lambda n: news, aliases={"김어준 논란": ["김어준"]})
        self.assertEqual(out["issues"], [])
        # 제목에 핵심어가 있으면 채택
        ok = dict(news, title="김어준 방송 발언 논란", text="김어준 방송 발언을 두고 비판이 이어졌다.")
        out2 = d.build_defs([{"이슈명": "김어준 논란"}], {"김어준 논란": []}, source="spectrum",
                            caption=lambda x: None, news=lambda n: ok, aliases={"김어준 논란": ["김어준"]})
        self.assertEqual(len(out2["issues"]), 1)
        self.assertEqual(out2["issues"][0]["evidence_kind"], "news")

    def test_numbered_live_is_omnibus(self):
        title = "[심층분석 LIVE] - 1.김민석의 다음 스탭은… 2.경기동부연합 성남라인 이재명의 변호인들 지금 어디야…"
        self.assertTrue(d._is_omnibus_for(title, "강훈식·성남라인"))

    def test_pick_news_title_only(self):
        items = [
            {"title": "한덕수 석방 가능성에 조희대 책임론", "lead": "김어준의 겸손은 힘들다 뉴스공장에서 발언", "ts": 2},
            {"title": "김어준 열폭 논란", "lead": "", "ts": 1},
        ]
        picked = d._pick_news(items, ["김어준"])
        self.assertEqual(picked["title"], "김어준 열폭 논란")


if __name__ == "__main__":
    unittest.main()


def test_junk_tokens_not_issues():
    from issue_auto import is_junk_token, is_junk_issue_name, extract_offline_candidates
    for tok in ["LIVE", "live", "261111", "261106", "11월", "11일", "2026년", "이유", "없다", "shorts"]:
        assert is_junk_token(tok), tok
        assert is_junk_issue_name(tok), tok
    assert not is_junk_issue_name("신천지 논란")
    assert not is_junk_issue_name("한덕수 석방")
    titles = [
        "LIVE 특집 1", "LIVE 특집 2", "LIVE 특집 3",
        "261111 아침뉴스", "261111 저녁뉴스", "261111 뉴스룸",
        "신천지 명단 공개", "신천지 의혹 재점화", "신천지 수습 논란",
    ]
    out = extract_offline_candidates(titles)
    names = [i["name"] for i in out.get("issues", [])]
    assert not any(is_junk_issue_name(n) for n in names)
    assert any("신천지" in n for n in names)

