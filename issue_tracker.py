"""'주요 이슈 언급' 하이브리드(고정 목록 + 자동 추출) 보조 모듈.
두 저장소(Youtube-Monitoring_gemini / Youtube-Monitoring_grok)에 같은 파일을 둔다. LLM 호출 없음(순수 로직).

- merge_auto_into_fixed: 자동 이슈의 키워드/이름이 고정 이슈 키워드와 겹치면 고정 이슈로 흡수, 자동끼리 겹치면 하나로 합침
- check_issue_name: 자동 이슈명을 person_facts의 직함·여야 점검에 통과시킴 (경고 시 키워드명으로 교체, 그래도 경고면 제외)
- 이력(issue_history): 날짜별 자동 이슈 기록 → 최근 7일 중 4일 이상(3일 초과) 등장하면 '고정 목록 후보'
"""
import re
from datetime import datetime, timedelta

from person_facts import audit_person_titles, audit_party_camp

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


def strip_tag(name: str) -> str:
    name = (name or "").strip()
    return name[: -len(AUTO_TAG)].strip() if name.endswith(AUTO_TAG) else name


def name_tokens(name: str) -> list:
    toks = [t for t in _NAME_SPLIT_RE.split(strip_tag(name)) if len(t) >= 2 and t not in _TOKEN_STOPWORDS]
    return list(dict.fromkeys(toks))


def _terms(name: str, keywords) -> list:
    return list(dict.fromkeys([k.strip() for k in (keywords or []) if k and len(k.strip()) >= 2] + name_tokens(name)))


def _overlap_score(terms_a, terms_b) -> int:
    """키워드/별칭 겹침 수: 같은 문자열이거나 한쪽이 다른 쪽을 포함(예: '조희대 탄핵' ⊃ '조희대')."""
    score = 0
    for a in terms_a:
        if any(a == b or (len(b) >= 2 and b in a) or (len(a) >= 2 and a in b) for b in terms_b):
            score += 1
    return score


def merge_auto_into_fixed(auto_issues: dict, fixed_issues: dict, extra_fixed_terms: dict = None):
    """반환: (남은 자동 이슈 {이름: 키워드}, 흡수 내역 {고정 이슈명: [자동 이슈명...]}).
    흡수된 자동 이슈의 키워드는 고정 이슈에 추가하지 않는다(고정 목록 집계·전일 비교 유지)."""
    extra_fixed_terms = extra_fixed_terms or {}
    fixed_terms = {f: _terms(f, kws) + list(extra_fixed_terms.get(f, [])) for f, kws in fixed_issues.items()}
    absorbed, remaining = {}, {}
    for name, kws in (auto_issues or {}).items():
        terms = _terms(name, kws)
        best, best_score = None, 0
        for f, ft in fixed_terms.items():
            s = _overlap_score(terms, ft)
            if s > best_score:
                best, best_score = f, s
        if best:
            absorbed.setdefault(best, []).append(name)
        else:
            remaining[name] = list(kws)
    # 자동끼리 겹치면 먼저 나온(우선순위 높은) 항목으로 합침
    merged = {}
    for name, kws in remaining.items():
        target = next((m for m, mk in merged.items() if _overlap_score(_terms(name, kws), _terms(m, mk))), None)
        if target:
            merged[target] = list(dict.fromkeys(merged[target] + kws))
            absorbed.setdefault(target, []).append(name)
        else:
            merged[name] = list(kws)
    return merged, absorbed


def check_issue_name(name: str, keywords=None, facts: dict = None):
    """자동 이슈명을 직함·여야 점검에 통과시킨다. 통과한 이름(태그 제외) 또는 None."""
    base = strip_tag(name)
    for cand in [base] + [k for k in (keywords or []) if k and k != base]:
        if not (audit_person_titles(cand, "auto_issue_name", facts) or audit_party_camp(cand, "auto_issue_name", facts)):
            return cand
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
