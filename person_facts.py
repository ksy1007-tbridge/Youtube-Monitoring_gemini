"""현직 인물·정당 사실 기준(persons_current.json)을 프롬프트에 주입하고, 결과 텍스트의 직함 오류를 로그로 경고."""
import json
import os
import re
from datetime import datetime, timedelta, timezone

PERSONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "persons_current.json")

# 이름 바로 뒤에 붙는 직함 후보 (긴 것부터 매칭)
_TITLE_WORDS = [
    "전 장관", "전 대표", "전 의원", "전 대통령", "원내대표", "최고위원", "대변인", "위원장",
    "구청장", "도지사", "지사", "시장", "군수", "장관", "의원", "대표", "총리", "대통령",
]
_TITLE_RE = "|".join(re.escape(t) for t in _TITLE_WORDS)
_CAMP_RE = r"(여당|야당|여권|야권)"
# 자치단체장 직함 (본문에 정당명 없이 '이름 직함'으로 표기)
_LOCAL_HEAD_RE = re.compile(r"(도지사|지사|시장|구청장|군수|교육감)$")
_DEFAULT_PARTY_ALIASES = {"더불어민주당": ["더불어민주당", "민주당"], "국민의힘": ["국민의힘", "국힘"]}


def load_person_facts(path: str = PERSONS_FILE) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data.get("persons"), list):
            raise ValueError("persons 목록 없음")
        return data
    except Exception as e:
        print(f"[WARN] {path} 로드 실패 → 정당 기준만 사용: {e}")
        return {"as_of": None, "parties": {"ruling": "더불어민주당", "opposition": "국민의힘"}, "persons": []}


def show_party(person: dict) -> bool:
    """본문에 정당명을 붙일지. 명시값 우선, 없으면 자치단체장 직함이면 False."""
    if "show_party" in person and person["show_party"] is not None:
        return bool(person["show_party"])
    return not _LOCAL_HEAD_RE.search(person.get("title") or "")


def _person_line(p: dict) -> str:
    title = p.get("title")
    party = p.get("party")
    if not title and not party:
        return f"  · {p['name']}: 직함 미상 → 이름만"
    parts = [title] if title else []
    if party:
        parts.append("무소속" if party == "무소속" else f"소속 {party}")
    line = f"  · {p['name']}: {', '.join(parts)}"
    if title and not show_party(p):
        line += f" → 본문 표기는 정당명 없이 '{p['name']} {title}'"
    return line


def build_fact_block(facts: dict = None) -> str:
    facts = facts or load_person_facts()
    parties = facts.get("parties") or {}
    ruling = parties.get("ruling") or "더불어민주당"
    opp = parties.get("opposition") or "국민의힘"
    today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
    lines = [
        f"[사실 기준 — 학습 지식보다 우선, 반드시 준수 (오늘 {today} KST, 명단 기준일 {facts.get('as_of') or '미상'})]",
        f"- 집권 여당: {ruling} / 제1야당: {opp}",
        f"- 정당은 '여당/야당/여권/야권' 대신 정당명({ruling}, {opp})으로 적을 것. "
        f"영상 원문 인용 등으로 불가피하게 쓰면 여당={ruling}, 야당={opp} 의미로만 쓸 것",
        "- 인물 이름 앞뒤에 '여당/야당/여권/야권'을 붙이지 말 것 (예: '야당 ○○○ 의원' 금지)",
        "- 자치단체장(도지사·시장·구청장·군수 등)은 본문에 정당명을 붙이지 말고 '이름 직함'으로만 적을 것 (예: '추미애 경기도지사')",
        "- 아래 명단의 인물은 반드시 명단의 현재 직함·소속으로만 적을 것 ('전 장관', '전 대표' 등 과거 직함 금지):",
    ]
    for p in facts.get("persons") or []:
        if p.get("name"):
            lines.append(_person_line(p))
    lines.append("- 명단에 없는 인물은 영상 제목·설명에 직함이 적혀 있을 때만 그대로 쓰고, 없으면 이름만 적을 것. 직함·당적을 추측하지 말 것")
    return "\n".join(lines)


def audit_person_titles(text: str, where: str = "report", facts: dict = None) -> list:
    """명단 인물의 직함·소속 표기 오류를 [WARN] 로그로 남긴다. 텍스트는 바꾸지 않는다.
    - 현재 직함과 다른 직함 / '전 ○○' 과거 직함
    - 이름에 붙은 여당·야당·여권·야권 라벨
    - 다른 정당명이 이름 앞에 붙은 경우 (예: '국민의힘 한동훈')
    - show_party=false 인물(자치단체장)에 정당명이 붙은 경우 (예: '더불어민주당 추미애')"""
    if not text:
        return []
    facts = facts or load_person_facts()
    aliases = facts.get("party_aliases") or _DEFAULT_PARTY_ALIASES
    warnings = []
    for p in facts.get("persons") or []:
        name = p.get("name")
        title = p.get("title") or ""
        party = p.get("party")
        if not name:
            continue
        n = re.escape(name)
        for m in re.finditer(rf"{n}\s*({_TITLE_RE})", text):
            used = m.group(1)
            if used.startswith("전 ") or used not in title:
                warnings.append(f"'{m.group(0)}' (현재 직함: {title or '미상'})")
        camp_patterns = [
            rf"{_CAMP_RE}\s*(?:소속\s*)?(?:(?:{_TITLE_RE})\s*)?{n}",  # '야당 김민석', '야당 최고위원 양향자'
            rf"{n}\s*{_CAMP_RE}",                                       # '양향자 야당 최고위원'
        ]
        for pat in camp_patterns:
            for m in re.finditer(pat, text):
                warnings.append(f"'{m.group(0)}' (여당/야당 라벨 대신 정당명 사용 필요)")
        for party_name, names in aliases.items():
            pat = "|".join(re.escape(a) for a in sorted(names, key=len, reverse=True))
            for m in re.finditer(rf"({pat})\s*(?:소속\s*)?{n}(?![가-힣])", text):
                if party_name != party:
                    warnings.append(f"'{m.group(0)}' (현재 소속: {party or '미상'})")
                elif not show_party(p):
                    warnings.append(f"'{m.group(0)}' (자치단체장은 정당명 없이 '{name} {title}')")
    for w in dict.fromkeys(warnings):
        print(f"[WARN] 인물 직함 점검({where}): {w}")
    return warnings
