import issue_defs as m


def test_summarize_skips_ars_and_broadcast_header():
    text = """MBC RADIO 시사 정치인싸 261101 방송

인싸를 꿈꾸는 네 사람의 유쾌한 시사 토크쇼!

대통령의 SNS, 점점 거칠어진다
유시민의 시선은 어디에 가 있나
"임기 채우지 못할 수도 있다"
"""
    line = m.summarize_one_liner(text, issue_name="유시민 논란")
    assert line
    assert "261101" not in line
    assert "ARS" not in line
    assert "RADIO" not in line


def test_summarize_rejects_pure_sponsor():
    assert m.summarize_one_liner("[고발뉴스 ARS 후원] 1877-0786") is None
    assert m.summarize_one_liner("*후원계좌 안내 : 기업은행 221-456011-04-028") is None


def test_title_fallback_clickbait():
    line = m._title_one_liner("제사장의 몰락..김어준 실화임?", "김어준 논란")
    assert line
    assert "김어준" in line
    assert "실화임?" not in line  # strip trailing ?


def test_build_one_no_title_fallback_when_desc_junk():
    videos = [{
        "채널명": "고발뉴스TV",
        "제목": "제사장의 몰락..김어준 실화임?",
        "링크": "https://youtu.be/tpte96vNPn4",
        "조회수": 14325,
        "프레임": "기타",
    }]
    line, meta = m._build_one(
        "김어준 논란",
        videos,
        m.summarize_one_liner,
        describe=lambda v: "[고발뉴스 ARS 후원] 1877-0786",
        caption=lambda v: None,
        news=lambda n: None,
    )
    assert line is None and meta is None


MBC_DESC = """※ 〈김종배의 시선집중〉 방송 화면 인용 시, 출처를 잘 보이는 크기의 자막으로 다음과 같이 명시해주세요.
※ 'MBC 라디오 〈김종배의 시선집중〉'
※ 인터뷰 전문은 홈페이지에서 확인할 수 있습니다.

(출연) 신장식 조국혁신당 대표

#시선집중 #유시민 #신장식 #MBC
"""
MBC_TITLE = "[시선집중] 유시민의 '파브르 곤충기' 혁신당 입장은...민주와 '합당론' 진척 상황은 - 신장식 조국혁신당 대표, MBC 261103 방송"


def test_summarize_mbc_attribution_only_is_none():
    assert m.summarize_one_liner(MBC_DESC, issue_name="유시민 논란") is None


def test_build_one_mbc_no_title_fallback():
    videos = [{"채널명": "MBC 라디오 시사", "제목": MBC_TITLE,
               "링크": "https://youtu.be/u4G42iyKF2Y", "조회수": 50000, "프레임": "기타"}]
    line, meta = m._build_one(
        "유시민 논란", videos, m.summarize_one_liner,
        lambda v: MBC_DESC, lambda v: None, lambda q: None)
    assert line is None and meta is None


def test_skip_misc_junk_lines():
    for s in [
        "이 영상은 공정사용(fair use) 원칙에 따라 제작되었습니다.",
        "제품 링크 및 광고 포함 영상입니다",
        "광고 가능 여부와 상관없이 제작한 콘텐츠입니다",
        "(출연) 김아무개 평론가, 이아무개 기자",
        "[4:10] 유시민 발언 논란 본격 시작",
        "12:00 합당론 이야기 시작",
        "※ 무단 전재 및 재배포 금지",
        "도서구입: 교보문고에서 판매중입니다",
    ]:
        assert m.summarize_one_liner(s, issue_name="유시민 논란") is None, s


def test_body_line_not_overkilled():
    line = m.summarize_one_liner("유시민 작가가 '파브르 곤충기' 비유로 합당론을 비판했다.", issue_name="유시민 논란")
    assert line and "유시민" in line


# ----- 2026-11-04 잡음 강화 -----
import issue_defs as _d

AD = "AI가 조건에 맞는 최적의 장소를 찾아 무료로 견적 비교부터 예약까지 해결해 드립니다"
FLOWER = "따뜻한 진심을 전하는 전국구 꽃배달 꽃반장"
COPY = "Copyrightⓒ ○○TV 무단사용을 금지합니다"


def test_ad_flower_copyright_are_junk():
    for s in (AD, FLOWER, COPY, "All rights reserved."):
        assert _d._is_junk_line(s)
        assert _d._is_weak_one_liner(s, "김어준")
    assert _d.summarize_one_liner("\n".join([AD, FLOWER, COPY]), issue_name="김어준") is None


def test_broadcast_header_is_weak():
    assert _d._is_weak_one_liner("유시민 관련 MBC 라디오 〈김종배의 시선집중〉 261103(화) 방송", "유시민")
    assert _d._is_weak_one_liner("유시민 관련 MBC 라디오 〈김종배의 시선집중〉", "유시민 논란")
    assert not _d._is_weak_one_liner("유시민 전 이사장, 합당 반대파를 공개 비판하며 파장", "유시민")


def test_build_one_ad_desc_falls_to_news():
    v = {"채널명": "c", "제목": "김어준 발언 정리", "조회수": 10, "링크": "https://y/1", "프레임": "x"}
    news = lambda n: {"channel": "○○일보", "title": "김어준, 방송 발언 논란", "url": "https://n/1",
                      "text": "김어준씨의 방송 발언이 정치권 비판을 불렀다."}
    line, ev = _d._build_one("김어준", [v], _d.summarize_one_liner,
                             lambda v: AD + "\n" + FLOWER, lambda v: None, news)
    assert ev["evidence_kind"] == "news" and "발언" in line


def test_build_one_weak_mock_summarize_rejected():
    v = {"채널명": "c", "제목": "t", "조회수": 1, "링크": "https://y/1"}
    lines = {"d": "유시민 관련 MBC 라디오 〈김종배의 시선집중〉 261103(화) 방송"}
    summ = lambda raw, issue_name=None: raw
    news = lambda n: {"channel": "c", "title": "유시민 전 이사장 발언 파장", "url": "u", "text": "유시민 전 이사장 발언이 합당 논쟁으로 번졌다"}
    line, ev = _d._build_one("유시민", [v], summ, lambda v: lines["d"], lambda v: None, news)
    assert ev["evidence_kind"] == "news"


def test_omnibus_title_detected():
    assert _d._is_omnibus_for("합당 내홍 | 신장식 | 유시민 발언 | 예산", "유시민")
    assert not _d._is_omnibus_for("유시민 발언 파장", "유시민")


def test_news_picker_rejects_unrelated():
    items = [{"channel": "조선비즈", "title": "내년 예산안 국회 통과 전망", "url": "u1",
              "lead": "기재부는 총지출을 늘리기로 했다", "ts": 2}]
    assert _d._pick_news(items, ["김어준"]) is None
    items.append({"channel": "b", "title": "김어준 발언 논란", "url": "u2", "lead": "", "ts": 1})
    assert _d._pick_news(items, ["김어준"]) is None  # 제목만 매칭은 거부
    items[-1]["lead"] = "김어준의 방송 발언을 둘러싼 공방이 이어졌다"
    assert _d._pick_news(items, ["김어준"])["url"] == "u2"
