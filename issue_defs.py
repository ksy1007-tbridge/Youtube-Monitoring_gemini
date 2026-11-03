# -*- coding: utf-8 -*-
"""[Phase A] 텔레그램 「○○ 논란이 뭐야?」 Q&A용 이슈 한 줄 정의 생성·KV 게시.

설계: telegram_issue_qa_design.md §1·§2·§5(A)
- build_defs(issue_rows, videos, source="yeogwon") -> dict
- publish_defs(defs) -> None  (Cloudflare KV REST PUT, 실패/미설정이어도 예외 없음)
근거 순서: 이슈별 조회수 상위 단독 영상 설명란 → 자막 앞부분(~1분) → 보도 1건. 근거 없으면 정의 생략.
"""
import os
import re
import json
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
DYNAMIC_TAG = " (자동)"
ONE_LINER_MAX = 60
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


def fetch_caption_head(video: dict, seconds: int = 60):
    """자막 앞부분(~1분). TODO: captions API는 OAuth 필요 → Phase A에서는 미구현(None)."""
    return None


GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
_TAG_RE = re.compile(r"<[^>]+>")


def _news_query(issue_name: str) -> str:
    base = re.sub(r"\s*(논란|이슈|사건)\s*$", "", strip_auto(issue_name or "")).strip()
    base = re.sub(r"[\[\]()「」『』\"“”‘’]", " ", base)
    return re.sub(r"\s+", " ", base).strip()


def parse_news_rss(xml_text: str):
    """Google 뉴스 RSS → 최신 pubDate 1건 {"channel","title","url","text"} 또는 None."""
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return None
    best, best_ts = None, None
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
        # Google RSS description은 관련기사 묶음이라 자기 제목·언론사 외 리드가 있을 때만 사용
        lead = "" if (not desc or title in desc) else desc[:200]
        try:
            ts = parsedate_to_datetime(it.findtext("pubDate") or "").timestamp()
        except Exception:
            ts = 0
        if best is None or ts > best_ts:
            text = title + ("\n" + lead if lead else "")
            best, best_ts = {"channel": channel, "title": title, "url": url, "text": text}, ts
    return best


def fetch_news(issue_name: str, timeout: int = 8):
    """보도 1건 폴백 → {"channel","title","url","text"} 또는 None.
    소스: Google 뉴스 RSS 검색(키 불필요), 최근 7일 최신순 1건. 실패·0건은 None."""
    q = _news_query(issue_name)
    if not q or os.environ.get("ISSUE_DEFS_NEWS_DISABLE") == "1":
        return None
    try:
        r = requests.get(GOOGLE_NEWS_RSS,
                         params={"q": f"{q} when:7d", "hl": "ko", "gl": "KR", "ceid": "KR:ko"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        r.raise_for_status()
        return parse_news_rss(r.text)
    except Exception as e:
        print(f"⚠️ [issue_defs] 보도 조회 실패: {_redact(e)}")
        return None


_JUNK_LINE_RE = re.compile(
    r"(ARS|후원|광고|계좌|멤버십|구독\s*자|좋아요|알림\s*설정|홈페이지|"
    r"bit\.ly|카톡|텔레그램|출연\s*[:：]|\(\s*출연\s*\)|진행\s*[:：]|문의\s*[:：]|메일|email|"
    r"RADIO\s+\S+\s+\d{4,}\s*방송|\d{2,4}-\d{3,4}-\d{4}|"
    # 출처·자막 고지 / 저작권 / 제품 링크 / 도서 / 인터뷰 전문
    r"출처|자막\s*으로\s*명시|화면\s*인용|다음과\s*같이\s*명시|"
    r"공정\s*사용|fair\s*use|저작권|콘텐츠\s*(무단\s*)?(사용|이용)|무단\s*(전재|배포)|"
    r"제품\s*링크|도서\s*구입|책\s*구입|인터뷰\s*전문)",
    re.I,
)

# 챕터 타임스탬프 줄: [4:10] …, 12:00 …
_CHAPTER_RE = re.compile(r"^\s*[\[(]?\d{1,2}:\d{2}(:\d{2})?[\])]?(\s|$)")
# 채널 브랜드·슬로건만 있는 짧은 줄
_SLOGAN_RE = re.compile(r"^(미디어\s*['‘\"“「].{1,20}['’\"”」]|.{0,12}더\s*나은\s*세상)\S*\s*[!.]?$")


def _is_junk_line(line: str) -> bool:
    s = (line or "").strip()
    if s.startswith("※"):
        return True
    if _CHAPTER_RE.match(s):
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
    for line in str(text).splitlines():
        if line.strip().startswith("※"):
            continue
        line = _HASHTAG_RE.sub("", _URL_RE.sub("", line)).strip(" \t-•▶▷■※*|")
        if _is_junk_line(line):
            continue
        sent = re.split(r"(?<=[.!?。])\s|(?<=다\.)", line)[0].strip()
        if _is_junk_line(sent):
            continue
        if len(sent) > max_len:
            sent = sent[: max_len - 1].rstrip() + "…"
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
    if not cands:
        return None
    cands.sort(key=lambda x: (-x[0], -len(x[1])))
    best = cands[0][1]
    # 인용만 뽑혔고 이슈명이 없으면 앞에 붙여 한 문장으로
    if keys and not any(k in best for k in keys if len(k) >= 2):
        prefix = keys[0]
        best = f"{prefix} 관련 {best}".strip()
        if len(best) > max_len:
            best = best[: max_len - 1].rstrip() + "…"
    return best


def _title_one_liner(title: str, issue_name: str = None, max_len: int = ONE_LINER_MAX):
    """설명·자막이 광고뿐일 때 제목에서 담백한 한 줄. 자극 물음표·말줄임은 정리."""
    t = _HASHTAG_RE.sub("", _URL_RE.sub("", title or "")).strip()
    t = re.sub(r"^\[.*?\]\s*", "", t)
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


def _pick_standalone(videos):
    cands = [v for v in (videos or []) if v.get("프레임") not in NON_STANDALONE_FRAMES]
    return sorted(cands, key=lambda v: int(v.get("조회수") or 0), reverse=True)


def _build_one(issue_name, videos, summarize, describe, caption, news, max_videos=5):
    picked = _pick_standalone(videos)[:max_videos]
    for v in picked:
        for kind, getter in (("description", describe), ("caption", caption)):
            raw = getter(v)
            try:
                line = summarize(raw, issue_name=issue_name)
            except TypeError:
                line = summarize(raw)
            if line:
                return line, {"evidence_channel": v.get("채널명") or "", "evidence_title": v.get("제목") or "",
                              "evidence_url": v.get("링크") or "", "evidence_kind": kind}
    n = news(issue_name)
    if n:
        try:
            line = summarize(n.get("text") or n.get("title"), issue_name=issue_name)
        except TypeError:
            line = summarize(n.get("text") or n.get("title"))
        if line:
            return line, {"evidence_channel": n.get("channel") or "", "evidence_title": n.get("title") or "",
                          "evidence_url": n.get("url") or "", "evidence_kind": "news"}
    # 마지막: 상위 단독 영상 제목
    for v in picked:
        line = _title_one_liner(v.get("제목") or "", issue_name)
        if line:
            return line, {"evidence_channel": v.get("채널명") or "", "evidence_title": v.get("제목") or "",
                          "evidence_url": v.get("링크") or "", "evidence_kind": "description"}
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
                                  summarize, describe, caption, news)
        except Exception as e:
            print(f"⚠️ [issue_defs] '{name}' 정의 생성 실패: {_redact(e)}")
            continue
        if not line:
            print(f"ℹ️ [issue_defs] '{name}' 근거 없음 → 정의 생략")
            continue
        al = [a for a in (aliases.get(raw) or aliases.get(name) or []) if a and a != name]
        d = {"date": out["date"], "source": source, "issue_name": name, "aliases": al[:5],
             "definition_one_liner": line, **ev, "generated_at": gen_at}
        if row.get("match_note"):
            d["match_note"] = row["match_note"]
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
