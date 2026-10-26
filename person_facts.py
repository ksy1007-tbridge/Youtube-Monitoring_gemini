"""현직 인물·정당 사실 기준(persons_current.json)을 프롬프트에 주입하고, 결과 텍스트의 직함·여야 표기 오류를 로그로 경고.
두 저장소(Youtube-Monitoring_grok / Youtube-Monitoring_gemini)에 같은 파일을 둔다.

여야 표기 정책: 여당/야당/여권/야권은 쓸 수 있다. 가리키는 대상이 아래 대응표(persons_current.json의 camps)와 맞기만 하면 된다.
  여당 = 더불어민주당 / 야당 = 국민의힘
  여권 = 이재명 정부·더불어민주당·조국혁신당 / 야권 = 국민의힘·개혁신당
  → 조국혁신당은 여권이지만 여당이 아니고, 개혁신당은 야권이지만 야당이 아니다.
  '범여권'은 여권과 같게 본다(예: '범여권 조국혁신당'은 맞음).
인물별 진영: persons_current.json의 camp(여권/야권)가 있으면 그것을, 없으면 party에서 유도한다.
  여당/야당은 정당 표현이므로 그 인물의 party가 여당/야당일 때만 맞다.
  예) 한동훈(무소속, camp=야권): '야권 한동훈'은 맞고 '야당 한동훈'은 경고.
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
_CAMP_RE = r"(범여권|여당|야당|여권|야권)"
# '범여권'은 판정 시 '여권'으로 본다
_CAMP_NORMALIZE = {"범여권": "여권"}
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


def person_allowed_camps(person: dict, facts: dict) -> set:
    """인물이 맞게 불릴 수 있는 여야 표현 집합.
    - 여권/야권: camp 필드가 있으면 그것, 없으면 party에서 유도
    - 여당/야당: 정당 표현이므로 party가 여당/야당일 때만 (무소속은 해당 없음)"""
    party = person.get("party")
    from_party = allowed_camps(party, facts) if party and party != "무소속" else set()
    camp = person.get("camp")
    if camp in ("여권", "야권"):
        return {camp} | {c for c in from_party if c in ("여당", "야당")}
    return from_party


# 여야 표현과 인물 이름 사이에 올 수 있는 사이말 (짧은 창: 최대 3개, 문장 경계·조사 '을/를/이/가/은/는'은 넘지 않음)
_FILLER_RE = (r"(?:인사(?:인|이자|로서)?|소속(?:인)?|출신(?:인)?|정치인|중진|인물|"
              r"(?:대표\s*)?주자|잠룡|(?:전\s*)?[가-힣]{0,3}(?:" + "|".join(re.escape(t) for t in _TITLE_WORDS) + r")(?:인)?|"
              r"더불어민주당|민주당|국민의힘|국힘|조국혁신당|개혁신당|무소속)")


def _person_camp_before_re(name_re: str) -> str:
    """'<여야표현>[인|이자] [사이말 0~3개] <이름>' — 예: '야당 김민석', '야권 인사인 추미애'."""
    return (rf"(?<![가-힣]){_CAMP_RE}(?:인|이자|의)?"
            rf"(?:[ \t]*{_FILLER_RE}){{0,3}}[ \t]*{name_re}(?![가-힣]*(?:을|를)\s*(?:비판|공격|겨냥))")


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
    if p.get("camp"):
        parts.append(f"{p['camp']} 인사")
    line = f"  · {p['name']}: {', '.join(parts)}"
    if p.get("preferred"):
        line += f" → 표기 '{p['preferred']}'"
    elif title and not show_party(p):
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
    lines.append("  · '범여권'은 여권과 같은 뜻으로 써도 됨 (예: '범여권 조국혁신당')")
    lines.append("  · 여당/야당은 정당을 가리키므로 무소속 인물에게 쓰지 말 것 (진영은 여권/야권으로)")
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
        # 정당 직책자(직함이 '<정당> 대표/최고위원' 등)를 '<정당> 인사(인) 이름'으로 낮춰 부른 경우
        #  예: '국민의힘 인사인 장동혁' (현재 직함: 국민의힘 대표). 뒤에 현재 직함이 바로 붙으면 정상.
        if party and party != "무소속" and title.startswith(party):
            cur = re.escape(title[len(party):].strip())
            for m in re.finditer(rf"{_alt(aliases.get(party) or [party])}\s*인사(?:인|이자)?\s*{n}(?!\s*{cur})", text):
                warnings.append(f"'{m.group(0)}' (현재 직함: {title})")
            # '<정당> <다른 직함> 이름' (예: '국민의힘 최고위원 장동혁')
            for m in re.finditer(rf"{_alt(aliases.get(party) or [party])}\s*({_TITLE_RE})\s*{n}(?![가-힣])", text):
                if m.group(1).startswith("전 ") or m.group(1) not in title:
                    warnings.append(f"'{m.group(0)}' (현재 직함: {title})")
        ok_camps = person_allowed_camps(p, facts)
        if ok_camps:   # 진영을 판단할 수 없는 인물(무소속이고 camp 없음 등)은 건너뜀
            basis = f"진영 {p['camp']}" if p.get("camp") else f"소속 {party}"
            for pat in (_person_camp_before_re(n),                                    # '야당 김민석', '야권 인사인 추미애 경기도지사'
                        rf"{n}\s*{_CAMP_RE}(?!\s*(?:내|안|에서|에서도|의|과|와|을|를))"):  # '양향자 야당 최고위원'
                for m in re.finditer(pat, text):
                    used = _CAMP_NORMALIZE.get(m.group(1), m.group(1))
                    if used not in ok_camps:
                        warnings.append(f"'{m.group(0)}' ({name} {basis} → {'/'.join(sorted(ok_camps))})")
        for party_name, names in aliases.items():
            if party_name == "이재명 정부":
                continue
            for m in re.finditer(rf"({_alt(names)})\s*(?:소속\s*)?{n}(?![가-힣])", text):
                if party_name != party:
                    warnings.append(f"'{m.group(0)}' (현재 소속: {party or '미상'})")
    for w in dict.fromkeys(warnings):
        print(f"[WARN] 인물 직함 점검({where}): {w}")
    return list(dict.fromkeys(warnings))


# ---------------------------------------------------------------------------
# 이슈명 직함 누락 점검·보정 (preferred 표기가 있는 인물만; 이슈명에만 적용, 본문은 바꾸지 않음)
#  예: '장동혁 욕설' → '국민의힘 장동혁 대표 욕설' (preferred = '국민의힘 장동혁 대표')
#  '장동혁 대표 욕설', '국민의힘 대표 장동혁 욕설'처럼 직함이 이미 붙은 경우는 그대로 둔다.
# ---------------------------------------------------------------------------
def _bare_name_re(p: dict, facts: dict) -> str:
    n = re.escape(p["name"])
    party = p.get("party")
    party_names = (_aliases(facts).get(party) or [party]) if party and party != "무소속" else []
    party_pre = rf"(?:{_alt(party_names)}\s*)?" if party_names else ""
    # 뒤에 직함이 붙지 않은 이름 (앞 직함 여부는 _has_title_before로 따로 본다. 예: '국민의힘 대표 장동혁')
    return rf"(?<![가-힣]){party_pre}{n}(?!\s*(?:{_TITLE_RE}))"


def _has_title_before(text: str, start: int) -> bool:
    return bool(re.search(rf"(?:{_TITLE_RE})\s*$", text[:start]))


def audit_missing_titles(text: str, where: str = "auto_issue_name", facts: dict = None) -> list:
    """preferred 표기가 있는 인물이 직함 없이 쓰인 곳을 [WARN] 로그로 남긴다(텍스트는 바꾸지 않음)."""
    if not text:
        return []
    facts = facts or load_person_facts()
    warnings = []
    for p in facts.get("persons") or []:
        if not p.get("preferred") or p["name"] not in text:
            continue
        for m in re.finditer(_bare_name_re(p, facts), text):
            if not _has_title_before(text, m.start()):
                warnings.append(f"'{m.group(0).strip()}' (현재 직함 누락 → {p['preferred']})")
    for w in dict.fromkeys(warnings):
        print(f"[WARN] 인물 직함 점검({where}): {w}")
    return list(dict.fromkeys(warnings))


def apply_preferred_title(name: str, facts: dict = None) -> str:
    """이슈명 속 직함 없는 인물명을 preferred 표기로 바꾼다. 예: '장동혁 욕설' → '국민의힘 장동혁 대표 욕설'."""
    if not name:
        return name
    facts = facts or load_person_facts()
    out = name
    for p in facts.get("persons") or []:
        pref = p.get("preferred")
        if not pref or p["name"] not in out:
            continue
        m = next((m for m in re.finditer(_bare_name_re(p, facts), out) if not _has_title_before(out, m.start())), None)
        if m:
            out = out[:m.start()] + pref + out[m.end():]
    return re.sub(r"\s+", " ", out).strip()


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
            if "여권" in wrong:
                wrong = ["범여권"] + wrong   # '범여권'은 여권과 같게 판정 (긴 것 먼저)
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


# ---------------------------------------------------------------------------
# 텔레그램 리포트 하단 점검 꼬리말 (경고가 있을 때만; 본문은 바꾸지 않음)
# ---------------------------------------------------------------------------
TELEGRAM_MAX_LEN = 4096
_WARN_RE = re.compile(r"^'(?P<quote>.*)' \((?P<detail>.*)\)$", re.S)
_SECRET_LIKE_RE = re.compile(r"(AIza[0-9A-Za-z_\-]{10,}|bot\d+:[0-9A-Za-z_\-]{20,}|gh[pousr]_[0-9A-Za-z]{20,}|github_pat_\w+|\b\d{6,}:[0-9A-Za-z_\-]{20,})")


def _short(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _footer_item(warning: str, quote_max: int = 20) -> str:
    """"'장동혁 의원' (현재 직함: 국민의힘 대표)" → "장동혁 의원 → 국민의힘 대표 확인 필요"."""
    m = _WARN_RE.match(warning or "")
    if not m:
        return _short(warning, quote_max + 16) + " 확인 필요"
    detail = m.group("detail")
    want = detail.rsplit("→", 1)[-1] if "→" in detail else detail.split(":", 1)[-1]
    return f"{_short(m.group('quote'), quote_max)} → {_short(want, 16)} 확인 필요"


def _footer_safe(s: str, mode: str) -> str:
    s = _SECRET_LIKE_RE.sub("***", s)
    if mode == "html":
        import html as _html
        return _html.escape(s, quote=False)
    if mode == "markdown":   # 텔레그램 legacy Markdown 특수문자 제거 (파싱 오류 방지)
        return re.sub(r"[_*`\[\]]", "", s)
    return s


def format_audit_footer(title_warnings=None, camp_warnings=None, max_items: int = 3,
                        max_len: int = 300, mode: str = "plain") -> str:
    """직함·여야 점검 경고를 한 줄 꼬리말로. 경고가 없으면 ''(꼬리말 없음).
    중복 제거, 최대 max_items건 + '외 N건', 전체 max_len자 이하. 긴 인용·비밀값은 넣지 않는다."""
    title_items, camp_items = [], []
    for w in list(title_warnings or []) + list(camp_warnings or []):
        if not w:
            continue
        m = _WARN_RE.match(w)
        is_title = bool(m) and m.group("detail").startswith("현재")   # '현재 직함/소속: …' → 직함, 나머지(→ 진영) → 여야
        (title_items if is_title else camp_items).append(_footer_item(w))
    labels = [lbl for lbl, it in (("직함 점검", title_items), ("여야 점검", camp_items)) if it]
    if not labels:
        return ""
    label = " · ".join(labels)
    flat = list(dict.fromkeys(title_items + camp_items))
    total = len(flat)
    shown = max(1, min(max_items, total))
    while True:
        rest = total - shown
        line = f"⚠ {label}: " + "; ".join(flat[:shown]) + (f" 외 {rest}건" if rest else "")
        if len(line) <= max_len or shown == 1:
            break
        shown -= 1
    line = _short(line, max_len)
    return _footer_safe(line, mode)


def append_footer_to_chunks(chunks, footer: str, max_len: int = TELEGRAM_MAX_LEN, sep: str = "\n\n") -> list:
    """분할된 메시지 목록의 마지막 조각에 꼬리말을 붙인다. 넘치면 꼬리말만 별도 조각으로(그래도 넘치면 잘라냄).
    footer가 비어 있으면 입력을 그대로 돌려준다(경고 없는 날 출력 동일)."""
    chunks = list(chunks or [])
    if not footer:
        return chunks
    footer = footer if len(footer) <= max_len else footer[: max_len - 1] + "…"
    if chunks and len(chunks[-1]) + len(sep) + len(footer) <= max_len:
        chunks[-1] = chunks[-1] + sep + footer
    else:
        chunks.append(footer)
    return chunks
