# -*- coding: utf-8 -*-
"""[Phase A] 텔레그램 「○○ 논란이 뭐야?」 Q&A용 이슈 한 줄 정의 생성·KV 게시.

설계: telegram_issue_qa_design.md §1·§2·§5(A)
- build_defs(issue_rows, videos, source="yeogwon") -> dict
- publish_defs(defs) -> None  (Cloudflare KV REST PUT, 실패/미설정이어도 예외 없음)
근거 순서: 이슈별 조회수 상위 단독 영상 설명란 → 자막 앞부분(~1분, youtube-transcript-api, 키 불필요) → 보도 1건(리드·본문 핵심어 필수).
  제목·설명란 목차 줄만인 정의는 거부. 라이브/하이라이트이고 자막이 없으면 설명란 번호 목차 제목으로 같은 채널→유튜브에서 ~7일 안 본편을 찾아 자막을 쓴다.
  본편·보도 본문 근거가 없으면 정의를 생략한다(제목 폴백 없음).
"""
import os
import re
import json
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
DYNAMIC_TAG = " (자동)"
ONE_LINER_MAX = 60
ONE_LINER_HARD_MAX = 100  # 완전한 문장은 이 안에서는 자르지 않음
KV_TTL_SEC = 7 * 24 * 3600
CF_API_BASE = "https://api.cloudflare.com/client/v4"
# 종합방송(풀버전/OMNIBUS)은 '단독' 영상이 아니므로 근거에서 제외
NON_STANDALONE_FRAMES = {"종합방송"}

_URL_RE = re.compile(r"https?://\S+")
_HASHTAG_RE = re.compile(r"#\S+")
_VIDEO_ID_RE = re.compile(r"(?:youtu\.be/|v=)([A-Za-z0-9_-]{6,})")
_SECRET_RE = re.compile(r"(Bearer\s+)\S+|(key=)[^&\s]+", re.I)


def _redact(value) -> str:
    s = str(value)
    for k in ("CF_API_TOKEN", "YOUTUBE_API_KEY"):
        v = os.environ.get(k)
        if v:
            s = s.replace(v, "***")
    return _SECRET_RE.sub(lambda m: (m.group(1) or m.group(2)) + "***", s)


def strip_auto(name: str) -> str:
    name = str(name or "")
    return name[:-len(DYNAMIC_TAG)] if name.endswith(DYNAMIC_TAG) else name


# ----- [근거 텍스트 조회 인터페이스] -----
def fetch_description(video: dict):
    """영상 설명란. 수집 단계에서 받은 '설명'이 있으면 그대로, 없으면 YOUTUBE_API_KEY로 videos.list 1회."""
    desc = (video or {}).get("설명")
    if desc:
        return desc
    api_key = os.environ.get("YOUTUBE_API_KEY")
    m = _VIDEO_ID_RE.search(str((video or {}).get("링크") or ""))
    if not api_key or not m:
        return None
    try:
        r = requests.get("https://www.googleapis.com/youtube/v3/videos",
                         params={"part": "snippet", "id": m.group(1), "key": api_key}, timeout=10)
        r.raise_for_status()
        items = r.json().get("items") or []
        return (items[0]["snippet"].get("description") or None) if items else None
    except Exception as e:
        print(f"⚠️ [issue_defs] 설명란 조회 실패: {_redact(e)}")
        return None


def _video_id(video: dict):
    m = _VIDEO_ID_RE.search(str((video or {}).get("링크") or (video or {}).get("video_id") or ""))
    return m.group(1) if m else None


def fetch_caption_head(video: dict, seconds: int = 60):
    """자막 앞부분(~1분). youtube-transcript-api(키 불필요). 없거나 실패하면 None.
    TubeAlfred MCP는 GitHub Actions에서 호출할 수 없어 이 경로를 쓴다."""
    vid = _video_id(video)
    if not vid:
        return None
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        print("ℹ️ [issue_defs] youtube-transcript-api 미설치 → 자막 생략")
        return None
    try:
        snippets = None
        langs = ["ko", "ko-KR", "en"]
        if hasattr(YouTubeTranscriptApi, "get_transcript"):
            try:
                snippets = YouTubeTranscriptApi.get_transcript(vid, languages=langs)
            except Exception:
                snippets = YouTubeTranscriptApi.get_transcript(vid)
        else:
            fetched = YouTubeTranscriptApi().fetch(vid, languages=langs)
            snippets = list(fetched)
        parts = []
        for sn in snippets or []:
            if isinstance(sn, dict):
                start, text = float(sn.get("start") or 0), sn.get("text") or ""
            else:
                start, text = float(getattr(sn, "start", 0) or 0), getattr(sn, "text", "") or ""
            if start > seconds:
                break
            t = " ".join(str(text).split())
            if t:
                parts.append(t)
        out = " ".join(parts).strip()
        return out or None
    except Exception as e:
        print(f"ℹ️ [issue_defs] 자막 없음/실패: {_redact(e)}")
        return None


GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
_TAG_RE = re.compile(r"<[^>]+>")
NEWS_CANDIDATES = 6
NEWS_PAGE_TIMEOUT = 4
LEAD_MAX = 200


def _news_query(issue_name: str) -> str:
    base = re.sub(r"\s*(논란|이슈|사건)\s*$", "", strip_auto(issue_name or "")).strip()
    base = re.sub(r"[\[\]()「」『』\"“”‘’]", " ", base)
    return re.sub(r"\s+", " ", base).strip()


def _news_keywords(issue_name: str):
    """핵심어: 논란/이슈/사건 등 일반어 제거 후 2글자 이상 토큰."""
    q = re.sub(r"(논란|이슈|사건|의혹)", " ", _news_query(issue_name))
    return [w for w in q.split() if len(w) >= 2]


def _kw_score(item: dict, kws) -> int:
    """제목·리드(요약)에만 핵심어 매칭. 제목 매칭은 가중 2 (본문 묻힘 매칭은 고려 안 함)."""
    title = item.get("title") or ""
    lead = item.get("lead") or ""
    return sum(2 if k in title else (1 if k in lead else 0) for k in kws)


def parse_news_items(xml_text: str, limit: int = NEWS_CANDIDATES):
    """Google 뉴스 RSS → 최신순 후보 목록 [{"channel","title","url","lead","ts"}]."""
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return []
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src_el = it.find("source")
        channel = (src_el.text or "").strip() if src_el is not None else ""
        if channel and title.endswith(" - " + channel):
            title = title[: -len(" - " + channel)].strip()
        elif not channel and " - " in title:
            title, channel = [p.strip() for p in title.rsplit(" - ", 1)]
        url = (it.findtext("link") or "").strip()
        if not title or not url:
            continue
        desc = _TAG_RE.sub(" ", it.findtext("description") or "")
        desc = re.sub(r"\s+", " ", desc.replace("&nbsp;", " ")).strip()
        # Google RSS description은 관련기사 묶음이라 자기 제목 외 리드가 있을 때만 사용
        lead = "" if (not desc or title in desc) else desc[:LEAD_MAX]
        try:
            ts = parsedate_to_datetime(it.findtext("pubDate") or "").timestamp()
        except Exception:
            ts = 0
        items.append({"channel": channel, "title": title, "url": url, "lead": lead, "ts": ts})
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items[:limit]


def _lead_has_kw(item: dict, kws) -> bool:
    """제목만이 아니라 리드·본문에 핵심어가 있는지."""
    lead = (item or {}).get("lead") or ""
    return any(k in lead for k in (kws or []) if k)


def _pick_news(items, kws):
    """핵심어가 리드·본문에 있는 후보 중, 제목·리드 점수가 가장 높은 것(동점은 최신).
    핵심어가 있는데 리드·본문 매칭이 없으면 None(제목만 매칭 거부). 핵심어 자체가 없을 때만 최신 1건."""
    if not items:
        return None
    if kws:
        eligible = [x for x in items if _lead_has_kw(x, kws)]
        if not eligible:
            return None
        return max(eligible, key=lambda x: (_kw_score(x, kws), x["ts"]))
    return items[0]


def _as_news(item):
    if not item:
        return None
    lead = item.get("lead") or ""
    return {"channel": item["channel"], "title": item["title"], "url": item["url"],
            "text": item["title"] + ("\n" + lead if lead else "")}


def parse_news_rss(xml_text: str, issue_name: str = ""):
    """RSS → 1건 {"channel","title","url","text"} 또는 None (issue_name 주면 핵심어 우선)."""
    return _as_news(_pick_news(parse_news_items(xml_text), _news_keywords(issue_name)))


def _decode_google_news_url(url: str):
    """news.google.com 링크에서 HTTP 없이 원문 URL 추출(url= 파라미터 / 구형 base64 id). 실패 시 None."""
    import base64
    from urllib.parse import urlparse, parse_qs
    try:
        p = urlparse(url)
        if "news.google." not in (p.netloc or ""):
            return url
        qs = parse_qs(p.query)
        for k in ("url", "u", "q"):
            v = (qs.get(k) or [""])[0]
            if v.startswith("http"):
                return v
        seg = p.path.rstrip("/").rsplit("/", 1)[-1]
        raw = base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))
        m = re.search(rb"https?://[\x21-\x7e]+", raw)
        if m:
            cand = m.group(0).decode("ascii", "ignore")
            # protobuf 길이 바이트가 끝에 붙는 경우 정리
            return re.sub(r"[\x00-\x20\"<>]+.*$", "", cand)
    except Exception:
        pass
    return None


_META_RES = [
    re.compile(r'<meta[^>]+(?:property|name)=["\'](?:og:description|description|twitter:description)["\'][^>]*content=["\']([^"\']{10,})["\']', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']{10,})["\'][^>]*(?:property|name)=["\'](?:og:description|description|twitter:description)["\']', re.I),
]
_OG_URL_RE = re.compile(r'<meta[^>]+property=["\']og:url["\'][^>]*content=["\'](https?://[^"\']+)["\']', re.I)


def extract_lead(html_text: str) -> str:
    """og:description / meta description → 앞 200자. 없으면 첫 <p> 덩어리."""
    import html as _html
    s = html_text or ""
    for rx in _META_RES:
        m = rx.search(s)
        if m:
            t = re.sub(r"\s+", " ", _html.unescape(m.group(1))).strip()
            if t:
                return t[:LEAD_MAX]
    for m in re.finditer(r"<p[^>]*>(.*?)</p>", s, re.I | re.S):
        t = re.sub(r"\s+", " ", _html.unescape(_TAG_RE.sub(" ", m.group(1)))).strip()
        if len(t) >= 40:
            return t[:LEAD_MAX]
    return ""


def _enrich(item: dict, timeout: int = NEWS_PAGE_TIMEOUT) -> dict:
    """원문 URL·리드 보강. 실패해도 원 item 유지(예외 없음)."""
    out = dict(item)
    try:
        orig = _decode_google_news_url(out["url"])
        if orig:
            out["url"] = orig
        r = requests.get(out["url"], headers={"User-Agent": "Mozilla/5.0"},
                         timeout=timeout, allow_redirects=True)
        final = getattr(r, "url", "") or ""
        if final.startswith("http") and "news.google." not in final:
            out["url"] = final
        if getattr(r, "status_code", 200) < 400:
            text = r.text or ""
            if "news.google." in out["url"]:
                m = _OG_URL_RE.search(text)
                if m and "news.google." not in m.group(1):
                    out["url"] = m.group(1)
            else:
                lead = extract_lead(text)
                if lead and lead != out["title"]:
                    out["lead"] = lead
    except Exception as e:
        print(f"⚠️ [issue_defs] 원문 보강 생략: {_redact(e)}")
    return out


NAVER_NEWS_SEARCH = "https://search.naver.com/search.naver"
_HM_A_RE = re.compile(r'<a\b([^>]*?)data-heatmap-target="\.(tit|body|prof)"([^>]*)>(.*?)</a>', re.S)
_HREF_RE = re.compile(r'href="([^"]+)"')


def _clean_html_text(s: str) -> str:
    import html as _html
    t = _html.unescape(_TAG_RE.sub(" ", s or ""))
    t = t.replace("새 창 열림", " ")
    return re.sub(r"\s+", " ", t).strip()


def parse_naver_news(html_text: str, limit: int = NEWS_CANDIDATES):
    """네이버 뉴스 검색(where=news, 최신순) HTML → 후보 [{"channel","title","url","lead","ts"}].
    난독화 클래스 대신 의미 속성 data-heatmap-target(.prof 언론사 / .tit 제목 / .body 요약)에 의존.
    url은 .tit의 href = 언론사 원문. ts는 문서 순서(최신순 정렬 결과)를 역순 점수로."""
    items, press, cur = [], "", None
    try:
        for m in _HM_A_RE.finditer(html_text or ""):
            attrs = m.group(1) + m.group(3)
            kind = m.group(2)
            hm = _HREF_RE.search(attrs)
            href = (hm.group(1) if hm else "").replace("&amp;", "&")
            text = _clean_html_text(m.group(4))
            if kind == "prof":
                if text:
                    press = text
            elif kind == "tit":
                if text and href.startswith("http"):
                    cur = {"channel": press, "title": text, "url": href, "lead": "", "ts": 0}
                    items.append(cur)
            elif kind == "body" and cur is not None and not cur["lead"] and href == cur["url"]:
                cur["lead"] = text[:LEAD_MAX]
            if len(items) > limit:
                break
    except Exception:
        return []
    items = items[:limit]
    n = len(items)
    for i, it in enumerate(items):
        it["ts"] = n - i
    return items


def _fetch_naver_items(q: str, timeout: int):
    r = requests.get(NAVER_NEWS_SEARCH,
                     params={"where": "news", "query": q, "sort": "1", "pd": "1"},  # 최신순·1주
                     headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ko-KR,ko;q=0.9"},
                     timeout=timeout)
    r.raise_for_status()
    return parse_naver_news(r.text)


def _fetch_google_items(q: str, timeout: int):
    r = requests.get(GOOGLE_NEWS_RSS,
                     params={"q": f"{q} when:7d", "hl": "ko", "gl": "KR", "ceid": "KR:ko"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
    r.raise_for_status()
    return parse_news_items(r.text)


def fetch_news(issue_name: str, timeout: int = 8):
    """보도 1건 폴백 → {"channel","title","url","text"} 또는 None.
    1순위 네이버 뉴스 검색(최신순·1주, 키 불필요) — 원문 URL·언론사·요약이 바로 나옴.
    후보 중 핵심어(논란/이슈/사건/의혹 제거)가 리드·본문에 든 것 우선. 제목만 맞으면 쓰지 않는다.
    네이버 실패·0건이면 최후 폴백으로 Google 뉴스 RSS(+원문 디코드/리드 보강).
    (Daum 뉴스 검색은 비브라우저 요청을 차단해 미사용.) ISSUE_DEFS_NEWS_DISABLE=1이면 None. 예외 없음."""
    q = _news_query(issue_name)
    if not q or os.environ.get("ISSUE_DEFS_NEWS_DISABLE") == "1":
        return None
    kws = _news_keywords(issue_name)
    try:
        items = _fetch_naver_items(q, timeout)
        picked = _pick_news(items, kws) if items else None
        if picked:
            return _as_news(picked)
    except Exception as e:
        print(f"⚠️ [issue_defs] 네이버 뉴스 조회 실패: {_redact(e)}")
    try:
        items = _fetch_google_items(q, timeout)
        if not items:
            return None
        ranked = sorted(items, key=lambda x: (_kw_score(x, kws), x["ts"]), reverse=True)
        # 원문 리드를 보강한 뒤, 리드·본문 핵심어가 있는 것만 채택(제목만 매칭 거부)
        enriched = [_enrich(x) for x in ranked[:3]]
        return _as_news(_pick_news(enriched, kws))
    except Exception as e:
        print(f"⚠️ [issue_defs] 보도 조회 실패: {_redact(e)}")
        return None


_JUNK_LINE_RE = re.compile(
    r"(ARS|후원|광고|계좌|멤버십|구독\s*자|좋아요|알림\s*설정|홈페이지|"
    r"bit\.ly|카톡|텔레그램|출연\s*[:：]|\(\s*출연\s*\)|진행\s*[:：]|문의\s*[:：]|메일|email|"
    r"RADIO\s+\S+\s+\d{4,}\s*방송|\d{2,4}-\d{3,4}-\d{4}|"
    # 대표번호(1588·1644·1877 등 4-4)·지역/휴대 번호 2토막
    r"(?<![\d\-])(?:1[568]\d{2}|0\d{1,2})-\d{3,4}(?![\d\-])|"
    # 회원가입·제보·팩스·결제 안내 / 구글폼·URL 잔여 조각 / 이메일·코드 토큰
    r"회원\s*가입|가입\s*(링크|문의|번호|안내)|제보\s*(하기|번호|가능|전화|메일|창구|팩스|[:：])|팩스|"
    r"페이팔|paypal|구독료|문자로만|docs\.google|viewform|forms?/d/|"
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9]|[A-Za-z0-9_\-]{20,}|"
    # 출처·자막 고지 / 저작권 / 제품 링크 / 도서 / 인터뷰 전문
    r"출처|자막\s*으로\s*명시|화면\s*인용|다음과\s*같이\s*명시|"
    r"공정\s*사용|fair\s*use|저작권|콘텐츠\s*(무단\s*)?(사용|이용)|무단\s*(전재|배포)|"
    r"제품\s*링크|도서\s*구입|책\s*구입|인터뷰\s*전문|"
    # 광고·스팸 (예약/견적 대행, 꽃배달) / 저작권 보일러플레이트
    r"AI\s*가\s*조건에\s*맞는|견적\s*비교|무료로\s*견적|예약\s*까지\s*해결|"
    r"꽃\s*배달|꽃반장|전국구\s*꽃|따뜻한\s*진심을\s*전하는|"
    r"Copyright|ⓒ|©|무단\s*사용을?\s*금지|All\s*rights\s*reserv)",
    re.I,
)

# 채널 고정 푸터 글머리(◼️ 등). 이 글머리로 시작하는 줄이 3개 이상 이어지면 블록 전체를 버린다.
_FOOTER_BULLETS = "◼◾▪"
_BULLET_STRIP = " \t-•▶▷■※*|◼◾▪●◆□\ufe0f"
FOOTER_BLOCK_MIN = 3


def _drop_footer_blocks(lines):
    """◼️/◾/▪ 글머리 줄이 FOOTER_BLOCK_MIN개 이상 연속(사이의 빈 줄·들여쓴 이어진 줄 포함)이면 그 블록을 제거."""
    out, i, n = [], 0, len(lines)

    def is_bullet(x):
        return x.strip()[:1] in _FOOTER_BULLETS if x.strip() else False

    while i < n:
        if not is_bullet(lines[i]):
            out.append(lines[i])
            i += 1
            continue
        j, last, bullets = i, i, 0
        while j < n:
            ln = lines[j]
            if is_bullet(ln):
                bullets += 1
                last = j
            elif ln.strip() and not ln[:1].isspace():
                break
            j += 1
        if bullets >= FOOTER_BLOCK_MIN:
            i = last + 1
        else:
            out.extend(lines[i:last + 1])
            i = last + 1
    return out


# 챕터 타임스탬프 줄: [4:10] …, 12:00 …
_CHAPTER_RE = re.compile(r"^\s*[\[(]?\d{1,2}:\d{2}(:\d{2})?[\])]?(\s|$)")
# 설명란 목차 줄: "7- 김어준 드디어 조밍아웃..", "1) …", "하이라이트] 1. …"
_TOC_ITEM_RE = re.compile(
    r"^\s*(?:[\[【(]?(?:하이라이트|highlight)[\]】):]?\s*)?"
    r"(?:\d{1,2}\s*[-–—.)]|[\[(]\d{1,2}[\])])\s+\S",
    re.I,
)
_LIVE_HIGHLIGHT_RE = re.compile(r"하이라이트|생방송|\bLIVE\b|풀하이라이트", re.I)
# 채널 브랜드·슬로건만 있는 짧은 줄
_SLOGAN_RE = re.compile(r"^(미디어\s*['‘\"“「].{1,20}['’\"”」]|.{0,12}더\s*나은\s*세상)\S*\s*[!.]?$")


def _is_toc_or_title_line(line, title=None) -> bool:
    """번호 목차 줄이거나, 영상 제목만 다시 적은 줄이면 True."""
    s = str(line or "").strip()
    if not s:
        return True
    if _TOC_ITEM_RE.match(s) or re.match(r"^하이라이트\s*[\]:】]", s):
        return True
    if title:
        def norm(x):
            x = re.sub(r"[\s\.·…\-–—\[\]【】()]+", "", str(x or "").lower())
            return x
        nt, ns = norm(title), norm(s)
        if nt and ns and (ns == nt or (len(ns) >= 10 and (ns in nt or nt in ns))):
            return True
    return False


def _is_live_or_highlight(video: dict) -> bool:
    """라이브·하이라이트 묶음 영상. 자막이 없으면 근거로 쓰지 않는다."""
    v = video or {}
    t = str(v.get("제목") or "")
    if _LIVE_HIGHLIGHT_RE.search(t):
        return True
    if v.get("라이브") or v.get("live"):
        return True
    if "길이" in v or "duration" in v or "duration_ms" in v:
        dur = v.get("길이", v.get("duration", v.get("duration_ms")))
        if dur in (0, "0", "00:00", 0.0):
            return True
    return False


CLIP_WINDOW_DAYS = 7
YT_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"


def _clip_core_words(issue_name, aliases=None):
    fn = globals().get("_issue_core_words") or globals().get("_core_words")
    return fn(issue_name, aliases)


def _strip_toc_prefix(line: str) -> str:
    s = str(line or "").strip()
    s = re.sub(
        r"^\s*(?:[\[【(]?(?:하이라이트|highlight)[\]】):]?\s*)?"
        r"(?:\d{1,2}\s*[-–—.)]|[\[(]\d{1,2}[\])])\s+",
        "",
        s,
        flags=re.I,
    )
    return s.strip()


def toc_chapter_titles(text) -> list:
    """설명란에서 번호 목차 제목만 뽑는다."""
    out = []
    for line in str(text or "").splitlines():
        s = line.strip()
        if _TOC_ITEM_RE.match(s):
            t = _strip_toc_prefix(s)
            if t:
                out.append(t)
    return out


def pick_toc_chapter(text, issue_name, aliases=None):
    """이슈 핵심어가 들어 있는 목차 제목. 해당 줄이 없으면 None."""
    titles = toc_chapter_titles(text)
    cores = [c for c in _clip_core_words(issue_name, aliases) if c]
    hits = [t for t in titles if any(c in t for c in cores)]
    return hits[0] if hits else None


def _norm_title(s) -> str:
    return re.sub(r"[\s\.·…\-–—\[\]【】()|ㅣ]+", "", str(s or "").lower())


def titles_match_chapter(chapter, title) -> bool:
    nc, nt = _norm_title(chapter), _norm_title(title)
    if not nc or not nt or len(nc) < 8:
        return False
    return nc in nt or (len(nt) >= 10 and nt in nc)


def _parse_dt(raw, default=None):
    if not raw:
        return default
    s = str(raw).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return default
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt


def _video_anchor(video, now=None):
    now = now or datetime.now(KST)
    v = video or {}
    for k in ("게시일", "published_at", "publishedAt", "날짜", "date"):
        dt = _parse_dt(v.get(k), None)
        if dt:
            return dt
    return now


def _rfc3339_z(dt) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_search_items(items) -> list:
    out = []
    for it in items or []:
        if not it:
            continue
        if it.get("제목") or it.get("링크") or it.get("video_id"):
            out.append(it)
            continue
        sn = it.get("snippet") or {}
        vid = it.get("id") or ""
        if isinstance(vid, dict):
            vid = vid.get("videoId") or ""
        if not vid:
            continue
        out.append({
            "채널명": sn.get("channelTitle") or "",
            "채널ID": sn.get("channelId") or "",
            "제목": sn.get("title") or "",
            "링크": f"https://www.youtube.com/watch?v={vid}",
            "설명": sn.get("description") or "",
            "게시일": sn.get("publishedAt") or "",
        })
    return out


def _pick_clip(items, chapter_title, exclude_id=None, prefer_channel=None):
    for it in _normalize_search_items(items):
        if exclude_id and _video_id(it) == exclude_id:
            continue
        if prefer_channel and (it.get("채널명") or "") != prefer_channel:
            continue
        if titles_match_chapter(chapter_title, it.get("제목") or ""):
            return it
    return None


def _youtube_search_clips(chapter_title, video, *, channel_only, published_after, published_before):
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        return []
    params = {
        "part": "snippet",
        "q": chapter_title,
        "type": "video",
        "maxResults": 8,
        "order": "relevance",
        "key": api_key,
        "publishedAfter": published_after,
        "publishedBefore": published_before,
    }
    if channel_only:
        cid = (video or {}).get("채널ID") or (video or {}).get("channel_id")
        if cid:
            params["channelId"] = cid
    try:
        r = requests.get(YT_SEARCH_URL, params=params, timeout=10)
        r.raise_for_status()
        items = r.json().get("items") or []
        if channel_only and not params.get("channelId"):
            ch = (video or {}).get("채널명") or ""
            if ch:
                items = [it for it in items if (it.get("snippet") or {}).get("channelTitle") == ch]
        return items
    except Exception as e:
        print(f"ℹ️ [issue_defs] 본편 검색 실패: {_redact(e)}")
        return []


def find_source_clip(video, chapter_title, *, now=None, days=CLIP_WINDOW_DAYS, search=None):
    """라이브/하이라이트 목차 제목으로 같은 채널→유튜브에서 ~days일 창의 본편을 찾는다."""
    if not chapter_title:
        return None
    now = now or datetime.now(KST)
    anchor = _video_anchor(video, now)
    after, before = _rfc3339_z(anchor - timedelta(days=days)), _rfc3339_z(anchor + timedelta(days=1))
    exclude = _video_id(video)
    channel = (video or {}).get("채널명") or ""
    search = search or _youtube_search_clips
    for channel_only in (True, False):
        try:
            items = search(chapter_title, video, channel_only=channel_only,
                           published_after=after, published_before=before)
        except TypeError:
            try:
                items = search(chapter_title, video, channel_only)
            except TypeError:
                items = search(chapter_title, video)
        prefer = channel if channel_only else None
        hit = _pick_clip(items, chapter_title, exclude_id=exclude, prefer_channel=prefer)
        if hit:
            return hit
    return None


def _is_junk_line(line: str) -> bool:
    s = (line or "").strip()
    if s.startswith("※"):
        return True
    if _CHAPTER_RE.match(s) or _is_toc_or_title_line(s):
        return True
    if len(s) <= 30 and _SLOGAN_RE.match(s):
        return True
    if len(s) < 8:
        return True
    if _JUNK_LINE_RE.search(s):
        return True
    if re.fullmatch(r"[\d\-\s\(\)/:]+", s):
        return True
    if s.count("*") >= 3 or set(s) <= set("*-=_~# "):
        return True
    # 채널 소개·코너 소개만 있는 줄
    if re.match(r"^(인싸를 꿈꾸|네 사람의|유쾌|토크쇼)", s):
        return True
    return False


def _finish_sentence(sent, max_len=ONE_LINER_MAX, hard=ONE_LINER_HARD_MAX):
    """한 문장을 한 줄로. 60자를 넘어도 문장이 끝나면 자르지 않는다(하드 한도 hard).
    문장 중간을 '…'로 자르지 않는다. 완전한 문장을 못 만들면 None."""
    sent = (sent or "").strip()
    if not sent:
        return None
    if len(sent) <= max_len:
        return sent
    complete = bool(re.search(r"[.!?。]$|(?:다|요)\.?$", sent))
    if complete and len(sent) <= hard:
        return sent
    window = sent[:hard]
    last = None
    for m in re.finditer(r".+?(?:[.!?。]|다\.|요\.)", window):
        last = m.group(0).strip()
    if last and len(last) >= 12:
        return last
    return None


def summarize_one_liner(text: str, max_len: int = ONE_LINER_MAX, issue_name: str = None):

    """근거 텍스트 → 한 문장(60자 내). 후원·광고·방송코드 줄은 건너뛴다."""
    if not text:
        return None
    keys = []
    if issue_name:
        base = re.sub(r"\s*(논란|이슈|사건)$", "", str(issue_name)).strip()
        if base:
            keys.append(base)
            keys.extend([p for p in re.split(r"[\s/·]+", base) if len(p) >= 2])
    cands = []
    for line in _drop_footer_blocks(str(text).splitlines()):
        if line.strip().startswith("※"):
            continue
        line = _HASHTAG_RE.sub("", _URL_RE.sub("", line)).strip(_BULLET_STRIP)
        if _is_junk_line(line):
            continue
        sent = re.split(r"(?<=[.!?。])\s|(?<=다\.)", line)[0].strip()
        if _is_junk_line(sent):
            continue
        sent = _finish_sentence(sent, max_len)
        if not sent:
            continue
        score = 0
        for k in keys:
            if k and k in sent:
                score += 2
        # 인용·구체 주장 가산, 수사 의문문 감점
        if any(q in sent for q in ("“", '"', "‘", "'")):
            score += 2
        if any(w in sent for w in ("발언", "임기", "비판", "논란", "파장", "합당", "기소", "공소")):
            score += 1
        if sent.endswith(("?", "？", "나", "까", "인가", "을까", "을까.")):
            score -= 2
        cands.append((score, sent))
    # 이슈 핵심어(2자+)가 있으면 점수 0 이하(핵심어·인용·실체어 없음) 줄은 채택하지 않는다 → 다음 근거(보도)로
    # (예전: 전부 0점이면 가장 긴 줄 → 설명란 광고 줄이 정의로 뽑힘)
    if any(len(k) >= 2 for k in keys):
        cands = [c for c in cands if c[0] > 0]
    if not cands:
        return None
    cands.sort(key=lambda x: (-x[0], -len(x[1])))
    # '{이슈명} 관련' 접두는 붙이지 않는다(관련성 없는 줄을 가려 버림) — 관련성은 _is_weak_one_liner가 판단
    return cands[0][1]


_SUBSTANCE_WORDS = ("발언", "비판", "고발", "논란", "파장", "합당", "기소", "공소", "탄핵", "의혹",
                    "사과", "사퇴", "재판", "특검", "수사", "판결", "구속", "반발", "주장", "폭로",
                    "제기", "요구", "규탄", "징계", "출마", "공천", "선고", "압수", "체포", "고소")
# 방송 라벨: MBC 라디오 〈김종배의 시선집중〉 261103(화) 방송 등
_BROADCAST_RE = re.compile(
    r"(MBC|KBS|SBS|YTN|JTBC|CBS|TBS|BBS|MBN|채널A|TV조선|라디오|시선집중|뉴스공장|"
    r"\d{6}\s*\(?[월화수목금토일]?\)?\s*방송|\S+방송$|〈[^〉]*〉|<[^>]*>|생방송|LIVE|풀버전|다시보기)",
    re.I)


def _is_broadcast_header(line: str, issue_name: str = None) -> bool:
    """'○○ 관련' 접두·방송사·프로그램명·방송코드를 걷어내면 남는 게 거의 없는 줄."""
    s = str(line or "")
    base = re.sub(r"\s*(논란|이슈|사건)$", "", strip_auto(issue_name or "")).strip()
    if base:
        s = s.replace(base, " ")
    s = re.sub(r"^\s*관련\s*", " ", s)
    if not _BROADCAST_RE.search(str(line or "")):
        return False
    rest = _BROADCAST_RE.sub(" ", s)
    rest = re.sub(r"[\d\s\(\)\[\]〈〉<>《》「」'\"“”‘’·,.:|ㅣ/\-~…!?]+", " ", rest)
    rest = re.sub(r"\b(관련|의|에서|오늘|방송|라디오)\b", " ", rest)
    return len(rest.replace(" ", "")) <= 6


def _issue_core_words(issue_name=None, aliases=None):
    """이슈 핵심어: 이슈명(논란/이슈/사건 제거)과 그 토막(2자+), 키워드(aliases)."""
    base = re.sub(r"\s*(논란|이슈|사건)$", "", strip_auto(issue_name or "")).strip()
    words = [base] if len(base) >= 2 else []
    words += [p for p in re.split(r"[\s/·]+", base) if len(p) >= 2]
    words += [a for a in (aliases or []) if a and len(str(a)) >= 2]
    return list(dict.fromkeys(words))


def _is_weak_one_liner(line, issue_name=None, aliases=None) -> bool:
    """정의 한 줄로 쓰기에 부적합(잡음·광고·방송 라벨·실체 없음·이슈와 무관)이면 True."""
    s = str(line or "").strip()
    if not s:
        return True
    if _is_toc_or_title_line(s):
        return True
    core = re.sub(r"^\S+(\s+\S+)?\s+관련\s+", "", s) if " 관련 " in s else s
    if _is_junk_line(s) or _is_junk_line(core) or _JUNK_LINE_RE.search(s):
        return True
    if _is_broadcast_header(s, issue_name):
        return True
    base = re.sub(r"\s*(논란|이슈|사건)$", "", strip_auto(issue_name or "")).strip()
    body = s.replace(base, "") if base else s
    body = re.sub(r"\s*관련\s*", " ", body).strip()
    has_sub = any(w in s for w in _SUBSTANCE_WORDS) or any(q in s for q in ("“", '"', "‘"))
    if len(body.replace(" ", "")) < 15 and not has_sub:
        return True
    # 관련성 하한: 이슈 핵심어(키워드 포함)도 실체어도 없으면 거부
    core_words = _issue_core_words(issue_name, aliases)
    # (핵심어를 만들 수 없는 이슈명이면 판단 근거가 없어 건너뜀)
    if core_words and not any(w in s for w in core_words) and not any(w in s for w in _SUBSTANCE_WORDS):
        return True
    return False


def _is_omnibus_for(title: str, issue_name: str = None) -> bool:
    """'A | B | C' 식 여러 주제 묶음 LIVE이고 이슈 핵심어가 그중 한 토막에만 있으면 True."""
    t = str(title or "")
    parts = [p.strip() for p in re.split(r"[|ㅣ]", t) if p.strip()]
    if len(parts) < 3:
        return False
    base = re.sub(r"\s*(논란|이슈|사건)$", "", strip_auto(issue_name or "")).strip()
    toks = [w for w in re.split(r"[\s/·]+", base) if len(w) >= 2] or ([base] if base else [])
    if not toks:
        return True
    hit = sum(1 for p in parts if any(k in p for k in toks))
    return hit <= 1


def _title_one_liner(title: str, issue_name: str = None, max_len: int = ONE_LINER_MAX):
    """설명·자막이 광고뿐일 때 제목에서 담백한 한 줄. 자극 물음표·말줄임은 정리."""
    t = _HASHTAG_RE.sub("", _URL_RE.sub("", title or "")).strip()
    t = re.sub(r"^(\s*(\[[^\]]*\]|【[^】]*】))+\s*", "", t)  # 앞머리 [코너]·【날짜】 태그 제거
    t = t.replace("..", " ").replace("…", " ")
    t = re.sub(r"[?？]+$", "", t).strip()
    t = re.sub(r"\s+", " ", t)
    if len(t) < 10 or _is_junk_line(t):
        return None
    # 제목만으로 쟁점이 안 드러나면 커버리지 문장으로
    base = re.sub(r"\s*(논란|이슈|사건)$", "", str(issue_name or "")).strip()
    clickbait = bool(re.search(r"(실화|충격|경악|소름|몰락|폭로)", t)) or t.endswith(("?", "？"))
    if clickbait and base:
        # 제목만으로는 쟁점이 안 보이므로 커버리지 문장으로
        short = t[:40].rstrip() + ("…" if len(t) > 40 else "")
        sent = f"여권 채널이 「{short}」 등으로 {base} 관련 논점을 다룸"
    elif base and base not in t:
        sent = f"{base} 관련으로 「{t}」 등이 상위 조회"
    else:
        sent = t
    if len(sent) > max_len:
        sent = sent[: max_len - 1].rstrip() + "…"
    return sent


def _pick_standalone(videos, core_words=None):
    """단독 영상 후보. core_words가 있으면 제목에 핵심어가 있는 영상을 먼저, 그다음 조회수순."""
    cands = [v for v in (videos or []) if v.get("프레임") not in NON_STANDALONE_FRAMES]
    words = [w for w in (core_words or []) if w]
    return sorted(cands, key=lambda v: (any(w in str(v.get("제목") or "") for w in words),
                                        int(v.get("조회수") or 0)), reverse=True)



def _news_body(n: dict) -> str:
    """보도 본문(리드). 제목만 있는 text는 빈 문자열."""
    if not n:
        return ""
    if n.get("lead"):
        return n["lead"]
    text, title = n.get("text") or "", n.get("title") or ""
    if text and title and text.startswith(title) and "\n" in text:
        return text.split("\n", 1)[-1].strip()
    if text and text != title:
        return text
    return ""


def _news_body_has_kw(n: dict, kws) -> bool:
    if not kws:
        return True
    body = _news_body(n)
    return any(k in body for k in kws if k)


def _reject_line(line, issue_name, aliases=None, title=None) -> bool:
    if not line:
        return True
    if _is_weak_one_liner(line, issue_name, aliases) or _is_toc_or_title_line(line, title):
        return True
    return False


def _build_one(issue_name, videos, summarize, describe, caption, news, max_videos=5, aliases=None, search_clip=None):
    picked = _pick_standalone(videos, _issue_core_words(issue_name))[:max_videos]
    search_clip = search_clip or find_source_clip
    for v in picked:
        # 여러 주제 묶음 LIVE에서 이슈가 한 토막뿐이면 설명·자막은 다른 주제 잡음 → 건너뜀
        if _is_omnibus_for(v.get("제목") or "", issue_name):
            continue
        cap = caption(v)
        if _is_live_or_highlight(v) and not cap:
            desc = v.get("설명") or describe(v)
            chapter = pick_toc_chapter(desc, issue_name, aliases)
            clip = search_clip(v, chapter) if chapter else None
            if clip:
                cap2 = caption(clip)
                if cap2:
                    try:
                        line = summarize(cap2, issue_name=issue_name)
                    except TypeError:
                        line = summarize(cap2)
                    if line and _reject_line(line, issue_name, aliases, clip.get("제목")):
                        print(f"ℹ️ [issue_defs] '{issue_name}' 본편 자막 약한 한 줄 거부: {line[:40]}")
                        line = None
                    if line:
                        return line, {"evidence_channel": clip.get("채널명") or "",
                                      "evidence_title": clip.get("제목") or "",
                                      "evidence_url": clip.get("링크") or "", "evidence_kind": "caption"}
            print(f"ℹ️ [issue_defs] '{issue_name}' 라이브/하이라이트·자막 없음 → 영상 생략")
            continue
        pairs = (("caption", cap),) if (_is_live_or_highlight(v) and cap) else (("description", describe(v)), ("caption", cap))
        for kind, raw in pairs:
            try:
                line = summarize(raw, issue_name=issue_name)
            except TypeError:
                line = summarize(raw)
            if line and _reject_line(line, issue_name, aliases, v.get("제목")):
                print(f"ℹ️ [issue_defs] '{issue_name}' {kind} 약한 한 줄 거부: {line[:40]}")
                line = None
            if line:
                return line, {"evidence_channel": v.get("채널명") or "", "evidence_title": v.get("제목") or "",
                              "evidence_url": v.get("링크") or "", "evidence_kind": kind}
    n = news(issue_name)
    if n and not _news_body_has_kw(n, _issue_core_words(issue_name, aliases) or _news_keywords(issue_name)):
        print(f"ℹ️ [issue_defs] '{issue_name}' news 제목만 매칭 거부")
        n = None
    if n:
        try:
            line = summarize(n.get("text") or n.get("title"), issue_name=issue_name)
        except TypeError:
            line = summarize(n.get("text") or n.get("title"))
        if line and _reject_line(line, issue_name, aliases):
            print(f"ℹ️ [issue_defs] '{issue_name}' news 약한 한 줄 거부: {line[:40]}")
            line = None
        if line:
            return line, {"evidence_channel": n.get("channel") or "", "evidence_title": n.get("title") or "",
                          "evidence_url": n.get("url") or "", "evidence_kind": "news"}
    # 제목만으로는 정의를 만들지 않는다
    return None, None


def build_defs(issue_rows, videos, source="yeogwon", *, now=None, summarize=None,
               describe=None, caption=None, news=None, aliases=None) -> dict:
    """issue_rows: extract_major_issues 레코드(이슈명 등). videos: {이슈명: [영상 dict(채널명/제목/조회수/링크/프레임/설명)]}.
    aliases: {이슈명: [키워드]} (선택). 근거 없는 이슈는 issues에서 빠진다."""
    now = now or datetime.now(KST)
    summarize = summarize or summarize_one_liner
    describe = describe or fetch_description
    caption = caption or fetch_caption_head
    news = news or fetch_news
    aliases = aliases or {}
    gen_at = now.isoformat(timespec="seconds")
    out = {"date": now.strftime("%Y-%m-%d"), "source": source, "generated_at": gen_at, "issues": []}
    for row in issue_rows or []:
        raw = row.get("이슈명")
        if not raw:
            continue
        name = strip_auto(raw)
        try:
            line, ev = _build_one(name, (videos or {}).get(raw) or (videos or {}).get(name),
                                  summarize, describe, caption, news,
                                  aliases=aliases.get(raw) or aliases.get(name))
        except Exception as e:
            print(f"⚠️ [issue_defs] '{name}' 정의 생성 실패: {_redact(e)}")
            continue
        if not line:
            print(f"ℹ️ [issue_defs] '{name}' 근거 없음 → 정의 생략")
            continue
        al = [a for a in (aliases.get(raw) or aliases.get(name) or []) if a and a != name]
        d = {"date": out["date"], "source": source, "issue_name": name, "aliases": al[:5],
             "definition_one_liner": line, **ev, "generated_at": gen_at}
        out["issues"].append(d)
    return out


def kv_key(defs: dict) -> str:
    return f"defs:{defs.get('date')}:{defs.get('source')}"


def publish_defs(defs, *, session=None) -> None:
    """Cloudflare KV REST로 defs:{date}:{source} PUT (TTL 7일). 환경변수 미설정·오류 시 로그만 남기고 반환."""
    acct, ns, token = (os.environ.get(k) for k in ("CF_ACCOUNT_ID", "CF_KV_NAMESPACE_ID", "CF_API_TOKEN"))
    if not (acct and ns and token):
        print("ℹ️ [issue_defs] CF_* 미설정 → KV 게시 생략")
        return
    if not defs or not defs.get("issues"):
        print("ℹ️ [issue_defs] 게시할 정의 없음")
        return
    key = kv_key(defs)
    url = f"{CF_API_BASE}/accounts/{acct}/storage/kv/namespaces/{ns}/values/{key}"
    try:
        r = (session or requests).put(url, params={"expiration_ttl": KV_TTL_SEC},
                                      headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                                      data=json.dumps(defs, ensure_ascii=False).encode("utf-8"), timeout=15)
        if r.status_code >= 300:
            print(f"⚠️ [issue_defs] KV 게시 실패: HTTP {r.status_code}")
            return
        print(f"✅ [issue_defs] KV 게시: {key} ({len(defs['issues'])}건)")
    except Exception as e:
        print(f"⚠️ [issue_defs] KV 게시 에러: {_redact(e)}")
