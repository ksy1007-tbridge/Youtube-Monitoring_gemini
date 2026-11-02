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


def fetch_news(issue_name: str):
    """보도 1건 폴백 → {"channel","title","url","text"} 또는 None. TODO: 뉴스 소스 미정, Phase A에서는 None."""
    return None


def summarize_one_liner(text: str, max_len: int = ONE_LINER_MAX):
    """근거 텍스트 → 한 문장(60자 내). LLM 없이 첫 의미 있는 문장을 정리해 자른다."""
    if not text:
        return None
    for line in str(text).splitlines():
        line = _HASHTAG_RE.sub("", _URL_RE.sub("", line)).strip(" \t-•▶▷■※*|")
        if len(line) < 8:
            continue
        sent = re.split(r"(?<=[.!?。])\s|(?<=다\.)", line)[0].strip()
        if len(sent) > max_len:
            sent = sent[:max_len - 1].rstrip() + "…"
        return sent
    return None


def _pick_standalone(videos):
    cands = [v for v in (videos or []) if v.get("프레임") not in NON_STANDALONE_FRAMES]
    return sorted(cands, key=lambda v: int(v.get("조회수") or 0), reverse=True)


def _build_one(issue_name, videos, summarize, describe, caption, news, max_videos=3):
    for v in _pick_standalone(videos)[:max_videos]:
        for kind, getter in (("description", describe), ("caption", caption)):
            line = summarize(getter(v))
            if line:
                return line, {"evidence_channel": v.get("채널명") or "", "evidence_title": v.get("제목") or "",
                              "evidence_url": v.get("링크") or "", "evidence_kind": kind}
    n = news(issue_name)
    if n:
        line = summarize(n.get("text") or n.get("title"))
        if line:
            return line, {"evidence_channel": n.get("channel") or "", "evidence_title": n.get("title") or "",
                          "evidence_url": n.get("url") or "", "evidence_kind": "news"}
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
