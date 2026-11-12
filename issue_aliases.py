# -*- coding: utf-8 -*-
"""이름 사전(issue_aliases.json) — 라벨 전용. 영상 배정·이슈 채택에 쓰지 않는다.

양쪽 저장소에 같은 파일을 둔다. 사전 추가는 소연 확인 후에만(코드가 파일을 고치지 않음).
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

from issue_tracker import name_tokens, strip_tag

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "issue_aliases.json")

# 표시명에서 라벨 바늘로 쓰지 않는 일반 토막
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


def _text_has(needle: str, parts) -> bool:
    if not needle or len(needle) < 2:
        return False
    return any(needle == p or needle in (p or "") or ((p or "") and len(p) >= 2 and p in needle)
               for p in parts if p)


def label_issue(name: str, keywords=None, aliases_map=None) -> str:
    """묶음이 이미 만들어진 뒤에만 호출. 이슈명·키워드가 사전 별칭(또는 표시명의 실체 토막)을
    문자열로 포함할 때만 그 표시명을 쓴다. 토큰 부분일치('조작'⊂'조작기소')만으로는 붙이지 않음.

    성남라인만 있는 묶음은 '강훈식·성남라인'에 붙이지 않는다(시드에 성남라인 별칭 없음,
    그리고 묶음에 강훈식이 있어야 함).
    """
    aliases_map = aliases_map if aliases_map is not None else load_aliases()
    base = strip_tag(name)
    parts = [base] + [str(k) for k in (keywords or []) if k]
    if not any(parts) or not aliases_map:
        return base
    hits = []
    for display, als in aliases_map.items():
        if display == "강훈식·성남라인":
            if not _text_has("강훈식", parts):
                continue
            needles = ["강훈식"] + list(als or [])
        else:
            needles = list(als or []) + [display] + [
                t for t in name_tokens(display) if t not in _WEAK_LABEL_TOKS and len(t) >= 3
            ]
        if any(_text_has(n, parts) for n in needles):
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
