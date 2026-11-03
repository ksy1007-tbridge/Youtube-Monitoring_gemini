import base64
import issue_defs as d

RSS = """<rss><channel>
<item><title>여야 예산안 공방 - 가신문</title><link>https://news.google.com/rss/articles/AAA</link>
<pubDate>Tue, 03 Nov 2026 09:00:00 GMT</pubDate><source url="https://a.kr">가신문</source></item>
<item><title>유시민 발언 파장 - 나일보</title><link>https://news.google.com/rss/articles/BBB</link>
<pubDate>Tue, 03 Nov 2026 07:00:00 GMT</pubDate><source url="https://b.kr">나일보</source></item>
</channel></rss>"""


class R:
    def __init__(self, text="", url="", status=200):
        self.text, self.url, self.status_code = text, url, status
    def raise_for_status(self):
        pass


def test_keyword_match_preferred():
    n = d.parse_news_rss(RSS, "유시민 논란")
    assert n["title"] == "유시민 발언 파장" and n["channel"] == "나일보"


def test_no_keyword_match_falls_back_latest():
    assert d.parse_news_rss(RSS, "없는사람 논란")["title"] == "여야 예산안 공방"


def test_decode_google_url():
    seg = base64.urlsafe_b64encode(b"\x08\x13\x22\x1chttps://b.kr/news/123.html\xd2\x01\x00").decode().rstrip("=")
    assert d._decode_google_news_url("https://news.google.com/rss/articles/" + seg + "?oc=5") == "https://b.kr/news/123.html"
    assert d._decode_google_news_url("https://news.google.com/x?url=https://c.kr/1") == "https://c.kr/1"
    assert d._decode_google_news_url("https://b.kr/a") == "https://b.kr/a"


def test_og_description_to_text(monkeypatch):
    html = '<html><meta property="og:description" content="유시민 전 이사장의 방송 발언이 정치권 논쟁으로 번졌다."></html>'
    def get(url, params=None, **kw):
        if params:
            return R(RSS)
        return R(html, url="https://b.kr/news/123.html")
    monkeypatch.setattr(d.requests, "get", get)
    monkeypatch.delenv("ISSUE_DEFS_NEWS_DISABLE", raising=False)
    n = d.fetch_news("유시민 논란")
    assert n["url"] == "https://b.kr/news/123.html"
    assert n["text"] == "유시민 발언 파장\n유시민 전 이사장의 방송 발언이 정치권 논쟁으로 번졌다."


def test_page_failures_safe_fallback(monkeypatch):
    def get(url, params=None, **kw):
        if params:
            return R(RSS)
        raise RuntimeError("timeout")
    monkeypatch.setattr(d.requests, "get", get)
    monkeypatch.delenv("ISSUE_DEFS_NEWS_DISABLE", raising=False)
    n = d.fetch_news("유시민 논란")
    assert n["title"] == "유시민 발언 파장" and n["text"] == "유시민 발언 파장"
    assert n["url"].startswith("https://news.google.com/")


def test_disable_env(monkeypatch):
    monkeypatch.setenv("ISSUE_DEFS_NEWS_DISABLE", "1")
    assert d.fetch_news("유시민 논란") is None
