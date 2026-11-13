# -*- coding: utf-8 -*-
"""이름 사전(issue_aliases.json) — 라벨 전용. 영상 배정·이슈 채택에 쓰지 않는다.

양쪽 저장소에 같은 파일을 둔다. 사전 추가는 소연 확인 후에만(코드가 파일을 고치지 않음).

라벨은 인물명과 사건어가 모두 맞을 때만 붙인다. 짧거나 모호한 별칭(삼성전자, 최민희,
조작 폭로 등)은 쓰지 않는다.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

from issue_tracker import leading_person, name_tokens, person_set, strip_tag

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "issue_aliases.json")

# 표시명에서 사건 바늘로 쓰지 않는 일반 토막 (인물+사건 AND의 사건어는 예외적으로 허용)
_WEAK_LABEL_TOKS = frozenset({
    "논란", "공방", "갈등", "의혹", "문제", "사태", "관련", "당내", "조작",
})


@lru_cache(maxsize=4)
def load_aliases(path: str = None) -> dict:
    """{표시명: [별칭, ...]}."""
    path = path or _DEFAULT_PATH
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"ℹ️ [이름 사전] 파일 없음: {path}")
        return {}
    out = {}
    for name, als in (data or {}).items():
        if not name:
            continue
        out[str(name)] = [str(a) for a in (als or []) if a and str(a).strip()]
    return out


def clear_alias_cache():
    load_aliases.cache_clear()


def alias_terms(display: str, aliases=None) -> list:
    als = list(aliases or [])
    return list(dict.fromkeys([display] + als + [t for t in name_tokens(display) if t not in _WEAK_LABEL_TOKS]))


def _specificity(display: str, aliases=None) -> tuple:
    """더 구체적(인물+사건, 더 긴 토큰)인 쪽을 고르기 위한 키."""
    toks = name_tokens(display)
    has_person_event = len(toks) >= 2
    longest = max((len(t) for t in toks + list(aliases or [])), default=0)
    return (int(has_person_event), longest, len(toks))


def _needle_in_parts(needle: str, parts) -> bool:
    """별칭·사건어 전체가 이슈명/키워드에 들어 있을 때만. 짧은 키워드가 긴 별칭에 포함되는 역방향은 쓰지 않음."""
    if not needle or len(str(needle).strip()) < 2:
        return False
    n = str(needle)
    return any(n == (p or "") or n in (p or "") for p in parts if p)


def _raw_toks(text: str) -> list:
    import re
    return [t for t in re.split(r"[\s/·,()\[\]'\"‘’“”]+", strip_tag(text or "")) if len(t) >= 2]


def person_and_events(display: str, aliases=None, persons=None):
    """표시명의 대표 인물(없으면 '')과 사건어 목록."""
    persons = persons or person_set()
    lp = leading_person(display, persons)
    events = []
    blobs = list(aliases or []) + [display]
    for a in blobs:
        if not a or a == lp:
            continue
        rest = a
        if lp:
            rest = a.replace(lp, " ")
        for tok in _raw_toks(rest):
            if tok != lp and len(tok) >= 2:
                events.append(tok)
        stripped = " ".join(_raw_toks(rest)).strip()
        if stripped and stripped != lp and len(stripped) >= 2:
            events.append(stripped)
    return lp, list(dict.fromkeys(events))


def label_issue(name: str, keywords=None, aliases_map=None) -> str:
    """묶음이 이미 만들어진 뒤에만 호출.

    인물이 있는 표시명(김어준 논란, 조희대/사법부 등)은 이슈명에 그 인물이 있고
    사건어도 이슈명·키워드에 있을 때만 붙인다. 사건만 있는 표시명(여론조사 조작 공방,
    부동산·증시 등)은 짧지 않은 별칭이 문자열로 포함될 때만 붙인다.
    """
    aliases_map = aliases_map if aliases_map is not None else load_aliases()
    base = strip_tag(name)
    parts = [base] + [str(k) for k in (keywords or []) if k]
    if not any(parts) or not aliases_map:
        return base
    persons = person_set()
    name_parts = [base]
    hits = []
    for display, als in aliases_map.items():
        lp, events = person_and_events(display, als, persons)
        if lp:
            if not _needle_in_parts(lp, name_parts):
                continue
            name_lp = leading_person(base, persons)
            # 이슈명의 대표 인물이 다르면 붙이지 않음 (한덕수 석방 및 조희대 탄핵 ⊄ 조희대/사법부)
            if name_lp and name_lp != lp:
                continue
            ev_needles = [e for e in events if e]
            if not ev_needles or not any(_needle_in_parts(e, parts) for e in ev_needles):
                continue
            needles = ev_needles
        else:
            needles = []
            for n in list(als or []) + [display]:
                if n and len(n.replace(" ", "")) >= 2 and n not in _WEAK_LABEL_TOKS:
                    needles.append(n)
            for t in _raw_toks(display):
                if t not in _WEAK_LABEL_TOKS and t not in persons and len(t) >= 3:
                    needles.append(t)
            needles = list(dict.fromkeys(needles))
            if not needles or not any(_needle_in_parts(n, parts) for n in needles):
                continue
        hits.append((_specificity(display, als), len(max(needles, key=len) if needles else ""), display))
    if not hits:
        return base
    hits.sort(reverse=True)
    return hits[0][2]


def aliases_for_qa(display_name: str, extracted_keywords=None, aliases_map=None) -> list:
    """Q&A KV aliases: 표시명의 사전 별칭 + 그날 추출 키워드."""
    aliases_map = aliases_map if aliases_map is not None else load_aliases()
    display = strip_tag(display_name)
    out = []
    if display in aliases_map:
        out.extend(aliases_map[display])
    for k in extracted_keywords or []:
        if k and k not in out and k != display:
            out.append(k)
    return list(dict.fromkeys(out))
