# -*- coding: utf-8 -*-
import os
import pytest
import issue_defs as d

RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>예전 기사 제목입니다 정말로 - 한겨레</title><link>https://news.google.com/rss/articles/OLD</link>
<pubDate>Sun, 01 Nov 2026 01:00:00 GMT</pubDate><description>&lt;a href="x"&gt;예전 기사 제목입니다 정말로&lt;/a&gt;</description>
<source url="https://www.hani.co.kr">한겨레</source></item>
<item><title>유시민 “검찰개혁 늦추면 안 돼” 발언에 여권 내 비판 확산 - 경향신문</title>
<link>https://news.google.com/rss/articles/NEW</link><pubDate>Mon, 02 Nov 2026 23:34:00 GMT</pubDate>
<description>&lt;a href="x"&gt;유시민 “검찰개혁 늦추면 안 돼” 발언에 여권 내 비판 확산&lt;/a&gt;&amp;nbsp;경향신문</description>
<source url="https://www.khan.co.kr">경향신문</source></item>
</channel></rss>"""


def test_parse_news_rss_latest_and_source():
    n = d.parse_news_rss(RSS)
    assert n["channel"] == "경향신문"
    assert n["url"].endswith("/NEW")
    assert n["title"].startswith("유시민") and not n["title"].endswith("경향신문")
    assert "유시민" in n["text"]


def test_parse_news_rss_empty_or_bad():
    assert d.parse_news_rss("<rss><channel></channel></rss>") is None
    assert d.parse_news_rss("not xml") is None


def test_news_query_strips_suffix():
    assert d._news_query("유시민 논란") == "유시민"
    assert d._news_query("검찰개혁 이슈 (자동)") == "검찰개혁"


def test_fetch_news_failure_returns_none(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("timeout key=SECRET")
    monkeypatch.setattr(d.requests, "get", boom)
    assert d.fetch_news("유시민 논란") is None


def test_build_one_uses_news_when_description_is_junk():
    vids = [{"채널명": "MBC 시선집중", "제목": "[시선집중] 인터뷰", "조회수": 100, "링크": "https://youtu.be/abcdefg",
             "설명": "※ 이 영상의 출처는 MBC 라디오입니다.\n※ 화면 인용 시 출처를 다음과 같이 명시해주세요"}]
    news = lambda name: d.parse_news_rss(RSS)
    line, ev = d._build_one("유시민 논란", vids, d.summarize_one_liner,
                            d.fetch_description, lambda v: None, news)
    assert ev["evidence_kind"] == "news"
    assert ev["evidence_channel"] == "경향신문"
    assert line and "유시민" in line and len(line) <= d.ONE_LINER_MAX


@pytest.mark.skipif(os.environ.get("ISSUE_DEFS_LIVE") != "1", reason="live network test")
def test_fetch_news_live():
    n = d.fetch_news("유시민 논란")
    assert n is None or n["url"].startswith("http")
