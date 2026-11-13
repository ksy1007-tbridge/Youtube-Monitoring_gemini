"""11/09 '여론조사 조작 공방' 오답 회귀 테스트 (오프라인: 네트워크 차단, 실제 API 호출 없음).

오답: 강진구 기자 우산 영상(뉴탐사, youtu.be/njAjuVJLD0Q)이 인물명 단독 키워드로 이 이슈에 묶였고,
설명란 광고 줄 '◼️ 회원가입 번호 : 1877-2270 1번 문자나 음성 제보 가능'이 정의로 뽑혔다.
"""
import os
import socket
import sys
import unittest
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import issue_defs as m  # noqa: E402
import main  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "newtamsa_njAjuVJLD0Q_desc.txt")
with open(FIX, encoding="utf-8") as f:
    NEWTAMSA_DESC = f.read()

ISSUE = "여론조사 조작 공방"
T_UMBRELLA = '【261108】[숏탐사] "취재 중 우산에 옆구리를 세 번 찔렸다“ 강진구 기자, 정청래 처남 취재 현장 상황 전해'
T_YEORON = ('【261108】[숏탐사] "공익제보자를 주동자로 몰아 가족과 함께 송치“ 전북 여론조작 167명 송치, '
            '대포폰 브로커와 당선자는 다 빠져')
V_UMBRELLA = {"채널명": "뉴탐사 NewTamsa", "제목": T_UMBRELLA, "조회수": 6062,
              "링크": "https://youtu.be/njAjuVJLD0Q", "프레임": "당내/인물", "설명": NEWTAMSA_DESC}
V_YEORON = {"채널명": "뉴탐사 NewTamsa", "제목": T_YEORON, "조회수": 3336,
            "링크": "https://youtu.be/nsRDA8d44AQ", "프레임": "기타", "설명": NEWTAMSA_DESC}
NEWS = {"channel": "연합뉴스", "title": "전북 지방선거 경선 여론조사 조작 167명 송치",
        "url": "https://www.yna.co.kr/view/AKR20261105054451055",
        "text": "전북경찰청이 민주당 경선 여론조사에서 허위 응답한 167명을 송치했다."}


def _no_network(*a, **k):
    raise AssertionError("네트워크 호출 금지(오프라인 테스트)")


class _Offline(unittest.TestCase):
    def setUp(self):
        patches = [mock.patch.object(socket.socket, "connect", _no_network),
                   mock.patch.object(socket, "create_connection", _no_network),
                   mock.patch.dict(os.environ, {"YOUTUBE_API_KEY": "", "CF_API_TOKEN": ""})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class Boilerplate(_Offline):
    def test_newtamsa_footer_gives_no_definition(self):
        self.assertIsNone(m.summarize_one_liner(NEWTAMSA_DESC, issue_name=ISSUE))
        self.assertIsNone(m.summarize_one_liner(NEWTAMSA_DESC))

    def test_phone_4_4_and_signup_lines_are_junk(self):
        self.assertTrue(m._is_junk_line("회원가입 번호 : 1877-2270 1번 문자나 음성 제보 가능"))
        self.assertTrue(m._is_junk_line("대표전화 1588-1234 로 연락 주세요 감사합니다"))
        self.assertTrue(m._is_junk_line("제보 팩스 번호 : 02-6011-1118"))
        self.assertTrue(m._is_junk_line("진실수호연대 페이팔 안내드립니다 많은 참여"))
        self.assertTrue(m._is_junk_line("6i52Nt02jzKIJvHt8UWlA/viewform"))
        self.assertTrue(m._is_junk_line("AICOMPANY@3VH2DL 문의 바랍니다"))

    def test_real_content_not_junk(self):
        self.assertFalse(m._is_junk_line("공익제보자를 주동자로 몰아 가족과 함께 송치했다는 주장"))
        self.assertFalse(m._is_junk_line("1987-2024 민주화 이후 여론조사 제도의 변화"))

    def test_footer_block_removed_keeps_content(self):
        text = ("전북경찰이 경선 여론조사 허위 응답 167명을 송치하자 제보자가 반발했다.\n"
                "◼️ 뉴탐사 제보하기: https://tipoff.newtamsa.org\n"
                "◼️ 진실수호연대 안내 페이지 참고\n"
                "◼️ 6.3지방선거 호남백서 '불의 고리' 출간 소식\n")
        line = m.summarize_one_liner(text, issue_name=ISSUE)
        self.assertIsNotNone(line)
        self.assertIn("여론조사", line)
        self.assertNotIn("◼", line)
        self.assertNotIn("불의 고리", line)
        self.assertEqual(m._drop_footer_blocks(text.splitlines()), [text.splitlines()[0]])

    def test_bullet_vs16_stripped(self):
        line = m.summarize_one_liner("◼️ 조희대 대법원장 “사법부 독립 훼손 없다” 발언 파장", issue_name="조희대/사법부")
        self.assertTrue(line.startswith("조희대"))

    def test_no_related_prefix(self):
        line = m.summarize_one_liner('"임기 채우지 못할 수도 있다"는 발언이 나왔다', issue_name="유시민 논란")
        self.assertIsNotNone(line)
        self.assertNotIn("관련", line)


class BuildDefs(_Offline):
    def test_umbrella_video_desc_falls_to_news(self):
        d = m.build_defs([{"이슈명": ISSUE}], {ISSUE: [V_UMBRELLA]}, source="yeogwon",
                         caption=lambda v: None, news=lambda name: NEWS,
                         aliases={ISSUE: ["여론조사 조작", "여론조작", "여조 조작", "허위 응답"]})
        self.assertEqual(len(d["issues"]), 1)
        it = d["issues"][0]
        self.assertEqual(it["evidence_kind"], "news")
        self.assertNotIn("1877", it["definition_one_liner"])
        self.assertNotIn("회원가입", it["definition_one_liner"])

    def test_umbrella_video_no_news_gives_none(self):
        d = m.build_defs([{"이슈명": ISSUE}], {ISSUE: [V_UMBRELLA]}, source="yeogwon",
                         caption=lambda v: None, news=lambda name: None,
                         aliases={ISSUE: ["여론조사 조작", "여론조작", "여조 조작", "허위 응답"]})
        self.assertEqual(d["issues"], [])

    def test_title_with_keyword_preferred_as_evidence(self):
        vids = [dict(V_UMBRELLA, 설명="강진구 기자가 현장에서 피습 상황을 전했다는 주장"),
                dict(V_YEORON, 설명="전북 여론조작 167명 송치 뒤 제보자가 배후는 빠졌다고 반발했다")]
        d = m.build_defs([{"이슈명": ISSUE}], {ISSUE: vids}, source="yeogwon",
                         caption=lambda v: None, news=lambda name: None,
                         aliases={ISSUE: ["여론조사 조작", "여론조작", "여조 조작", "허위 응답"]})
        it = d["issues"][0]
        self.assertEqual(it["evidence_url"], "https://youtu.be/nsRDA8d44AQ")
        self.assertIn("여론조작", it["definition_one_liner"])

    def test_normal_issue_still_defined(self):
        v = {"채널명": "MBC 라디오 시사", "제목": "조희대 대법원장 청문 공방", "조회수": 50000,
             "링크": "https://youtu.be/abcdefg1234", "프레임": "검찰/사법",
             "설명": "조희대 대법원장 “재판 지연 없다” 발언에 여당이 사퇴 요구\n◼️ 구독 알림 설정\n◼️ 후원 계좌\n◼️ 제보하기: x"}
        d = m.build_defs([{"이슈명": "조희대/사법부"}], {"조희대/사법부": [v]}, source="yeogwon",
                         caption=lambda v: None, news=lambda name: None)
        it = d["issues"][0]
        self.assertEqual(it["evidence_kind"], "description")
        self.assertIn("조희대", it["definition_one_liner"])


def _df(rows):
    df = pd.DataFrame(rows, columns=["채널명", "제목", "조회수"])
    df["제목_정제"] = df["제목"]
    df["프레임"] = "기타"
    return df


class IssueMatching(_Offline):
    def test_yeoron_auto_extract_needs_topic_not_person_alone(self):
        df = _df([("뉴탐사 NewTamsa", T_YEORON, 3336),
                  ("서울의소리 VoiceOfSeoul", "전북 여론조작 송치 파장", 900),
                  ("스픽스", "여론 조작 공방 격화", 800),
                  ("뉴탐사 NewTamsa", T_UMBRELLA, 6062)])
        cands = {"issues": [{"name": "여론조사 조작 공방", "keywords": ["여론조작", "여론 조작", "여론조사"]}], "persons": []}
        dyn, _, _ = main.filter_dynamic_entities(df, cands, None)
        issues, _ = main.extract_major_issues(df, dyn)
        names = issues["이슈명"].tolist() if issues is not None and not issues.empty else []
        assert any("여론" in n or "여론조사 조작 공방" in n for n in names)
        recs = issues.to_dict("records")
        with mock.patch.object(main, "build_defs", side_effect=lambda rows, videos, **k: videos):
            videos = main.build_issue_defs(df, recs, dyn)
        key = next(iter(videos))
        assert T_UMBRELLA not in [v["제목"] for v in videos[key]]

    def test_person_alone_does_not_make_issue(self):
        df = _df([("뉴탐사 NewTamsa", T_UMBRELLA, 6062),
                  ("고발뉴스TV", "이상호 기자 뉴스비평 11월 3일", 1000),
                  ("스픽스", "강진구 기자 현장 스케치", 800)])
        cands = {"issues": [{"name": "여론조사 조작 공방", "keywords": ["강진구", "이상호 기자"]}], "persons": []}
        dyn, _, _ = main.filter_dynamic_entities(df, cands, None)
        # 인물 단독 키워드 → 일반어/인물 단독 제외
        assert not any("여론" in k for k in dyn)




if __name__ == "__main__":
    unittest.main()
