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


def test_build_one_falls_to_title_when_desc_junk():
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
    assert line
    assert "김어준" in line
    assert meta["evidence_kind"] == "description"


MBC_DESC = """※ 〈김종배의 시선집중〉 방송 화면 인용 시, 출처를 잘 보이는 크기의 자막으로 다음과 같이 명시해주세요.
※ 'MBC 라디오 〈김종배의 시선집중〉'
※ 인터뷰 전문은 홈페이지에서 확인할 수 있습니다.

(출연) 신장식 조국혁신당 대표

#시선집중 #유시민 #신장식 #MBC
"""
MBC_TITLE = "[시선집중] 유시민의 '파브르 곤충기' 혁신당 입장은...민주와 '합당론' 진척 상황은 - 신장식 조국혁신당 대표, MBC 261103 방송"


def test_summarize_mbc_attribution_only_is_none():
    assert m.summarize_one_liner(MBC_DESC, issue_name="유시민 논란") is None


def test_build_one_mbc_falls_to_title():
    videos = [{"채널명": "MBC 라디오 시사", "제목": MBC_TITLE,
               "링크": "https://youtu.be/u4G42iyKF2Y", "조회수": 50000, "프레임": "기타"}]
    line, meta = m._build_one(
        "유시민 논란", videos, m.summarize_one_liner,
        lambda v: MBC_DESC, lambda v: None, lambda q: None)
    assert line
    assert "출처" not in line and "자막" not in line
    assert "유시민" in line
    assert "파브르" in line or "합당론" in line


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
