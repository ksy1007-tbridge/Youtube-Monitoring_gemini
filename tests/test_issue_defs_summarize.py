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
