"""'주요 이슈 언급' 하이브리드(고정 목록 + 자동 추출) 보조 모듈.
두 저장소(Youtube-Monitoring_gemini / Youtube-Monitoring_grok)에 같은 파일을 둔다. LLM 호출 없음(순수 로직).

- merge_auto_into_fixed: 고정 칸 흡수는 제거. 자동끼리 키워드·이름 겹침만 합침
- check_issue_name: 자동 이슈명을 점검 — 선정적·의문형 표현(썸네일 문구)이면 다른 후보로 교체, 인물명 단독은 이슈명으로 쓰지 않음,
  직함 없는 인물명은 preferred 표기로 보정(예: '장동혁 욕설' → '국민의힘 장동혁 대표 욕설'), 직함·여야 경고가 남으면 다음 후보, 다 실패하면 제외
- 이력(issue_history): 날짜별 자동 이슈 기록 → 최근 7일 중 4일 이상 등장하면 '이름 사전 별칭 추가 제안'(소연 확인 후만 편입)
"""
import re
from datetime import datetime, timedelta

from person_facts import audit_person_titles, audit_party_camp, apply_preferred_title, load_person_facts

AUTO_TAG = " (자동)"
HISTORY_VERSION = 1
HISTORY_KEEP_DAYS = 14
CANDIDATE_WINDOW_DAYS = 7
CANDIDATE_MIN_DAYS = 4          # '3일 초과' 지속
_NAME_SPLIT_RE = re.compile(r"[\s/·,()\[\]'\"‘’“”]+")
_TOKEN_STOPWORDS = {
    "논란", "의혹", "공방", "문제", "사태", "파문", "이슈", "관련", "발언", "대응", "비판", "논쟁",
    "대통령", "의원", "대표", "장관", "정부", "국회", "민주당", "국민의힘", "자동",
}


# 라벨·겹침 통합에서 인물을 가리키는 이름. TRACK_PERSONS와 별개(한덕수·윤석열은 추적 인물이 아님).
KNOWN_PERSONS = frozenset({
    "정청래", "김민석", "김어준", "유시민", "이재명", "최민희", "백낙청", "송영길",
    "이석현", "한민수", "최강욱", "이성윤", "정봉주", "한동훈", "김지용", "장동혁",
    "강훈식", "조희대", "추미애", "오세훈", "트럼프", "Trump",
    "한덕수", "윤석열", "젠슨 황", "젠슨황", "이용철",
})


def person_set():
    """이슈명에서 인물을 찾을 때 쓰는 이름 집합."""
    names = set(KNOWN_PERSONS)
    try:
        for p in (load_person_facts().get("persons") or []):
            n = (p.get("name") or "").strip()
            if n:
                names.add(n)
            for a in p.get("aliases") or []:
                a = (a or "").strip()
                if a and len(a) >= 2:
                    names.add(a)
    except Exception:
        pass
    return names


def leading_person(text: str, persons=None) -> str:
    """텍스트에 가장 먼저 나오는 인물명. 같은 자리면 더 긴 이름을 고른다."""
    persons = persons if persons is not None else person_set()
    t = strip_tag(text or "")
    found = []
    for p in persons:
        if not p or len(p) < 2:
            continue
        i = t.find(p)
        if i >= 0:
            found.append((i, -len(p), p))
    if not found:
        return ""
    found.sort()
    return found[0][2]


def strip_tag(name: str) -> str:
    name = (name or "").strip()
    return name[: -len(AUTO_TAG)].strip() if name.endswith(AUTO_TAG) else name


def name_tokens(name: str) -> list:
    toks = [t for t in _NAME_SPLIT_RE.split(strip_tag(name)) if len(t) >= 2 and t not in _TOKEN_STOPWORDS]
    return list(dict.fromkeys(toks))


def _terms(name: str, keywords) -> list:
    return list(dict.fromkeys([k.strip() for k in (keywords or []) if k and len(k.strip()) >= 2] + name_tokens(name)))


def _overlap_score(terms_a, terms_b) -> int:
    """키워드/별칭 겹침 수: 같은 문자열이거나 한쪽이 다른 쪽을 포함(예: '조희대 탄핵' ⊃ '조희대').
    짧은 부분일치는 통합 판단에 쓰지 않는다. 하위 호환·디버그용으로 남겨 둔다."""
    score = 0
    for a in terms_a:
        if any(a == b or (len(b) >= 2 and b in a) or (len(a) >= 2 and a in b) for b in terms_b):
            score += 1
    return score


def _shared_terms(terms_a, terms_b):
    """정확한 일치, 또는 4글자 이상 구가 서로 포함될 때만 공유로 본다."""
    shared = set()
    for a in terms_a:
        for b in terms_b:
            if a == b:
                shared.add(a)
            elif len(a) >= 4 and len(b) >= 4 and (a in b or b in a):
                shared.add(a if len(a) <= len(b) else b)
    return shared


def issues_should_merge(name_a, kws_a, name_b, kws_b) -> bool:
    """자동 이슈를 합칠지. 대표 인물이 다르면 합치지 않는다(한덕수 석방 ⊄ 조희대/사법부).
    같은 인물이면 공유 사건어가 있어야 하고, 인물이 없으면 공유 실체어가 2개 이상(또는 4글자+)이어야 한다."""
    pa = leading_person(strip_tag(name_a))
    pb = leading_person(strip_tag(name_b))
    if pa and pb and pa != pb:
        return False
    shared = _shared_terms(_terms(name_a, kws_a), _terms(name_b, kws_b))
    persons = person_set()
    if pa and pa == pb:
        return any(t != pa and t not in persons for t in shared)
    if len(shared) >= 2:
        return True
    return any(len(t) >= 4 for t in shared)


def merge_auto_into_fixed(auto_issues: dict, fixed_issues: dict = None, extra_fixed_terms: dict = None):
    """자동끼리 키워드·이름이 겹치면 하나로 합친다(조건을 더 엄격히).
    fixed_issues·extra_fixed_terms는 호환을 위해 받지만 사용하지 않는다(고정 칸 흡수 분기 제거).
    반환: (남은 자동 이슈 {이름: 키워드}, 흡수 내역 {남은 이슈명: [합쳐진 이슈명...]})."""
    absorbed, merged = {}, {}
    for name, kws in (auto_issues or {}).items():
        target = next((m for m, mk in merged.items() if issues_should_merge(name, kws, m, mk)), None)
        if target:
            merged[target] = list(dict.fromkeys(merged[target] + list(kws)))
            absorbed.setdefault(target, []).append(name)
        else:
            merged[name] = list(kws)
    return merged, absorbed


# 이슈명으로 쓰면 안 되는 선정적·비유적 표현 (썸네일 문구가 사건명처럼 읽히는 것을 막음)
SENSATIONAL_TERMS = (
    "무릎", "꿇", "굴복", "몰락", "충격", "경악", "폭로전", "발칵", "박살", "참패", "망했", "망한", "멘붕",
    "대폭발", "끝장", "개망신", "처참", "소름", "난리", "아수라장", "초토화", "폭망", "탈탈", "작살", "화형",
)
_SENSATIONAL_MARK_RE = re.compile(r"[?？!！\"“”‘’']")


def is_sensational(name: str) -> bool:
    """선정적 어휘, 의문·감탄 부호, 따옴표 인용이 있으면 True."""
    base = strip_tag(name)
    return bool(_SENSATIONAL_MARK_RE.search(base)) or any(t in base for t in SENSATIONAL_TERMS)


def check_issue_name(name: str, keywords=None, facts: dict = None, person_names=None):
    """자동 이슈명 점검. 통과한 이름(태그 제외, 필요하면 직함 보정) 또는 None.
    후보 순서: 이슈명 → 키워드. 선정적 표현·인물명 단독 후보는 건너뛴다."""
    facts = facts or load_person_facts()
    persons = set(person_names or []) | {p.get("name") for p in facts.get("persons") or [] if p.get("name")}
    base = strip_tag(name)
    for cand in [base] + [k for k in (keywords or []) if k and k != base]:
        cand = (cand or "").strip()
        if not cand or cand in persons:
            continue
        if is_sensational(cand):
            print(f"[WARN] 이슈명 선정성 점검(auto_issue_name): '{cand}' → 제외")
            continue
        fixed = apply_preferred_title(cand, facts)
        if fixed != cand:
            print(f"[INFO] 이슈명 직함 보정(auto_issue_name): '{cand}' → '{fixed}'")
        if not (audit_person_titles(fixed, "auto_issue_name", facts) or audit_party_camp(fixed, "auto_issue_name", facts)):
            return fixed
    return None


# ---------------- 날짜별 이력 ----------------
def empty_history() -> dict:
    return {"version": HISTORY_VERSION, "days": {}}


def normalize_history(hist) -> dict:
    if not isinstance(hist, dict) or not isinstance(hist.get("days"), dict):
        return empty_history()
    return {"version": HISTORY_VERSION, "days": {d: v for d, v in hist["days"].items() if isinstance(v, dict)}}


def update_history(hist, date_str: str, auto_rows: dict, keep_days: int = HISTORY_KEEP_DAYS) -> dict:
    """auto_rows: {자동 이슈명(태그 제외): {"keywords": [...], "cnt": n}}. 같은 날 재실행은 덮어씀."""
    hist = normalize_history(hist)
    hist["days"][date_str] = {strip_tag(n): {"keywords": list(v.get("keywords") or []), "cnt": int(v.get("cnt") or 0)}
                              for n, v in (auto_rows or {}).items()}
    cutoff = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=keep_days - 1)).strftime("%Y-%m-%d")
    hist["days"] = {d: v for d, v in sorted(hist["days"].items()) if d >= cutoff}
    return hist


def persistent_candidates(hist, date_str: str, window: int = CANDIDATE_WINDOW_DAYS,
                          min_days: int = CANDIDATE_MIN_DAYS) -> list:
    """오늘 자동 이슈 중 최근 window일(오늘 포함) 동안 min_days일 이상 나온 것.
    날마다 이름이 조금 달라도 이름·키워드가 겹치면 같은 이슈로 본다. 반환: [{"name", "days", "dates"}]."""
    hist = normalize_history(hist)
    today = hist["days"].get(date_str) or {}
    start = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=window - 1)).strftime("%Y-%m-%d")
    out = []
    for name, info in today.items():
        terms = _terms(name, info.get("keywords"))
        dates = [d for d, items in sorted(hist["days"].items()) if start <= d <= date_str and any(
            n == name or _overlap_score(terms, _terms(n, v.get("keywords"))) for n, v in items.items())]
        if len(dates) >= min_days:
            out.append({"name": name, "days": len(dates), "dates": dates})
    return sorted(out, key=lambda x: -x["days"])


def format_candidates(cands) -> str:
    return ", ".join(f"{c['name']}({c['days']}일)" for c in cands)
