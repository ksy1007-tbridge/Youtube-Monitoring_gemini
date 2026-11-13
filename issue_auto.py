# -*- coding: utf-8 -*-
"""자동 이슈 추출 공통 임계·배정·오프라인 후보. 양쪽 저장소에 같은 파일을 둔다.

상수는 이 모듈에서만 바꾸고, 채택/탈락 시 로그에 남긴다.
이름 사전(issue_aliases)은 라벨에만 쓰고 배정·채택에는 쓰지 않는다.
"""
from __future__ import annotations

import re
from collections import defaultdict

from issue_tracker import _terms, name_tokens, strip_tag

# ----- 채택 기본값 (조정 가능) -----
DYNAMIC_MIN_TITLES = 3
DYNAMIC_MIN_CHANNELS = 2
# 최대 채널 조회 비중 80% 미만 채택. 소연이 7일 표로 탈락 기준으로 쓸지 결정한다.
DYNAMIC_MAX_CHANNEL_SHARE = 80.0
DYNAMIC_HIGH_VIEW = 500000
DYNAMIC_HIGH_VIEW_EXEMPT = False  # 기본 끔. True면 고조회 1건으로 건수·채널 하한 면제
DYNAMIC_MAX_ISSUES = 15
DYNAMIC_MAX_PERSONS = 5
DYNAMIC_TAG = " (자동)"

# 단독으로 묶음을 만들지 않는 일반어 (인물명만도 금지 — AUTO_ISSUE_MUST에서 처리)
GENERAL_WORDS = frozenset({
    "성남라인", "성남 라인", "의혹", "스캔들", "논란", "공방", "문제", "사태", "파문", "이슈",
    "관련", "발언", "대응", "비판", "주장", "언급", "파장", "후폭풍",
})

_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]{2,12}")
_STOP = frozenset({
    "오늘", "어제", "이번", "최근", "단독", "속보", "긴급", "종합", "뉴스", "방송", "라이브",
    "풀버전", "다시보기", "인터뷰", "기자", "대통령", "의원", "대표", "장관", "총리", "정부",
    "국회", "민주당", "국민의힘", "국힘", "여당", "야당", "관련", "대해", "위한", "있는",
    "없는", "하는", "되는", "했다", "한다", "이다", "있다", "같은", "다른", "통해", "대한",
    "이유", "없다", "결정적", "비공개", "발표", "현장", "하나", "만에", "세력", "걱정하는",
    "보수", "공개", "다시", "직접", "나온", "유튜브", "본방송", "시선집중", "클립", "영상",
    "특집", "출연", "풀영상", "하이라이트", "더불어민주당", "국회의원", "국무회의", "기자회견",
    "쇼츠", "shorts", "live", "생중계", "클립영상", "뉴스공장", "매불쇼", "누구를", "것인가",
    "그리고", "이렇게", "그렇게", "어떻게", "됐다", "됩니다", "입니다", "합니다",
    "월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일",
    "진짜", "시작", "지금", "그때", "누가", "정치", "전문가", "분노", "사람", "보고",
    "최고", "취재", "예산", "법무장관", "당대표", "해법은", "본인", "우리", "그들",
    "이야기", "얘기", "상황", "부분", "내용", "사실", "정말", "계속", "아직", "이미",
    "이후", "이전", "오늘자", "어제자", "속보로",
    "아니다", "그렇다", "맞다", "해체하라", "브리핑", "MBC", "FULL", "KBS", "SBS",
    "숏탐사", "정치숏탐사", "뉴스바사삭", "뉴스하이킥", "현장라이브", "1분바사삭",
    "종대타임", "뉴잼스",
    "프로젝트", "최고위", "최고위원", "간담회", "워크숍", "현안", "소식",
    "모음집", "이슈모음", "관련소식", "공식입장",
})

# 날짜 코드·방송 표기 등 — 이슈명/사건어로 쓰지 않음
_JUNK_TOKEN_RE = re.compile(
    r"^(?:\d+|\d{1,2}월|\d{1,2}일|\d{2,4}년|20\d{2}년?|live|shorts)$",
    re.I,
)
_PARTICLE_RE = re.compile(r"(의|은|는|을|를|이|가|도|만|과|와|로|으로|에서|부터|까지)$")


def is_junk_token(tok: str) -> bool:
    """날짜 스탬프·조사만 남은 말·방송 표기는 클러스터 키가 되지 않는다."""
    t = (tok or "").strip()
    if not t:
        return True
    if t in _STOP or t in GENERAL_WORDS:
        return True
    if _JUNK_TOKEN_RE.match(t) or t.lower() in {"live", "shorts"}:
        return True
    if re.fullmatch(r"\d{3,8}", t) or re.fullmatch(r"26\d{4}", t):
        return True
    if re.fullmatch(r"\d+(?:억|만|원|명|건|회|%)", t):
        return True
    stem = _PARTICLE_RE.sub("", t)
    if stem and stem != t and (stem in _STOP or stem in GENERAL_WORDS or _JUNK_TOKEN_RE.match(stem) or len(stem) < 3):
        return True
    if t.isascii() and t.isalpha() and len(t) <= 4:
        return True
    return False


def is_junk_issue_name(name: str) -> bool:
    """이슈명 전체가 잡토큰이거나, 쓸 수 있는 토큰이 없으면 True."""
    n = strip_tag(name or "").strip()
    if not n:
        return True
    if is_junk_token(n) or is_junk_token(n.replace(" ", "")):
        return True
    toks = _TOKEN_RE.findall(n)
    useful = [t for t in toks if not is_junk_token(t)]
    return not useful


def log_thresholds() -> None:
    print(
        f"ℹ️ [자동 추출] 채택 임계: 제목>={DYNAMIC_MIN_TITLES} 채널>={DYNAMIC_MIN_CHANNELS} "
        f"최대채널점유>={DYNAMIC_MAX_CHANNEL_SHARE}%는 내부·편중(탈락 아님) "
        f"high_view_exempt={DYNAMIC_HIGH_VIEW_EXEMPT} "
        f"(고조회={DYNAMIC_HIGH_VIEW}) 상한={DYNAMIC_MAX_ISSUES}"
    )


def is_general_only(keywords, must=None) -> bool:
    """사건어가 일반어뿐이거나, 인물만 있고 사건어가 없으면 True."""
    kws = [k for k in (keywords or []) if k and str(k).strip()]
    must_set = set(must or [])
    events = [k for k in kws if k not in must_set]
    if must and not events:
        return True
    if events and all(e in GENERAL_WORDS for e in events) and not must:
        return True
    if not must and events and all(e in GENERAL_WORDS for e in events):
        return True
    return False


def specificity_score(name: str, keywords=None, must=None) -> tuple:
    """더 구체적(더 긴 토큰·인물+사건 AND)이 앞. 동점이면 호출측에서 조회·건수로 가른다."""
    kws = [k for k in (keywords or []) if k]
    longest = max((len(k) for k in kws), default=0)
    total = sum(len(k) for k in kws)
    and_bonus = 1 if must else 0
    ntoks = len(name_tokens(name))
    return (and_bonus, longest, total, ntoks)


def title_share(views_by_channel: dict) -> float:
    total = sum(views_by_channel.values())
    if not total:
        return 0.0
    return round(max(views_by_channel.values()) / total * 100, 1)


def issue_bucket(n_channels: int, top_share=None, *, bias_pct: float = 60.0,
                 min_channels_spread: int = 3) -> str:
    """'spread' 또는 'internal'. 채널 수 < 3이거나 최대 점유율 ≥ bias_pct(조회 없으면 채널만)."""
    if int(n_channels or 0) < min_channels_spread:
        return "internal"
    if top_share is not None and float(top_share) >= bias_pct:
        return "internal"
    return "spread"


def passes_thresholds(n_titles: int, n_channels: int, top_share: float,
                      max_view: int = 0, *, apply_share: bool = False) -> tuple:
    """반환: (통과 여부, 탈락 이유 또는 ''). 건수·채널 하한만 탈락 기준.
    apply_share=True는 하위 호환(과거 80% 탈락)용이며 기본은 쓰지 않는다.
    80% 이상은 탈락이 아니라 내부·편중(참고만)으로 보낸다."""
    if n_titles < DYNAMIC_MIN_TITLES or n_channels < DYNAMIC_MIN_CHANNELS:
        if DYNAMIC_HIGH_VIEW_EXEMPT and max_view >= DYNAMIC_HIGH_VIEW and n_titles > 0:
            pass
        else:
            return False, f"하한 미달({n_titles}건/{n_channels}채널)"
    if apply_share and top_share >= DYNAMIC_MAX_CHANNEL_SHARE:
        return False, f"최대채널 {top_share}% (≥{DYNAMIC_MAX_CHANNEL_SHARE}%)"
    return True, ""


def assign_videos(videos, issues, already_claimed=None):
    """영상 1건은 이슈 1곳. videos: [{id, title, channel, views}], 
    issues: [{name, keywords, must, views, cnt}]  (views/cnt는 순위용 사전 집계, 없어도 됨).

    동점이면 키워드가 더 구체적(더 긴 토큰·인물+사건 AND)인 쪽, 그래도 같으면 순위(조회·건수)가 높은 쪽.
    반환: {video_id: issue_name}, 이슈별 배정 목록 {name: [video, ...]}
    """
    claimed = set(already_claimed or [])
    # 사전 순위
    rank = {}
    for i, iss in enumerate(issues):
        rank[iss["name"]] = (int(iss.get("views") or 0), int(iss.get("cnt") or 0), -i)

    def hits(title, iss) -> bool:
        title = title or ""
        kws = [k for k in (iss.get("keywords") or []) if k]
        if not kws or not any(k in title for k in kws):
            return False
        must = [m for m in (iss.get("must") or []) if m]
        if must and not any(m in title for m in must):
            return False
        return True

    assignment = {}
    for v in videos:
        if v["id"] in claimed:
            continue
        title = v.get("title") or ""
        cands = [iss for iss in issues if hits(title, iss)]
        if not cands:
            continue
        def key(iss):
            return (specificity_score(iss["name"], iss.get("keywords"), iss.get("must")),
                    rank.get(iss["name"], (0, 0, 0)))
        best = max(cands, key=key)
        assignment[v["id"]] = best["name"]

    by_issue = defaultdict(list)
    for v in videos:
        name = assignment.get(v["id"])
        if name:
            by_issue[name].append(v)
    return assignment, dict(by_issue)


def summarize_group(sel) -> dict:
    """배정된 영상 목록 → 건수·채널·조회·최대점유."""
    if not sel:
        return {"cnt": 0, "channels": 0, "views": 0, "top_share": 0.0,
                "top_ch": "", "by_ch": {}, "max_view": 0}
    by_ch = defaultdict(int)
    for v in sel:
        by_ch[v.get("channel") or ""] += int(v.get("views") or 0)
    total = sum(by_ch.values())
    top_ch = max(by_ch, key=by_ch.get) if by_ch else ""
    top_share = round((by_ch[top_ch] / total * 100), 1) if total else 0.0
    return {
        "cnt": len(sel),
        "channels": len({v.get("channel") for v in sel}),
        "views": int(total),
        "top_share": top_share,
        "top_ch": top_ch,
        "by_ch": dict(by_ch),
        "max_view": max(int(v.get("views") or 0) for v in sel),
    }


def adopt_after_assign(issues, by_issue, *, apply_share=False):
    """배정 후 임계를 다시 적용. 탈락 이슈의 영상은 재배정하지 않고 사유만 남긴다.
    반환: (채택 이슈 목록(stats 포함), 탈락 [{name, reason, stats}], 80%만으로 탈락한 것).
    """
    adopted, dropped, dropped_share = [], [], []
    for iss in issues:
        sel = by_issue.get(iss["name"]) or []
        st = summarize_group(sel)
        ok, reason = passes_thresholds(st["cnt"], st["channels"], st["top_share"],
                                       st["max_view"], apply_share=apply_share)
        rec = {**iss, **st, "videos": sel}
        if ok:
            adopted.append(rec)
        else:
            rec["drop_reason"] = reason
            dropped.append(rec)
            if "최대채널" in reason:
                dropped_share.append(rec)
            print(f"ℹ️ [자동 추출] 탈락 '{strip_tag(iss['name'])}': {reason} "
                  f"({st['cnt']}건/{st['channels']}채널/{st['views']:,}회/최대{st['top_share']}%)")
    adopted.sort(key=lambda r: (int(r.get("views") or 0), int(r.get("cnt") or 0)), reverse=True)
    if len(adopted) > DYNAMIC_MAX_ISSUES:
        extra = adopted[DYNAMIC_MAX_ISSUES:]
        for r in extra:
            r["drop_reason"] = f"추출 상한 {DYNAMIC_MAX_ISSUES}"
            dropped.append(r)
            print(f"ℹ️ [자동 추출] 탈락 '{strip_tag(r['name'])}': 추출 상한 {DYNAMIC_MAX_ISSUES}")
        adopted = adopted[:DYNAMIC_MAX_ISSUES]
    return adopted, dropped, dropped_share


def extract_offline_candidates(titles, person_alias_map=None, min_titles: int = None) -> dict:
    """Gemini 없이 제목에서 후보를 뽑는다 (재현·폴백). 채택 여부는 filter가 결정.
    person_alias_map: {인물표시명: [별칭...]}.
    """
    min_titles = DYNAMIC_MIN_TITLES if min_titles is None else min_titles
    person_alias_map = person_alias_map or {}
    # 긴 별칭 우선
    person_index = []
    for name, als in person_alias_map.items():
        for a in sorted(set([name] + list(als or [])), key=len, reverse=True):
            if a and len(a) >= 2:
                person_index.append((a, name))

    def persons_in(title: str):
        hits = []
        for a, name in person_index:
            if a in title and name not in hits:
                hits.append(name)
        return hits

    def tokens(title: str):
        out = []
        for raw in _TOKEN_RE.findall(title or ""):
            stem = _PARTICLE_RE.sub("", raw) or raw
            use = stem if len(stem) >= 2 else raw
            if use in _STOP or use in GENERAL_WORDS or is_junk_token(use) or is_junk_token(raw) or len(use) < 2:
                continue
            out.append(use)
        return out

    clusters = []  # (name, keywords, must, title_set)

    # 1) 인물 + 사건어
    by_person = defaultdict(list)
    for t in titles:
        for p in persons_in(t):
            by_person[p].append(t)
    for p, ts in by_person.items():
        if len(set(ts)) < min_titles:
            continue
        event_counts = defaultdict(int)
        als = set([p] + list(person_alias_map.get(p) or [p]))
        for t in ts:
            for tok in tokens(t):
                if tok in als or any(tok in a or a in tok for a in als if len(tok) >= 2):
                    continue
                if tok in GENERAL_WORDS:
                    continue
                event_counts[tok] += 1
        events = [e for e, c in sorted(event_counts.items(), key=lambda x: (-x[1], -len(x[0]))) if c >= min_titles]
        if not events:
            # 일반 사건어(의혹 등)라도 인물과 같이 있으면 후보로 — filter가 GENERAL_ONLY+MUST를 봄
            soft = [e for e, c in event_counts.items() if c >= min_titles]
            events = soft[:3]
        if not events:
            continue
        ev = events[:4]
        name = f"{p} {ev[0]}"
        kws = ev[:]
        clusters.append((name, kws, list(als), set(ts)))

    # 2) 비인물 반복 토큰 (3글자+ 또는 2글자이되 빈도 높음)
    tok_titles = defaultdict(list)
    for t in titles:
        seen = set()
        for tok in tokens(t):
            if tok in seen:
                continue
            if any(tok == a or tok in a for a, _ in person_index):
                continue
            seen.add(tok)
            tok_titles[tok].append(t)
    person_alias_flat = set()
    for name, als in person_alias_map.items():
        person_alias_flat.add(name)
        person_alias_flat.update(als or [])
    n_titles = max(len(set(titles or [])), 1)
    used_titles = set()
    for ev, ts in sorted(tok_titles.items(), key=lambda x: (-len(set(x[1])), -len(x[0]))):
        uniq = set(ts)
        if len(uniq) < min_titles:
            continue
        if ev in GENERAL_WORDS or ev in _STOP or is_junk_token(ev):
            continue
        if ev in person_alias_flat:
            continue  # 인물 단독은 path 1 또는 TRACK_PERSONS 줄
        if len(ev) < 3:
            continue
        if n_titles >= 30 and len(uniq) / n_titles > 0.20:
            continue  # 너무 흔한 일반 토큰(충분한 표본일 때만)
        # 이미 인물 클러스터가 가져간 제목만 남은 경우 스킵
        if uniq <= used_titles:
            continue
        name = ev
        clusters.append((name, [ev], None, uniq))

    # 이름 중복 제거
    out_issues, seen_n = [], set()
    for name, kws, must, _ts in clusters:
        if name in seen_n:
            continue
        if is_junk_issue_name(name):
            continue
        kws = [k for k in kws if not is_junk_token(k)]
        if not kws:
            continue
        seen_n.add(name)
        item = {"name": name, "keywords": kws}
        if must:
            item["_must"] = must
        out_issues.append(item)
    return {"issues": out_issues, "persons": []}
