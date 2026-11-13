# -*- coding: utf-8 -*-
"""2026-11-13 실제 사례: 목차 제목 정의 거부, 제목만 보도 거부, 보도 문장 미절단."""
import issue_defs as m

TOC = "7- 김어준 드디어 조밍아웃..합당 촉구 발언 와중에 실언"
LIVE_TITLE = "하이라이트] 최민희 망언, 청와대 발칵..팀김어준 올게왔다..초상집"
LIVE_DESC = f"""{LIVE_TITLE}

1- 이재명 대통령과 김민석 케미, 이 정도?..뉴이재명 요즘 뜨는 유튜버들
2- 딸 결혼식 이후 최대 위기..최민희 잇따르는 피해 호소들
{TOC}
8- 신경질 최욱 안타깝다..무너지는 팀김어준 라인업
"""
ALG_TITLE = "김어준 이 경고한 ‘알고리즘 감옥’…정작 뉴스공장 시청자들은?"
YUSIMIN = "김어준 씨와 유시민 작가가 조국당을 '구명정'으로 거론하는 상황을 비판하며, 대통령을 향한 비판이 조롱으로 나아가고 있다고 주장했다."

LIVE_VIDEO = {
    "채널명": "고발뉴스TV",
    "제목": LIVE_TITLE,
    "링크": "https://youtu.be/d5dceZXvCnE",
    "조회수": 46494,
    "프레임": "기타",
    "설명": LIVE_DESC,
    "라이브": True,
}


def test_toc_line_is_rejected():
    assert m._is_toc_or_title_line(TOC)
    assert m._is_junk_line(TOC)
    assert m._is_weak_one_liner(TOC, "김어준 논란", ["김어준"])
    assert m.summarize_one_liner(LIVE_DESC, issue_name="김어준 논란") is None


def test_live_highlight_without_captions_emits_no_definition():
    line, ev = m._build_one(
        "김어준 논란", [LIVE_VIDEO], m.summarize_one_liner,
        m.fetch_description, lambda v: None, lambda n: None,
        aliases=["김어준"],
    )
    assert line is None and ev is None


def test_live_highlight_uses_captions_when_present():
    cap = "김어준이 민주당과 조국혁신당 합당을 1월까지 하자고 주장하는 가운데 속내가 드러났다는 지적이 나왔다."
    line, ev = m._build_one(
        "김어준 논란", [LIVE_VIDEO], m.summarize_one_liner,
        m.fetch_description, lambda v: cap, lambda n: None,
        aliases=["김어준"],
    )
    assert ev["evidence_kind"] == "caption"
    assert "합당" in line and "김어준" in line
    assert not m._is_toc_or_title_line(line)


def test_spectrum_headline_only_algorithm_prison_rejected():
    items = [{
        "channel": "주간경향",
        "title": ALG_TITLE,
        "url": "https://weekly.khan.co.kr/article/202611131447001",
        "lead": "",
        "ts": 2,
    }]
    assert m._pick_news(items, ["김어준"]) is None
    items[0]["lead"] = "유튜브 추천 시스템이 이용자 소비를 가두는 현상을 분석했다."
    assert m._pick_news(items, ["김어준"]) is None  # 제목에만 김어준
    items[0]["lead"] = "김어준이 알고리즘 감옥을 경고했지만 뉴스공장 시청자 조사에서는 다른 결과가 나왔다."
    assert m._pick_news(items, ["김어준"])["url"].endswith("202611131447001")

    news = lambda n: {"channel": "주간경향", "title": ALG_TITLE,
                      "url": items[0]["url"], "text": ALG_TITLE, "lead": ""}
    line, ev = m._build_one(
        "김어준 논란", [LIVE_VIDEO], m.summarize_one_liner,
        m.fetch_description, lambda v: None, news, aliases=["김어준"],
    )
    assert line is None and ev is None


def test_yusimin_news_sentence_not_truncated():
    line = m.summarize_one_liner(YUSIMIN, issue_name="유시민 논란")
    assert line == YUSIMIN
    assert "…" not in line
    assert line.endswith("다.")
    news = lambda n: {"channel": "뉴데일리",
                      "title": "합당하자면서 '대선 꿈 접으라'는 민주당 … 조국당 \"기만\" 반발",
                      "url": "https://www.newdaily.co.kr/site/data/html/2026/11/13/2026111300225.html",
                      "lead": YUSIMIN, "text": "합당하자면서 '대선 꿈 접으라'는 민주당\n" + YUSIMIN}
    line, ev = m._build_one(
        "유시민 논란", [], m.summarize_one_liner,
        lambda v: None, lambda v: None, news, aliases=["유시민"],
    )
    assert ev["evidence_kind"] == "news"
    assert line == YUSIMIN


def test_fetch_caption_head_without_id_is_none():
    assert m.fetch_caption_head({"제목": "x", "링크": ""}) is None
