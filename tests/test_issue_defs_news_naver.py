import os
import pytest
import issue_defs as d

HTML = """<div class="x"><a class="Ab12" href="https://media.naver.com/press/001" data-heatmap-target=".prof"><span>가신문</span><span>새 창 열림</span></a>
<a class="zz" href="https://a.kr/1" data-heatmap-target=".tit"><span>여야 예산안 공방</span></a>
<a class="zz" href="https://a.kr/1" data-heatmap-target=".body"><span>예산안 처리를 두고 여야가 맞섰다.</span></a>
<a href="https://n.news.naver.com/mnews/article/001/1" data-heatmap-target=".nav">네이버뉴스</a></div>
<div><a href="https://b.kr/" data-heatmap-target=".prof"></a><a href="https://b.kr/" data-heatmap-target=".prof">나일보새 창 열림</a>
<a href="https://b.kr/news?id=2&amp;r=1" data-heatmap-target=".tit">정치권 &quot;파장&quot;</a>
<a href="https://b.kr/news?id=2&amp;r=1" data-heatmap-target=".body"><mark>유시민</mark> 전 이사장 발언이 논쟁으로 번졌다.</a></div>"""


class R:
    def __init__(self, text):
        self.text, self.status_code = text, 200
    def raise_for_status(self):
        pass


def test_parse_naver():
    items = d.parse_naver_news(HTML)
    assert [i["channel"] for i in items] == ["가신문", "나일보"]
    assert items[1]["url"] == "https://b.kr/news?id=2&r=1"
    assert items[1]["title"] == '정치권 "파장"'
    assert items[1]["lead"].startswith("유시민 전 이사장")
    assert items[0]["ts"] > items[1]["ts"]


def test_relevance_from_lead_and_latest_fallback(monkeypatch):
    monkeypatch.delenv("ISSUE_DEFS_NEWS_DISABLE", raising=False)
    calls = []
    def get(url, params=None, **kw):
        calls.append(url)
        return R(HTML)
    monkeypatch.setattr(d.requests, "get", get)
    n = d.fetch_news("유시민 논란")
    assert n["url"] == "https://b.kr/news?id=2&r=1" and n["channel"] == "나일보"
    assert n["text"].startswith('정치권 "파장"\n유시민')
    assert d.fetch_news("없는사람 의혹") is None  # 제목·리드 핵심어 없으면 엉뚱한 기사 대신 None
    assert "naver" in calls[0]  # 네이버 무관 기사뿐이면 Google 다음 후보로 넘어감


def test_disable(monkeypatch):
    monkeypatch.setenv("ISSUE_DEFS_NEWS_DISABLE", "1")
    monkeypatch.setattr(d.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert d.fetch_news("유시민 논란") is None


def test_naver_error_falls_back_google_no_raise(monkeypatch):
    monkeypatch.delenv("ISSUE_DEFS_NEWS_DISABLE", raising=False)
    def get(url, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(d.requests, "get", get)
    assert d.fetch_news("유시민 논란") is None


def test_build_one_uses_news(monkeypatch):
    monkeypatch.delenv("ISSUE_DEFS_NEWS_DISABLE", raising=False)
    monkeypatch.setattr(d.requests, "get", lambda *a, **k: R(HTML))
    defs = d.build_defs([{"이슈명": "유시민 논란"}], {"유시민 논란": []},
                        describe=lambda v: None, caption=lambda v: None)
    s = str(defs)
    assert "https://b.kr/news?id=2&r=1" in s and "news.google" not in s


@pytest.mark.skipif(os.environ.get("ISSUE_DEFS_LIVE") != "1", reason="live")
def test_live_yusimin(monkeypatch):
    monkeypatch.delenv("ISSUE_DEFS_NEWS_DISABLE", raising=False)
    n = d.fetch_news("유시민 논란")
    print(n)
    assert n and n["url"].startswith("http") and "news.google" not in n["url"]
