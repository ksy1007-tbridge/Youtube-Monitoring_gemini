"""현직 인물·정당 사실 기준(persons_current.json)을 프롬프트에 주입하고, 결과 텍스트의 직함·여야 표기 오류를 로그로 경고.
두 저장소(Youtube-Monitoring_grok / Youtube-Monitoring_gemini)에 같은 파일을 둔다.

여야 표기 정책: 여당/야당/여권/야권은 쓸 수 있다. 가리키는 대상이 아래 대응표(persons_current.json의 camps)와 맞기만 하면 된다.
  여당 = 더불어민주당 / 야당 = 국민의힘
  여권 = 이재명 정부·더불어민주당·조국혁신당 / 야권 = 국민의힘·개혁신당
  → 조국혁신당은 여권이지만 여당이 아니고, 개혁신당은 야권이지만 야당이 아니다.
    따라서 '여당 조국혁신당', '야당 개혁신당'도 엄격 대응표 기준으로 경고한다(로그 경고일 뿐 텍스트는 바꾸지 않음).
자치단체장은 통상 직책만 쓰지만(예: '추미애 경기도지사'), 정당·여야 표현을 덧붙여도 경고하지 않는다.
"""
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
_CAMP_WORDS = ("여당", "야당", "여권", "야권")
_CAMP_RE = r"(여당|야당|여권|야권)"
# 자치단체장 직함 (통상 '이름 직함'으로 표기)
_LOCAL_HEAD_RE = re.compile(r"(도지사|지사|시장|구청장|군수|교육감)$")
_DEFAULT_CAMPS = {
    "여당": ["더불어민주당"],
    "야당": ["국민의힘"],
    "여권": ["이재명 정부", "더불어민주당", "조국혁신당"],
    "야권": ["국민의힘", "개혁신당"],
}
_DEFAULT_PARTY_ALIASES = {
    "더불어민주당": ["더불어민주당", "민주당"], "국민의힘": ["국민의힘", "국힘"],
    "조국혁신당": ["조국혁신당"], "개혁신당": ["개혁신당"], "이재명 정부": ["이재명 정부"],
}


def load_person_facts(path: str = PERSONS_FILE) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data.get("persons"), list):
            raise ValueError("persons 목록 없음")
        return data
    except Exception as e:
        print(f"[WARN] {path} 로드 실패 → 여야 대응표 기본값만 사용: {e}")
        return {"as_of": None, "parties": {"ruling": "더불어민주당", "opposition": "국민의힘"},
                "camps": _DEFAULT_CAMPS, "persons": []}


def _camps(facts: dict) -> dict:
    return facts.get("camps") or _DEFAULT_CAMPS


def _aliases(facts: dict) -> dict:
    return facts.get("party_aliases") or _DEFAULT_PARTY_ALIASES


def allowed_camps(entity: str, facts: dict) -> set:
    """정당·주체가 맞게 불릴 수 있는 여야 표현 집합. 예: 조국혁신당 → {'여권'}."""
    return {camp for camp, members in _camps(facts).items() if entity in (members or [])}


def show_party(person: dict) -> bool:
    """기본 표기 힌트: 명시값 우선, 없으면 자치단체장 직함이면 False(통상 직책만). 금지 규칙이 아니다."""
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
        line += f" → 통상 표기 '{p['name']} {title}'"
    return line


def build_fact_block(facts: dict = None) -> str:
    facts = facts or load_person_facts()
    camps = _camps(facts)
    today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
    lines = [
        f"[사실 기준 — 학습 지식보다 우선, 반드시 준수 (오늘 {today} KST, 명단 기준일 {facts.get('as_of') or '미상'})]",
        "- 여야 표현은 아래 대응에 맞게 쓸 것 (정당명을 그대로 써도 됨):",
    ]
    for camp in _CAMP_WORDS:
        if camps.get(camp):
            lines.append(f"  · {camp} = {', '.join(camps[camp])}")
    lines += [
        "- 자치단체장(도지사·시장·구청장·군수 등)은 통상 직책만 쓸 것 (예: '추미애 경기도지사')",
        "- 아래 명단의 인물은 명단의 현재 직함·소속으로 적을 것 ('전 장관', '전 대표' 등 과거 직함 금지):",
    ]
    for p in facts.get("persons") or []:
        if p.get("name"):
            lines.append(_person_line(p))
    lines.append("- 명단에 없는 인물은 영상 제목·설명에 직함이 적혀 있을 때만 그대로 쓰고, 없으면 이름만 적을 것. 직함·당적을 추측하지 말 것")
    return "\n".join(lines)


def _alt(names) -> str:
    return "(?:" + "|".join(re.escape(a) for a in sorted(names, key=len, reverse=True)) + ")"


def audit_person_titles(text: str, where: str = "report", facts: dict = None) -> list:
    """명단 인물의 직함·소속 표기 오류를 [WARN] 로그로 남긴다. 텍스트는 바꾸지 않는다.
    - 현재 직함과 다른 직함 / '전 ○○' 과거 직함 (예: '추미애 의원', '한동훈 전 대표')
    - 이름 바로 앞뒤의 여야 표현이 그 인물 소속 정당의 대응과 맞지 않는 경우 (예: '야당 김민석')
    - 다른 정당명이 이름 앞에 붙은 경우 (예: '국민의힘 추미애', '국민의힘 한동훈')
    자치단체장에 정당·여야 표현을 덧붙인 것('더불어민주당 추미애', '여권 추미애')은 경고하지 않는다."""
    if not text:
        return []
    facts = facts or load_person_facts()
    aliases = _aliases(facts)
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
        ok_camps = allowed_camps(party, facts) if party else set()
        if party and party != "무소속":   # 무소속은 여야 대응이 없어 판단하지 않음
            for pat in (rf"{_CAMP_RE}\s*(?:소속\s*)?(?:(?:{_TITLE_RE})\s*)?{n}",   # '야당 김민석', '야당 최고위원 양향자'
                        rf"{n}\s*{_CAMP_RE}(?!\s*(?:내|안|에서|에서도|의|과|와|을|를))"):  # '양향자 야당 최고위원'
                for m in re.finditer(pat, text):
                    if m.group(1) not in ok_camps:
                        warnings.append(f"'{m.group(0)}' ({name} 소속 {party} → {'/'.join(sorted(ok_camps)) or '해당 없음'})")
        for party_name, names in aliases.items():
            if party_name == "이재명 정부":
                continue
            for m in re.finditer(rf"({_alt(names)})\s*(?:소속\s*)?{n}(?![가-힣])", text):
                if party_name != party:
                    warnings.append(f"'{m.group(0)}' (현재 소속: {party or '미상'})")
    for w in dict.fromkeys(warnings):
        print(f"[WARN] 인물 직함 점검({where}): {w}")
    return list(dict.fromkeys(warnings))


# 정당·주체 ↔ 여야 표현 불일치 (동격·나열 표현만 잡아 오탐을 줄임)
#  경고: '더불어민주당 등 야권', '여당 국민의힘', '야권 조국혁신당', '여권 개혁신당', '야당인 더불어민주당', '조국혁신당 등 야당'
#  무경고: '여권 내 비판', '국민의힘 등 야권', '조국혁신당 등 여권', '여당인 더불어민주당', '더불어민주당과 야당',
#          '더불어민주당이 야당을 비판', '야권의 더불어민주당 비판', '… 야당 시절'
_CLAUSE_SPLIT_RE = re.compile(r"[.!?\n;/·,]|—|→| vs ")


def audit_party_camp(text: str, where: str = "report", facts: dict = None) -> list:
    """정당·주체를 대응표와 다른 여야 표현으로 부른 곳을 [WARN] 로그로 남긴다. 텍스트는 바꾸지 않는다."""
    if not text:
        return []
    facts = facts or load_person_facts()
    aliases = _aliases(facts)
    entities = {e for members in _camps(facts).values() for e in (members or [])}
    not_past = r"(?!\s*(?:시절|때|당시))"
    warnings = []
    for clause in _CLAUSE_SPLIT_RE.split(text):
        for entity in entities:
            wrong = [c for c in _CAMP_WORDS if c not in allowed_camps(entity, facts)]
            if not wrong:
                continue
            pn = _alt(aliases.get(entity) or [entity])
            camp = "(?:" + "|".join(wrong) + ")"
            patterns = [
                rf"(?<![가-힣]){pn}\s*(?:등|등의|을 비롯한|를 비롯한|포함한?)\s*{camp}{not_past}",   # 더불어민주당 등 야권
                rf"(?<![가-힣]){pn}\s+{camp}(?=\s*(?:인사|의원|지도부|진영|정당|소속|측|$))",       # 더불어민주당 야권 인사
                rf"(?<![가-힣]){pn}(?:은|는|이|가)\s*{camp}(?:으로서|이다|이지만|인데|입장)",          # 더불어민주당은 야당으로서
                rf"{camp}(?:인|이자)?\s+{pn}(?![가-힣]*(?:을|를)\s*(?:비판|공격|겨냥))",            # 야당인 더불어민주당
            ]
            for pat in patterns:
                for m in re.finditer(pat, clause):
                    ok = "/".join(sorted(allowed_camps(entity, facts))) or "해당 없음"
                    warnings.append(f"'{m.group(0).strip()}' ({entity} → {ok})")
    for w in dict.fromkeys(warnings):
        print(f"[WARN] 정당 여야 표기 점검({where}): {w}")
    return list(dict.fromkeys(warnings))
