import os
import json
import requests
import re
import html
from googleapiclient.discovery import build
from datetime import datetime, timedelta, timezone
import pandas as pd
from google import genai

from person_facts import (normalize_report_text, FIXED_DISPLAY, HOMONYMS, homonym_prompt_lines,
                          load_person_facts, build_fact_block, audit_person_titles, audit_party_camp,
                          format_audit_footer, append_footer_to_chunks)
from issue_tracker import (merge_auto_into_fixed, check_issue_name, update_history, persistent_candidates,
                           format_candidates, normalize_history, strip_tag, name_tokens)
from hanun_card import build_hanun_card, issues_snapshot
from issue_defs import build_defs, publish_defs
from issue_auto import (
    DYNAMIC_MAX_ISSUES, DYNAMIC_MAX_PERSONS, DYNAMIC_MIN_TITLES, DYNAMIC_MIN_CHANNELS,
    DYNAMIC_TAG, DYNAMIC_MAX_CHANNEL_SHARE, DYNAMIC_HIGH_VIEW, DYNAMIC_HIGH_VIEW_EXEMPT,
    log_thresholds, is_general_only, specificity_score, summarize_group, adopt_after_assign,
    extract_offline_candidates, passes_thresholds, is_junk_issue_name)
from issue_aliases import load_aliases, label_issue, aliases_for_qa

# ---------------------------------------------------------
# [환경 변수 로드]
# ---------------------------------------------------------
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
# DRY_RUN=1: 텔레그램·KV 미게시, previous_data 미저장 (workflow_dispatch 테스트용)
DRY_RUN = os.getenv("DRY_RUN", "").strip().lower() in ("1", "true", "yes")

# Gemini 3 thinking 설정 (minimal/low/medium/high). thinking_budget은 함께 보내지 않음(400 오류).
_THINKING_LEVELS = {"minimal", "low", "medium", "high"}


def _thinking_from_env(var: str, default: str = "low") -> dict:
    val = (os.getenv(var) or default).strip().lower()
    if val not in _THINKING_LEVELS:
        print(f"⚠️ {var}={val!r} 미지원 → {default} 사용")
        val = default
    return {"thinking_level": val}


GEMINI_ENTITY_THINKING = _thinking_from_env("GEMINI_ENTITY_THINKING")    # 신규 이슈/인물 자동 추출
GEMINI_INSIGHT_THINKING = _thinking_from_env("GEMINI_INSIGHT_THINKING")  # AI 심층 분석(generate_ai_insight)


def _log_gemini_meta(response, label: str, think_cfg: dict) -> None:
    """finish_reason·토큰 사용량만 로그 (본문·키 미출력)."""
    try:
        cands = getattr(response, "candidates", None) or []
        fr = getattr(cands[0], "finish_reason", None) if cands else None
        fr = getattr(fr, "name", fr)
        um = getattr(response, "usage_metadata", None)
        g = lambda k: getattr(um, k, None) if um is not None else None
        print(f"[GEMINI:{label}] finish_reason={fr} tokens: prompt={g('prompt_token_count')} "
              f"output={g('candidates_token_count')} thinking={g('thoughts_token_count')} "
              f"total={g('total_token_count')} (thinking_config={think_cfg})")
        if fr == "MAX_TOKENS":
            print(f"[WARN] Gemini 응답이 토큰 한도에서 잘림 ({label})")
    except Exception:
        pass


def redact_secrets(value) -> str:
    """예외 메시지 등에 섞인 API 키·텔레그램 토큰을 가린다 (로그/텔레그램 출력용)."""
    text = str(value)
    for secret in (YOUTUBE_API_KEY, GEMINI_API_KEY, TELEGRAM_BOT_TOKEN):
        if secret and secret.strip():
            text = text.replace(secret.strip(), "***")
    text = re.sub(r"bot\d+:[A-Za-z0-9_-]+", "bot***", text)
    text = re.sub(r"([?&](?:key|api_key)=)[^&\s'\"]+", r"\1***", text)
    return text

KST = timezone(timedelta(hours=9))
PREV_DATA_FILE = os.environ.get("PREV_DATA_FILE", "previous_data.json")

# 현직 인물·정당 사실 기준 (persons_current.json) — AI 분석 프롬프트에 주입
PERSON_FACTS = load_person_facts()
FACT_BLOCK = build_fact_block(PERSON_FACTS)

TARGET_CHANNELS = {
    "김어준의 겸손은힘들다 뉴스공장": "UCAAvO0ehWox1bbym3rXKBZw",
    "[팟빵] 최욱의 매불쇼": "UCMYhq9OyGI5UEz_NTAoHY7A",
    "스픽스": "UCgeOlLcX6PReHdWImEnUVTg",
    "장윤선의 취재편의점": "UCAVVxLmPDFkSTROPue8ZrRA",
    "[공식] 새날": "UCu1FzjrHosuKGvgIx8oBi8w",
    "이동형TV": "UCd4BxCKyMHG2J0X1SerTPaQ",
    "시사타파TV": "UCzQJmmpZjqzJe96CwlrwlHQ",
    "열린공감TV": "UC4y2Jx26qCb7CrSt_i5bf1A",
    "뉴탐사 NewTamsa": "UCpr8CBjls1XYoSd98d6aT1w",
    "서울의소리 VoiceOfSeoul": "UCUxTPRSns--l5BX2537u7Rw",
    "고발뉴스TV": "UCX7-K_PSdtAiUDLEMQwrRoQ",
    "김용민TV": "UCljnbFCt-4doBr7wtEIIbbw",
    "박시영TV": "UCIMv9bOOGWGIfg6wPcRLItQ",
    "MBC 라디오 시사": "UCTTmtS2ljy1vyl_s-d_LEHQ",
}

# ---------------------------------------------------------
# [추적 인물 - 별칭(alias) 사전]
# 제목_정제 기준 부분 문자열 매칭. 성씨+직함("한 전 대표", "김 청장")이나 한 글자 별칭("李")은 오탐 위험으로 사용하지 않음.
# ---------------------------------------------------------
TRACK_PERSONS = {
    "정청래": ["정청래"],
    "김민석": ["김민석"],
    "김어준": ["김어준"],
    "유시민": ["유시민"],
    "이재명": ["이재명", "이 대통령", "李 대통령", "李대통령"],
    "최민희": ["최민희"],
    "백낙청": ["백낙청"],
    "송영길": ["송영길"],
    "이석현": ["이석현"],
    "한민수": ["한민수"],
    "최강욱": ["최강욱"],
    "이성윤": ["이성윤"],
    "정봉주": ["정봉주"],
    "한동훈": ["한동훈"],
    "김지용": ["김지용", "중수청장"],
    "장동혁": ["장동혁"],
    "강훈식": ["강훈식"],
    "조희대": ["조희대"],
    "추미애": ["추미애"],   # 경기도지사, 여권 인사 (10/21부터 연속 상위 → 고정)
    "오세훈": ["오세훈"],   # 서울시장 (자치단체장, 통상 정당 표기 없음)
}
# 고정 인물 편입 전 '(자동)' 키로 저장된 전일 값을 이어 쓰기 위한 대응 (전일 대비 끊김 방지)
PREV_PERSON_KEY_FALLBACK = {"추미애": "추미애 (자동)", "오세훈": "오세훈 (자동)"}

# [10/27 QA] 증감률 산출 조건: 전일 단독 조회 기준값이 이보다 작거나 없으면 % 대신 '전일 N회 → 금일 M회'만
MIN_PREV_VIEWS_FOR_PCT = 1000
# [10/27 QA] 신작 클립: 업로드 1시간 미만 또는 단독 조회 100회 미만이면 증감률·편중% 대신 '신작' 표기 (삭제하지 않음)
NEW_CLIP_MAX_HOURS = 1.0
NEW_CLIP_MAX_VIEWS = 100
RAW_CSV_DIR = os.environ.get("RAW_CSV_DIR", "raw_csv")


def person_display_name(p: str) -> str:
    """리포트 표시명: 고정 직함(장동혁) · 동명이인 직함(주진우) 반영. 인물 키는 그대로 둔다."""
    base = p.replace(" (자동)", "")
    suffix = p[len(base):]
    if base in FIXED_DISPLAY:
        return FIXED_DISPLAY[base] + suffix
    if base in HOMONYMS:
        return HOMONYMS[base]["yeogwon"] + suffix
    return p


def find_prev_person(p: str, prev_persons: dict):
    """전일 인물 레코드 찾기: 같은 키 → 고정 대응표 → '(자동)' 유무만 다른 키 (유시민 등 증감 누락 방지)."""
    if not prev_persons:
        return None, None
    base = p.replace(" (자동)", "")
    for k in (p, PREV_PERSON_KEY_FALLBACK.get(p), base, f"{base} (자동)"):
        if k and k in prev_persons:
            return k, prev_persons[k]
    return None, None


def person_trend(p_views_standalone: int, prev_rec, is_new_clip: bool):
    """(리포트 표기, AI 입력) — 증감률은 전일 단독 기준값이 충분할 때만."""
    if is_new_clip:
        return " | 🆕신작(업로드 직후·조회 극소, 증감률 산출 제외)", "신작(업로드 직후, 증감률 제외)"
    if prev_rec is None:
        return " | 전일 기록 없음", "전일 기록 없음(증감률 없음)"
    if "views_standalone" not in prev_rec:
        return " | 전일 단독 기준값 없음", "전일 단독 기준값 없음(증감률 없음)"
    prev_v = int(prev_rec.get("views_standalone") or 0)
    if prev_v < MIN_PREV_VIEWS_FOR_PCT:
        return (f" | 전일 {prev_v:,}회 → 금일 {p_views_standalone:,}회",
                f"전일 단독 {prev_v:,}회 -> 금일 단독 {p_views_standalone:,}회 (전일 기준값 작아 증감률 생략)")
    pct = round(((p_views_standalone - prev_v) / prev_v) * 100, 1)
    sign = "+" if pct >= 0 else ""
    return (f" | 전일 {prev_v:,}회 → {sign}{pct}%",
            f"전일 단독 {prev_v:,}회 -> 금일 단독 {p_views_standalone:,}회 ({sign}{pct}%)")


def save_raw_csv(df, now_kst) -> str:
    """수집 원본(중복 제거 전)을 CSV로 저장 — Actions artifact 대조용."""
    try:
        os.makedirs(RAW_CSV_DIR, exist_ok=True)
        path = os.path.join(RAW_CSV_DIR, f"yt_politics_24h_{now_kst.strftime('%Y%m%d_%H%M')}.csv")
        df.drop(columns=[c for c in ("게시일시_dt", "설명") if c in df.columns]).to_csv(path, index=False, encoding="utf-8-sig")
        print(f"ℹ️ 원본 CSV 저장: {path} ({len(df)}행)")
        return path
    except Exception as e:
        print(f"원본 CSV 저장 실패: {redact_secrets(e)}")
        return ""

# 고정 이슈 10칸(ISSUE_KEYWORDS)은 제거됨. 이슈는 자동 추출만으로 산출한다.
# 채택 임계·상한은 issue_auto.py (한곳에서 변경). 이름 사전은 issue_aliases.json (라벨 전용).

# ---------------------------------------------------------
# [프레임 키워드] 위에서부터 첫 매칭 프레임으로 판정 (순서가 우선순위)
# 오탐 방지: 단독 'UN', '안보', '이란', '군', '핵' 등 짧은 키워드는 사용하지 않음
# ---------------------------------------------------------
FRAME_KEYWORDS = {
    "전당대회/경선": ["전당대회", "최고위원", "당대표", "경선", "짝짓기", "투표전략", "경선후보", "토론", "재검표", "부정선거", "윤리위", "당규", "합동연설회", "전국당원대회", "공천", "당권", "쉬쉬하던", "찌라시", "출당"],
    "당내/인물": ["정청래", "김민석", "이재명", "송영길", "박지원", "박은정", "이석현", "신인규", "반명", "친명", "최민희", "스캔들", "친청계", "반명몰이", "민심이반", "팀김어준", "뉴스비평", "신천지", "고소", "고소전", "패악질", "협박", "자업자득", "유시민", "한동훈", "장동혁", "김지용", "강훈식"],
    "외교·안보": ["외교", "순방", "정상회담", "유엔", "UN총회", "UN 연설", "UN순방", "걸프", "중동", "한미", "미중", "한중", "한일", "트럼프", "관세협상", "우크라", "젤렌스키", "러시아", "포로 송환", "포로송환", "포로", "북한", "김정은", "DMZ", "비무장지대", "지뢰", "국방", "합참", "안보실", "국가안보", "NSC"],
    "검찰/사법": ["검찰", "검수완박", "수사권", "공수처", "기소", "수사", "공소취소", "특검", "보완수사권", "조희대", "대법원", "대법관", "법원행정처", "재제청", "사법부", "중수청"],
    "과거정권/윤": ["윤석열", "김건희", "이태원", "내란", "계엄", "윤 정권"],
    "언론/미디어": ["진보언론", "편파보도", "기자회견", "기자 편파", "방송 세탁", "왜곡 보도", "기괴한 언론", "저널리즘"],
    "민생/경제/정책": ["교육", "경제", "민생", "물가", "부동산", "교실", "코스피", "삼성전자", "레버리지", "ETF", "실적발표", "코스닥", "사이드카", "증시", "소상공인", "세제", "투자자", "중복 상장", "주주환원", "집값", "실거주", "주택"],
}

# ---------------------------------------------------------
# [문맥 조건부 별칭] 약칭(trigger)이 문맥어 중 하나와 함께 제목에 있을 때만 해당 인물로 인정
# 예: '희대의 밥값'(청탁금지법) → 조희대 / '희대의 사기꾼' → 미매칭
# ---------------------------------------------------------
CONTEXT_ALIASES = {
    "조희대": [
        ("희대", ["청탁금지법", "밥값", "대법원", "대법원장", "사법", "법원", "탄핵", "김영란법"]),
    ],
}
# 문맥 별칭을 함께 적용할 이슈/프레임 → 인물명 목록
ISSUE_CONTEXT_LINKS = {"조희대/사법부": ["조희대"]}
FRAME_CONTEXT_LINKS = {"검찰/사법": ["조희대"]}

# ---------------------------------------------------------
# [채널 편중 / 자동 추출 설정]
# ---------------------------------------------------------
CONCENTRATION_TOP_SHARE = 50.0      # 최대채널 조회 비중(%) 초과 시 편중
CONCENTRATION_TOP2_VID_SHARE = 60.0  # 상위 2개 영상 조회 비중(%) 초과 시 편중 (영상 3건 이상일 때만)

# DYNAMIC_* 채택 임계는 issue_auto에서 import. 80%는 채택 탈락 기본값 (소연이 7일 표로 확정).
DYNAMIC_SINGLE_CHANNEL_SHARE = DYNAMIC_MAX_CHANNEL_SHARE
DYNAMIC_ALIAS_STOPWORDS = {
    "대통령", "의원", "대표", "장관", "총리", "청장", "위원장", "원장", "여사", "기자", "작가", "의장", "후보",
    "민주당", "국민의힘", "국힘", "정부", "청와대", "대통령실", "여당", "야당", "검찰", "법원", "국회",
}
SURNAME_TITLE_RE = re.compile(
    r"^[가-힣](\s?전)?\s?(대통령|대표|의원|장관|청장|총리|위원장|원장|여사|작가|기자|의장|전 대표|후보)$"
)

OMNIBUS_KEYWORDS = [
    "뉴스공장 2026", "뉴스공장 월요일", "뉴스공장 화요일", "뉴스공장 수요일", "뉴스공장 목요일", "뉴스공장 금요일",
    "[full]", "풀버전", "풀방송", "김용민 브리핑] 아침7시", "뉴스하이킥 full", "시선집중 full", "겸손은힘들다"
]


def parse_iso8601_duration(duration_str):
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def context_alias_hit(title, name) -> bool:
    """CONTEXT_ALIASES[name]의 (trigger, 문맥어) 중 하나라도 trigger와 문맥어가 제목에 함께 있으면 True."""
    if not isinstance(title, str):
        return False
    for trigger, contexts in CONTEXT_ALIASES.get(name, []):
        if trigger in title and any(c in title for c in contexts):
            return True
    return False


def context_mask(series, names):
    """names 중 하나라도 문맥 별칭이 맞는 제목의 bool Series."""
    names = [n for n in (names or []) if n in CONTEXT_ALIASES]
    if not names:
        return series.map(lambda _: False).astype(bool)
    return series.map(lambda t: any(context_alias_hit(t, n) for n in names)).astype(bool)


def classify_frame(title: str, duration_sec: int) -> str:
    title_lower = title.lower()

    if duration_sec >= 5400 or any(p in title_lower for p in ["[full]", "풀방송"]):
        for kw in OMNIBUS_KEYWORDS:
            if kw.lower() in title_lower:
                return "종합방송"

    for frame, keywords in FRAME_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in title_lower:
                return frame
        if any(context_alias_hit(title, n) for n in FRAME_CONTEXT_LINKS.get(frame, [])):
            return frame
    return "기타"


def clean_title_for_person_search(title: str, channel_name: str) -> str:
    cleaned = title
    noise_patterns = [
        "김어준의 겸손은힘들다 뉴스공장",
        "김어준의 겸손은힘들다",
        "겸손은힘들다",
        "겸손공장",
        "김용민 브리핑",
        channel_name
    ]
    for pattern in noise_patterns:
        cleaned = cleaned.replace(pattern, "")
    return cleaned


def keywords_pattern(keywords) -> str:
    return "|".join(re.escape(k) for k in keywords if k)


def channel_concentration(sel):
    """채널 편중도: 최대채널 조회 비중, 상위 2개 영상 조회 비중, 채널 수."""
    if sel is None or len(sel) == 0:
        return None
    total = int(sel["조회수"].sum())
    if total <= 0:
        return None
    by_ch = sel.groupby("채널명")["조회수"].sum().sort_values(ascending=False)
    top_share = round(float(by_ch.iloc[0]) / total * 100, 1)
    top2_vid_share = round(float(sel["조회수"].nlargest(2).sum()) / total * 100, 1)
    n_videos = len(sel)
    flagged = top_share > CONCENTRATION_TOP_SHARE or (
        n_videos >= 3 and top2_vid_share > CONCENTRATION_TOP2_VID_SHARE
    )
    return {
        "top_ch": str(by_ch.index[0]),
        "top_share": top_share,
        "top2_vid_share": top2_vid_share,
        "n_ch": int(len(by_ch)),
        "n_videos": n_videos,
        "flagged": bool(flagged),
    }


def format_concentration_report(conc) -> str:
    if not conc:
        return ""
    flag = " ⚠편중" if conc["flagged"] else ""
    return f" | 최대채널 {html.escape(conc['top_ch'])} {conc['top_share']}%(단독 기준){flag}"


def format_concentration_ai(conc) -> str:
    if not conc:
        return ""
    flag = " [편중]" if conc["flagged"] else ""
    # [10/27 QA] 편중 %는 단독(종합방송 제외) 조회 한 기준만 노출 — 상위2개 영상 비중은 판정에만 쓰고 AI 입력에서 뺌 (72.7% vs 99.5% 불일치 방지)
    return f" | 채널집중(단독 기준): {conc['top_ch']} {conc['top_share']}%{flag}"


def match_titles(series, keywords):
    pattern = keywords_pattern(keywords)
    if not pattern:
        return series.str.contains(r"(?!x)x", regex=True, na=False)
    return series.str.contains(pattern, regex=True, na=False)


def issue_match_mask(series, keywords, must=None):
    """이슈 매칭: keywords 중 하나 이상(OR). must(인물 별칭)가 있으면 그 인물도 함께 있어야 함(AND).
    예: '국민의힘 장동혁 대표 욕설' → (장동혁) AND (욕설|녹취…). 인물명 하나만으로는 매칭되지 않는다."""
    mask = match_titles(series, keywords)
    if must:
        mask &= match_titles(series, must)
    return mask


_REUPLOAD_TAG_RE = re.compile(r"[\[\(【][^\]\)】]*(?:본방송|생방송|라이브|live|다시보기|재업로드|재업|🔴)[^\]\)】]*[\]\)】]", re.I)
_NON_WORD_RE = re.compile(r"[^0-9A-Za-z가-힣]+")
REUPLOAD_DURATION_TOL = 5  # 초


def normalize_title_for_dedupe(title: str) -> str:
    t = _REUPLOAD_TAG_RE.sub("", str(title or ""))
    t = re.sub(r"(?i)\b(live|full)\b|본방송|생방송|다시보기|재업로드", "", t)
    return _NON_WORD_RE.sub("", t).lower()


def dedupe_reuploads(df):
    """같은 채널에서 정규화 제목이 같고 길이가 ±5초 이내인 영상(본방송·재업로드)은 조회수가 큰 1건만 남긴다.
    길이 정보가 없거나 0초(진행 중 라이브)면 합치지 않는다. 반환: (df, 제외 내역 [(남긴 제목, 뺀 제목, 뺀 조회수)])."""
    if df is None or df.empty or "길이초" not in df.columns:
        return df, []
    drop, log = set(), []
    keys = df["제목"].map(normalize_title_for_dedupe)
    for (_, key), grp in df.groupby([df["채널명"], keys]):
        if not key or len(grp) < 2:
            continue
        grp = grp.sort_values("조회수", ascending=False)
        kept = []
        for idx, row in grp.iterrows():
            dur = int(row.get("길이초") or 0)
            twin = next((k for k in kept if dur > 0 and int(df.at[k, "길이초"] or 0) > 0
                         and abs(int(df.at[k, "길이초"]) - dur) <= REUPLOAD_DURATION_TOL), None)
            if twin is None:
                kept.append(idx)
            else:
                drop.add(idx)
                log.append((df.at[twin, "제목"], row["제목"], int(row["조회수"])))
    for kept_t, dropped_t, v in log:
        print(f"ℹ️ [중복 제거] 본방송·재업로드 동일 방송: '{str(dropped_t)[:40]}' ({v:,}회) 제외 ← '{str(kept_t)[:40]}' 유지")
    return df.drop(index=list(drop)), log


def extract_major_issues(df, issue_keywords=None, absorbed=None, issue_must=None):
    """자동 이슈만 집계. 영상 1건은 이슈 1곳(더 구체적 키워드, 동점이면 조회·건수).
    채택 후 이름 사전으로 라벨만 붙인다. 80% 규칙은 배정 후 적용하고, 그 영향은 별도 로그."""
    issue_keywords = issue_keywords or {}
    absorbed = absorbed or {}
    issue_must = AUTO_ISSUE_MUST if issue_must is None else issue_must
    df_matchable = df[df["프레임"] != "종합방송"]
    aliases_map = load_aliases()

    issues = []
    for name, kws in issue_keywords.items():
        titles = df_matchable["제목_정제"]
        mask = issue_match_mask(titles, kws, issue_must.get(name))
        if issue_must.get(name):
            mask = mask | (context_mask(titles, issue_must.get(name)) & match_titles(titles, kws))
        if AUTO_ISSUE_AND.get(name):
            mask = mask & match_titles(titles, AUTO_ISSUE_AND.get(name))
        sel = df_matchable[mask]
        issues.append({
            "name": name,
            "keywords": list(kws),
            "must": issue_must.get(name),
            "views": int(sel["조회수"].sum()) if len(sel) else 0,
            "cnt": int(len(sel)),
            "_index": set(sel.index),
        })

    # 영상 1건 1이슈
    by_issue = {iss["name"]: [] for iss in issues}
    claimed = set()
    for idx, row in df_matchable.iterrows():
        cands = [iss for iss in issues if idx in iss["_index"]]
        if not cands:
            continue
        def _key(iss):
            return (specificity_score(iss["name"], iss.get("keywords"), iss.get("must")),
                    (int(iss.get("views") or 0), int(iss.get("cnt") or 0)))
        best = max(cands, key=_key)
        by_issue[best["name"]].append({
            "id": idx, "title": str(row["제목_정제"]), "channel": row["채널명"],
            "views": int(row["조회수"] or 0),
        })
        claimed.add(idx)

    adopted, dropped, dropped_share = adopt_after_assign(issues, by_issue, apply_share=True)
    adopted_nos, _, _ = adopt_after_assign(issues, by_issue, apply_share=False)
    adopted_names = {a["name"] for a in adopted}
    share_only = [r for r in adopted_nos if r["name"] not in adopted_names]
    if share_only:
        print("ℹ️ [자동 추출] 80% 규칙만으로 탈락(소연 확인용): " + ", ".join(
            f"{strip_tag(r['name'])} {r['top_share']}%" for r in share_only))

    # 같은 표시명으로 라벨된 묶음은 합친다
    grouped = {}
    for rec in adopted:
        raw = rec["name"]
        labeled = label_issue(strip_tag(raw), rec.get("keywords"), aliases_map)
        if labeled != strip_tag(raw):
            print(f"ℹ️ [이름 사전] 라벨: '{strip_tag(raw)}' → '{labeled}'")
        rec["label_from"] = strip_tag(raw) if labeled != strip_tag(raw) else ""
        rec["display"] = labeled
        if labeled in grouped:
            g = grouped[labeled]
            g["videos"] = (g.get("videos") or []) + (rec.get("videos") or [])
            g["keywords"] = list(dict.fromkeys(list(g.get("keywords") or []) + list(rec.get("keywords") or [])))
            g["merged"] = list(dict.fromkeys(list(g.get("merged") or []) + [strip_tag(m) for m in absorbed.get(raw, [])] + ([strip_tag(raw)] if strip_tag(raw) != labeled else [])))
            st = summarize_group(g["videos"])
            g.update(st)
        else:
            rec["merged"] = [strip_tag(m) for m in absorbed.get(raw, [])]
            grouped[labeled] = rec

    issue_results, issue_debug = [], {}
    matched_indices = set()
    ordered = sorted(grouped.values(), key=lambda r: (int(r.get("views") or 0), int(r.get("cnt") or 0)), reverse=True)
    for rec in ordered:
        display = rec["display"]
        sel_vids = rec.get("videos") or []
        idxs = [v["id"] for v in sel_vids]
        matched_indices.update(idxs)
        sel = df_matchable.loc[list(idxs)] if idxs else df_matchable.iloc[0:0]
        conc = channel_concentration(sel) if len(sel) else None
        has_mbc = (len(sel) > 0 and "MBC 라디오 시사" in sel["채널명"].values)
        issue_results.append({
            "이슈명": display,
            "건수": rec["cnt"],
            "채널수": rec["channels"],
            "조회수": rec["views"],
            "mbc_tag": " [MBC 포함]" if has_mbc else " [진영 내부]",
            "concentration": conc,
            "is_auto": True,
            "single_channel": bool(conc and conc["top_share"] >= DYNAMIC_MAX_CHANNEL_SHARE),
            "merged": rec.get("merged") or [],
            "keywords": list(rec.get("keywords") or []),
            "must": rec.get("must"),
            "label_from": rec.get("label_from") or "",
            "video_indices": idxs,
        })
        if len(sel):
            top5 = sel.sort_values(by="조회수", ascending=False).head(5)
            issue_debug[display] = [
                {"채널": r["채널명"], "조회수": int(r["조회수"]), "제목": r["제목"]}
                for _, r in top5.iterrows()
            ]
        print(f"ℹ️ [자동 추출] 채택 '{display}' {rec['cnt']}건/{rec['channels']}채널/{rec['views']:,}회/"
              f"최대{rec.get('top_share')}%" + (f" (원이름 {rec['label_from']})" if rec.get("label_from") else ""))

    df_issues = pd.DataFrame(issue_results)
    if not df_issues.empty:
        df_issues = df_issues.sort_values(by=["조회수", "건수"], ascending=[False, False]).reset_index(drop=True)

    unclassified_df = df.drop(index=list(matched_indices), errors="ignore")
    unclassified_df = unclassified_df[unclassified_df["프레임"] != "종합방송"]
    df_unclassified_top5 = unclassified_df.sort_values(by="조회수", ascending=False).head(5)

    print_issue_debug(issue_debug)
    return df_issues, df_unclassified_top5


def is_valid_dynamic_alias(alias) -> bool:
    if not isinstance(alias, str):
        return False
    a = alias.strip()
    if len(a) < 2:
        return False
    if a.isascii() and len(a) < 3:  # 'UN' 등 짧은 영문 약어 제외
        return False
    if a in DYNAMIC_ALIAS_STOPWORDS:
        return False
    if SURNAME_TITLE_RE.match(a):  # 성씨+직함 ("한 전 대표", "김 청장")
        return False
    return True


def _parse_json_loose(text):
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", t)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def _person_alias_map():
    m = {p: list(dict.fromkeys([p] + list(als))) for p, als in TRACK_PERSONS.items()}
    for p in PERSON_FACTS.get("persons") or []:
        if p.get("name"):
            m.setdefault(p["name"], [p["name"]])
    return m


def extract_dynamic_entities(all_titles, pool_titles=None, high_view_titles=None):
    """제목 목록에서 오늘의 이슈/인물 후보를 Gemini로 1회 추출. 풀은 종합방송 제외 전체.
    실패·키 없음이면 오프라인 후보를 쓴다(고정 사전 없음)."""
    empty = {"issues": [], "persons": []}
    api_key = GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY")
    if not all_titles:
        return empty
    if not api_key or not api_key.strip():
        return extract_offline_candidates(all_titles, _person_alias_map())

    try:
        client = genai.Client(api_key=api_key.strip(), http_options=genai.types.HttpOptions(api_version="v1beta"))  # google-genai 3.x 기본 v1 대비 (11/10)
        pool_set, high_set = set(pool_titles or []), set(high_view_titles or [])
        ordered = [t for t in all_titles if t in high_set] + [t for t in all_titles if t in pool_set and t not in high_set] \
            + [t for t in all_titles if t not in pool_set and t not in high_set]

        def _mark(t):
            return ("[고조회]" if t in high_set else "") + ("[미분류]" if t in pool_set else "") + (" " if (t in pool_set or t in high_set) else "") + t
        titles_text = "\n".join(f"- {_mark(t)}" for t in ordered[:150])
        prompt = f"""
        아래는 오늘 수집된 한국 시사 유튜브 영상 제목 목록입니다.
        여러 제목에 반복 등장하는 오늘의 핵심 이슈와 인물을 추출하세요.

        [규칙]
        - keywords/aliases는 반드시 아래 제목에 글자 그대로 등장하는 문자열만 쓰세요.
        - 성씨+직함(예: '한 전 대표', '김 청장', '이 대통령'), 한 글자, 직함 단독('대통령', '의원'), 짧은 영문 약어('UN')는 쓰지 마세요.
        - 이슈는 최대 15개, 인물은 최대 8명. 제목 3개 이상·여러 채널에 등장할 법한 것만.
        - 전체 제목을 대상으로 찾으세요. 인물이 관련되면 그 인물과 사건어가 제목에 함께 있는 것만.
        - 일반어(의혹, 스캔들, 논란, 성남라인 등)나 인물명만으로는 이슈를 만들지 마세요.
        - 이슈명에 인물을 넣을 때는 현재 직함만 쓰세요(예: '국민의힘 장동혁 대표'). 직함이 불확실하면 이름만 쓰세요.
        - 이슈명은 사건·쟁점을 가리키는 중립 명사구로 쓰세요. 제목의 따옴표 인용, 의문형 주장, 비유·자극 표현(무릎·굴복·몰락·충격 등)은 이슈명에 쓰지 마세요.
        - 이슈에 인물이 관련되면 keywords에 인물명과 사건어(예: '욕설', '무죄')를 함께 넣으세요.
        - 반드시 JSON만 출력: {{"issues":[{{"name":"이슈명","keywords":["키워드1","키워드2"]}}],"persons":[{{"name":"인물명","aliases":["인물명","별칭"]}}]}}

        [제목 목록]
        {titles_text}
        """

        primary_model = 'gemini-3.5-flash-lite'
        fallback_model = 'gemini-3.1-flash-lite'
        config = {"response_mime_type": "application/json", "temperature": 0.2,
                  "thinking_config": GEMINI_ENTITY_THINKING}

        try:
            response = client.models.generate_content(model=primary_model, contents=prompt, config=config)
        except Exception as primary_e:
            print(f"⚠️ [자동 추출] {primary_model} 실패 ({redact_secrets(primary_e)}), {fallback_model}로 재시도")
            response = client.models.generate_content(model=fallback_model, contents=prompt, config=config)
        _log_gemini_meta(response, "entities", GEMINI_ENTITY_THINKING)

        data = _parse_json_loose(getattr(response, "text", None))
        if not isinstance(data, dict):
            print("ℹ️ [자동 추출] JSON 파싱 실패 → 오프라인 후보")
            return extract_offline_candidates(all_titles, _person_alias_map())

        out = {"issues": [], "persons": []}
        for it in data.get("issues") or []:
            if isinstance(it, dict) and isinstance(it.get("name"), str):
                kws = [k.strip() for k in (it.get("keywords") or []) if is_valid_dynamic_alias(k)]
                if kws:
                    out["issues"].append({"name": it["name"].strip(), "keywords": kws})
        for it in data.get("persons") or []:
            if isinstance(it, dict) and isinstance(it.get("name"), str):
                als = [a.strip() for a in ([it["name"]] + list(it.get("aliases") or [])) if is_valid_dynamic_alias(a)]
                als = list(dict.fromkeys(als))
                if als:
                    out["persons"].append({"name": it["name"].strip(), "aliases": als})
        return out
    except Exception as e:
        print(f"ℹ️ [자동 추출] 실패 → 오프라인 후보 ({redact_secrets(e)})")
        return extract_offline_candidates(all_titles, _person_alias_map())


# 자동 이슈별 필수 인물 별칭 {이슈명(자동): [별칭...]} — filter_dynamic_entities가 채우고 extract_major_issues가 읽는다
AUTO_ISSUE_MUST = {}
AUTO_ISSUE_AND = {}  # 이슈명 사건어가 키워드에 없으면 제목 AND 조건


def filter_dynamic_entities(df, candidates, pool_mask=None):
    """자동 추출 후보를 결정적으로 검증. 풀 = 종합방송 제외 전체. 이름 사전은 쓰지 않음.
    80% 규칙은 배정 후 extract_major_issues에서 적용(여기선 제목·채널 하한만).
    반환: (issue_dict, person_dict, absorbed)."""
    dyn_issues, dyn_persons, absorbed = {}, {}, {}
    AUTO_ISSUE_MUST.clear()
    AUTO_ISSUE_AND.clear()
    log_thresholds()
    try:
        fixed_person_aliases = {a for als in TRACK_PERSONS.values() for a in als} | set(TRACK_PERSONS.keys())
        fixed_person_aliases |= {trig for rules in CONTEXT_ALIASES.values() for trig, _ in rules}
        df_pool = df[df["프레임"] != "종합방송"]

        person_alias_map = _person_alias_map()
        for it in candidates.get("persons", []):
            person_alias_map.setdefault(it["name"], list(dict.fromkeys([it["name"]] + list(it["aliases"]))))

        def persons_in(name, kws):
            """이슈명·키워드에 등장하는 인물 → 그 인물의 별칭(AND 조건). 여러 명이면 이슈명에 먼저 나온 1명."""
            text = " ".join([strip_tag(name)] + list(kws))
            hits = [(min((text.find(a) for a in als if a in text), default=-1), p, als)
                    for p, als in person_alias_map.items() if any(a in text for a in als)]
            hits = sorted(h for h in hits if h[0] >= 0)
            return hits[0][2] if hits else None

        def pre_ok(kws, must=None, and_terms=None):
            sel = df_pool[issue_match_mask(df_pool["제목_정제"], kws, must)]
            if issue_must_ctx := must:
                sel_ctx = df_pool[context_mask(df_pool["제목_정제"], issue_must_ctx) & match_titles(df_pool["제목_정제"], kws)]
                sel = df_pool.loc[sel.index.union(sel_ctx.index)]
            if and_terms and len(sel):
                sel = sel[match_titles(sel["제목_정제"], and_terms)]
            ok, reason = passes_thresholds(len(sel), int(sel["채널명"].nunique()) if len(sel) else 0,
                                           0.0, int(sel["조회수"].max()) if len(sel) else 0,
                                           apply_share=False)
            return ok, reason, sel

        for it in candidates.get("issues", []):
            name = it["name"]
            must = persons_in(name, it["keywords"])
            must_set = set(must or [])
            kws = [k for k in it["keywords"] if k not in must_set]
            if must and not kws:
                kws = [tok for tok in name_tokens(name) if tok not in must_set and not any(a in tok for a in must_set)]
            if is_general_only(kws, must) or is_junk_issue_name(name):
                print(f"ℹ️ [자동 추출] 일반어·인물 단독이라 제외: {name}")
                continue
            if not kws or f"{name}{DYNAMIC_TAG}" in dyn_issues:
                continue
            name_ev = [tok for tok in name_tokens(name) if tok not in must_set]
            and_terms = None
            if name_ev and not any(any(e == k or e in k or k in e for k in kws) for e in name_ev):
                and_terms = name_ev
            ok, reason, _sel = pre_ok(kws, must, and_terms)
            if not ok:
                print(f"ℹ️ [자동 추출] 하한 미달 제외: {name} ({reason})")
                continue
            checked = check_issue_name(name, kws, PERSON_FACTS, person_names=list(person_alias_map))
            if not checked:
                print(f"ℹ️ [자동 추출] 이슈명 직함·여야 점검 실패로 제외: {name}")
                continue
            tagged = f"{checked}{DYNAMIC_TAG}"
            if tagged in dyn_issues:
                continue
            dyn_issues[tagged] = kws
            if must:
                AUTO_ISSUE_MUST[tagged] = list(must)
            if and_terms:
                AUTO_ISSUE_AND[tagged] = list(and_terms)
            print(f"ℹ️ [자동 추출] 이슈 매칭 조건: {checked} = 인물({'|'.join(must or []) or '없음'}) AND 키워드({'|'.join(kws)})")

        for it in candidates.get("persons", []):
            if len(dyn_persons) >= DYNAMIC_MAX_PERSONS:
                break
            name = it["name"]
            als = it["aliases"]
            if name in TRACK_PERSONS or any(a in fixed_person_aliases for a in als) or f"{name}{DYNAMIC_TAG}" in dyn_persons:
                continue
            sel = df[match_titles(df["제목_정제"], als)]
            if len(sel) >= DYNAMIC_MIN_TITLES and sel["채널명"].nunique() >= DYNAMIC_MIN_CHANNELS:
                dyn_persons[f"{name}{DYNAMIC_TAG}"] = als

        dyn_issues, absorbed = merge_auto_into_fixed(dyn_issues)
        def _remap(src):
            new_map = {}
            if absorbed:
                for kept, names in absorbed.items():
                    if kept in src:
                        new_map[kept] = src[kept]
                    for n in names:
                        if n in src and kept not in new_map:
                            new_map[kept] = src[n]
            for k, v in src.items():
                if k in dyn_issues:
                    new_map.setdefault(k, v)
            src.clear()
            src.update(new_map)
        _remap(AUTO_ISSUE_MUST)
        _remap(AUTO_ISSUE_AND)
    except Exception as e:
        print(f"ℹ️ [자동 추출] 검증 실패 → 빈 목록 ({redact_secrets(e)})")
        AUTO_ISSUE_MUST.clear()
        AUTO_ISSUE_AND.clear()
        return {}, {}, {}

    if dyn_issues or dyn_persons:
        print(f"ℹ️ [자동 추출] 이슈 {list(dyn_issues)} / 인물 {list(dyn_persons)}")
    return dyn_issues, dyn_persons, absorbed


def build_issue_section(df_issues, prev_data, date_str, issue_keywords=None):
    """'주요 이슈 언급' 섹션 텍스트(텔레그램 HTML), 이슈 줄만, AI 입력용 문자열, 갱신된 이슈 이력을 반환.
    이력은 previous_data.json의 'issue_history' 키(날짜별 자동 이슈)로 저장 — 기존 persons/frames 키는 그대로."""
    lines, lines_ai, auto_today = [], [], {}
    for _, row in df_issues.iterrows():
        conc = row.get("concentration")
        merged = row.get("merged") or []
        merged_note = f" (통합: {', '.join(merged)})" if merged else ""
        if row.get("single_channel"):
            merged_note += f" ⚠단일 채널 주의({conc['top_ch']} {conc['top_share']}%)"
        disp = strip_tag(str(row["이슈명"]))
        lines.append(
            f"• {html.escape(normalize_report_text(disp))} : <b>{row['건수']}건</b> | <b>{row['채널수']}채널</b> | {row['조회수']:,}회{row['mbc_tag']}{format_concentration_report(conc)}{html.escape(merged_note)}"
        )
        lines_ai.append(
            f"- {disp}: {row['건수']}건 | {row['채널수']}채널 | {row['조회수']:,}회{row['mbc_tag']}{format_concentration_ai(conc)}{merged_note}"
        )
        auto_today[disp] = {
            "keywords": list(row["keywords"]) if "keywords" in row and row["keywords"] is not None else list((issue_keywords or {}).get(f"{disp}{DYNAMIC_TAG}") or (issue_keywords or {}).get(disp) or []),
            "cnt": int(row["건수"]),
        }
    history = update_history((prev_data or {}).get("issue_history"), date_str, auto_today)
    cands = persistent_candidates(history, date_str)
    issue_lines = "\n".join(lines) if lines else "• 특이 이슈 없음"
    issue_ai = "\n".join(lines_ai) if lines_ai else "특이 이슈 없음"
    section = issue_lines
    if cands:
        section += f"\n• 이름 사전 별칭 추가 제안 (최근 7일 중 4일 이상 자동 추출): {html.escape(format_candidates(cands))}"
        issue_ai += f"\n- 이름 사전 별칭 추가 제안: {format_candidates(cands)}"
    return section, issue_lines, issue_ai, history


def print_issue_debug(issue_debug: dict) -> None:
    if not issue_debug:
        return
    print("\n" + "=" * 60)
    print("[디버그] 이슈별 매칭 제목 (텔레그램 미전송 · 로그 전용)")
    print("=" * 60)
    for issue_name, rows in issue_debug.items():
        print(f"\n■ {issue_name}")
        for r in rows:
            print(f"    {r['조회수']:>9,}회  [{r['채널']}]  {r['제목'][:60]}")
    print("=" * 60 + "\n")


def get_channel_uploads_playlist_id(youtube, channel_id):
    try:
        response = youtube.channels().list(part="contentDetails", id=channel_id).execute()
        items = response.get("items", [])
        if items:
            return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    except Exception as e:
        print(f"채널 ID({channel_id}) 재생목록 조회 실패: {redact_secrets(e)}")
    return None


def fetch_recent_videos(youtube, playlist_id, channel_name, channel_id):
    video_list = []
    now_kst = datetime.now(KST)
    thirty_six_hours_ago = now_kst - timedelta(hours=36)
    
    video_items_raw = []

    if playlist_id:
        try:
            playlist_response = youtube.playlistItems().list(
                part="snippet",
                playlistId=playlist_id,
                maxResults=50
            ).execute()
            video_items_raw.extend(playlist_response.get("items", []))
        except Exception as e:
            print(f"플레이리스트 수집 에러 ({channel_name}): {redact_secrets(e)}")

    try:
        search_response = youtube.search().list(
            part="snippet",
            channelId=channel_id,
            order="date",
            maxResults=25,
            type="video"
        ).execute()
        
        existing_ids = {item["snippet"]["resourceId"]["videoId"] for item in video_items_raw if "resourceId" in item.get("snippet", {})}
        for s_item in search_response.get("items", []):
            v_id = s_item["id"].get("videoId")
            if v_id and v_id not in existing_ids:
                s_item["snippet"]["resourceId"] = {"videoId": v_id}
                video_items_raw.append(s_item)
    except Exception as e:
        print(f"Search API 수집 에러 ({channel_name}): {redact_secrets(e)}")

    video_ids = list({item["snippet"]["resourceId"]["videoId"] for item in video_items_raw if "resourceId" in item.get("snippet", {})})
    
    if not video_ids:
        print(f"ℹ️ [{channel_name}] 조회 결과 0건 (최근 업로드 없음)")
        return []

    chunk_size = 50
    for i in range(0, len(video_ids), chunk_size):
        chunk_ids = video_ids[i:i + chunk_size]
        try:
            videos_response = youtube.videos().list(
                part="snippet,statistics,contentDetails",
                id=",".join(chunk_ids)
            ).execute()

            for item in videos_response.get("items", []):
                snippet = item["snippet"]
                stats = item.get("statistics", {})
                content_details = item.get("contentDetails", {})

                if snippet.get("liveBroadcastContent", "none") == "upcoming":
                    continue

                duration_sec = parse_iso8601_duration(content_details.get("duration", "PT0S"))
                if 0 < duration_sec <= 60:
                    continue

                pub_date_str = snippet["publishedAt"].replace("Z", "+00:00")
                pub_time_utc = datetime.fromisoformat(pub_date_str)
                pub_time_kst = pub_time_utc.astimezone(KST)

                if pub_time_kst < thirty_six_hours_ago:
                    continue

                elapsed_hours = (now_kst - pub_time_kst).total_seconds() / 3600.0
                elapsed_hours = max(elapsed_hours, 0.01)

                views = int(stats.get("viewCount", 0))
                views_per_hour = int(views / elapsed_hours)

                if elapsed_hours < (1.0 / 60.0):
                    elapsed_str = "방금 전"
                elif elapsed_hours < 1.0:
                    elapsed_str = f"{int(elapsed_hours * 60)}분 전"
                else:
                    elapsed_str = f"{int(elapsed_hours)}시간 전"

                title = snippet["title"]
                
                live_keywords = ["live", "라이브", "🔴", "12시에 만나요", "현장live", "뉴스공장 2026", "겸손은힘들다"]
                is_live_video = any(kw in title.lower() for kw in live_keywords) or (duration_sec >= 5400 and "full" in title.lower())

                video_list.append({
                    "채널명": channel_name,
                    "제목": title,
                    "제목_정제": clean_title_for_person_search(title, channel_name),
                    "프레임": classify_frame(title, duration_sec),
                    "길이초": duration_sec,
                    "조회수": views,
                    "시간당조회수": views_per_hour,
                    "경과시간_hours": elapsed_hours,
                    "경과시간": elapsed_str,
                    "게시일시_dt": pub_time_kst,
                    "게시일시": pub_time_kst.strftime("%m-%d %H:%M"),
                    "링크": f"https://youtu.be/{item['id']}",
                    "설명": (snippet.get("description") or "")[:1000],
                    "is_live": is_live_video
                })

        except Exception as e:
            print(f"영상 상세 수집 에러 ({channel_name}): {redact_secrets(e)}")

    print(f"✅ [{channel_name}] 최종 수집: 총 {len(video_list)}개 영상")
    return video_list


def load_previous_data():
    if os.path.exists(PREV_DATA_FILE):
        try:
            with open(PREV_DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"이전 데이터 로드 실패: {redact_secrets(e)}")
    return None


def save_current_data(data):
    if DRY_RUN:
        print("[DRY_RUN] previous_data.json 저장 생략")
        return
    try:
        with open(PREV_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"현재 데이터 저장 실패: {redact_secrets(e)}")


def generate_ai_insight(df_top, frame_stat_summary, person_summary_str, trend_summary_str, frame_trend_str, issue_summary_str, unclassified_summary_str, channel_volume_str=""):
    api_key = GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY")
    if not api_key or api_key.strip() == "":
        print("❌ GEMINI_API_KEY가 로드되지 않았거나 값이 비어 있습니다.")
        return "<b>[AI 심층 분석 스킵: GEMINI_API_KEY 미설정]</b>"

    try:
        client = genai.Client(api_key=api_key.strip(), http_options=genai.types.HttpOptions(api_version="v1beta"))  # google-genai 3.x 기본 v1 대비 (11/10)

        top_videos = df_top.head(10)[["채널명", "제목", "프레임", "조회수"]].to_dict(orient="records")

        homonym_hint = homonym_prompt_lines().replace("\n", " ").lstrip("- ")
        prompt = f"""
        당신은 수치 데이터와 실시간 프레임 전환을 정교하게 분석하는 수석 데이터 분석가입니다.
        과거 어제의 분석 프레임이나 지나간 이슈를 재활용하지 말고, 오직 오늘 수집된 데이터와 전일 대비 증감 지표만을 근거로 분석하세요.

{FACT_BLOCK}

        [오늘의 상위 영상 데이터 (누적 조회수 TOP 10)]
        {top_videos}

        [오늘의 주요 이슈 언급 현황 (건수 | 채널 수 | 총 조회수 | MBC포함여부 | 채널집중, [편중] 표시 / '(자동)'은 제목에서 자동 추출된 신규 항목)]
        {issue_summary_str}

        [이슈 미분류 상위 영상 (새 쟁점 후보)]
        {unclassified_summary_str}

        [채널별 수집 비중 (수집 영상 수 기준)]
        {channel_volume_str}

        [오늘의 프레임별 현황 및 전일 대비 비중 변화]
        {frame_stat_summary}
        {frame_trend_str}

        [주요 인물 언급 현황 (전일 단독 vs 금일 단독 조회수 비교 포함)]
        {person_summary_str}

        [전일 대비 주요 지표 증감 추세]
        {trend_summary_str}

        [엄격한 작성 지침 - 반드시 준수]
        1. [주요 확산 이슈 및 인물 수치 최우선 분석]:
           - 채널 수(확산도)가 넓은 주요 확산 이슈(예: 오늘 집계 상위 이슈) 및 레거시(MBC) 확장 여부를 1순위 핵심 기류로 필수 반영하세요.
        2. [키워드 집계의 한계 — 방향성 단정 금지]:
           - 이슈·인물 건수는 제목 키워드 일치 건수일 뿐이며 찬반·논조를 구분하지 않습니다. 반대·신중론 영상(예: '탄핵까지 가면 안된다')도 같은 건수에 포함됩니다.
           - 건수·채널 수·조회수만으로 '여론 집결', '결집', '공감대', '한목소리', '총공세' 등 방향성·합의를 서술하지 마세요. '○개 채널에서 언급'처럼 언급 범위로만 표현하세요.
           - 논조를 언급하려면 상위 영상 제목에 근거해 '제목상 찬성/반대/유보 논조 혼재'처럼 근거와 함께 쓰세요.
        3. [자가 단정 및 추측성 서술 절대 금지]:
           - 채널의 '특화 편성', '편성 전략' 등 방송사의 내면 의도를 단정하지 마세요.
           - 종합방송 비중이 높은 상태에서 내용이나 시청층 심리를 자의적으로 해석하지 마세요.
        4. [과거 소재 재활용 및 상투적 문구 금지]:
           - "전일 대비 유의미한 변동은 나타나지 않았으나"와 같은 상투적 표현 금지.
           - 호칭: 이재명은 현직 대한민국 대통령입니다. 반드시 '이재명 대통령'으로 표기하세요.
        5. [키워드 범주 제한]: '주요 언급 키워드'는 오늘 상위 영상 제목에 실제 등장한 [주요 인물, 핵심 정치 이슈, 법적 대응/대립 사건]만 8~10개 엄선하세요. (야권(국민의힘·개혁신당) 인물이나 단순 축구/가십성 인물 제외)
        6. [채널 편중 보정]: 인물·이슈의 최대채널 비중이 50%를 넘거나 [편중] 표시가 있으면 '대중적 관심 견인', '관심 집중', '폭발적 반응' 등으로 일반화하지 말고 '○○ 채널(비중 N%) 중심 조회'로 편중 사실을 함께 쓰세요. 채널 수 2곳 이하 이슈에는 '확산' 표현을 쓰지 마세요.
        7. [수치 재계산 금지 — 10/27 QA]: 편중 비중(%)은 입력에 '(단독 기준)'으로 적힌 값만 그대로 쓰고 새로 계산하거나 다른 분모(풀버전 포함·상위 영상 비중)로 바꾸지 마세요. 증감률은 [전일 대비 주요 지표 증감 추세]에 %가 적힌 인물만 쓰고, '증감률 생략/없음/신작'으로 적힌 인물에는 %를 만들지 마세요.
        8. [표기 고정]: 장동혁은 반드시 '국민의힘 장동혁 대표'('장동혁 의원'·'최고위원' 금지). {homonym_hint} '구조'라는 단어(예: '갈등 구조')는 쓰지 말고 '구도'·'양상'으로 쓰세요.
        9. [이슈 집계 범위]: 이슈 건수·조회수는 종합방송(풀버전) 제외 집계이므로 조회 TOP10의 풀버전 조회수와 직접 비교하지 마세요.

        [출력 양식]
        <b>[AI 데이터 심층 분석]</b>

        1. 핵심 기류
        - (주요 확산 이슈의 채널 확산 양상 및 인물별 단독 조회수 구도를 2문장으로 요약)

        2. 주요 언급 키워드
        - (오늘 데이터 기반 주요 인물 및 신규 핵심 이슈 키워드 8~10개)

        3. 모니터링 관측 평가
        - (채널 폭 확산도 및 인물 관심도 이동에 기반한 분석가 관점의 총평 1문장, 채널 편중이 있으면 편중 채널을 함께 표기)
        """

        primary_model = 'gemini-3.5-flash-lite'
        fallback_model = 'gemini-3.1-flash-lite'
        insight_config = {"thinking_config": GEMINI_INSIGHT_THINKING}

        try:
            response = client.models.generate_content(
                model=primary_model,
                contents=prompt,
                config=insight_config,
            )
            _log_gemini_meta(response, "insight", GEMINI_INSIGHT_THINKING)
            return response.text
        except Exception as primary_e:
            print(f"⚠️ {primary_model} 실패 ({redact_secrets(primary_e)}), {fallback_model}로 재시도")
            response = client.models.generate_content(
                model=fallback_model,
                contents=prompt,
                config=insight_config,
            )
            _log_gemini_meta(response, "insight", GEMINI_INSIGHT_THINKING)
            return response.text

    except Exception as e:
        return f"<b>[AI 심층 분석 생성 오류: {redact_secrets(e)}]</b>"


def split_telegram_chunks(message, footer="", MAX_LEN=3800):
    """기존 분할 규칙(3800자, 빈 줄 기준) + 점검 꼬리말을 마지막 조각에(넘치면 별도 조각으로)."""
    if len(message) <= MAX_LEN:
        chunks = [message]
    else:
        chunks = []
        while len(message) > MAX_LEN:
            split_idx = message.rfind("\n\n", 0, MAX_LEN)
            if split_idx == -1:
                split_idx = MAX_LEN
            chunks.append(message[:split_idx])
            message = message[split_idx:].lstrip()
        if message:
            chunks.append(message)
    return append_footer_to_chunks(chunks, footer, MAX_LEN)


def wrap_expandable_chunk(chunk: str, *, part: int = 1, total: int = 1) -> str:
    """원문 리포트를 텔레그램 접힌 인용(blockquote expandable)으로 감싼다."""
    part_s = f" ({part}/{total})" if total > 1 else ""
    return (
        f"<b>원문 리포트</b>{part_s} · 탭해서 펼치기\n"
        f"<blockquote expandable>{chunk}</blockquote>"
    )


def send_telegram_message(message, footer="", *, expandable=False):
    if DRY_RUN:
        print(f"[DRY_RUN] telegram skipped ({len(message or '')}자" + (f", footer {len(footer)}자" if footer else "") + ")")
        return
    token = TELEGRAM_BOT_TOKEN or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = TELEGRAM_CHAT_ID or os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print(f"❌ 텔레그램 전송 실패: 토큰 또는 CHAT_ID 환경 변수가 제대로 설정되지 않았습니다. (TOKEN 설정됨: {'예' if token else '아니오'}, CHAT_ID 설정됨: {'예' if chat_id else '아니오'})")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    # 접기 래퍼(~80자) 여유를 위해 expandable일 때 조금 더 짧게 분할
    max_len = 3700 if expandable else 3800
    chunks = split_telegram_chunks(message, footer, MAX_LEN=max_len)

    for i, chunk in enumerate(chunks, 1):
        text = wrap_expandable_chunk(chunk, part=i, total=len(chunks)) if expandable else chunk
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        try:
            response = requests.post(url, data=payload, timeout=10)
            res_json = response.json()
            if not res_json.get("ok"):
                print(f"❌ 텔레그램 전송 실패 (Part {i}/{len(chunks)}): {res_json}")
            else:
                print(f"✅ 텔레그램 메시지 전송 성공 (Part {i}/{len(chunks)})")
        except Exception as e:
            print(f"❌ 텔레그램 통신 에러 (Part {i}/{len(chunks)}): {redact_secrets(e)}")


def build_issue_defs(df, issue_records, issue_keywords):
    """카드 이슈별 배정 영상(종합방송 제외)을 모아 issue_defs.build_defs 입력으로 변환.
    aliases는 표시명의 사전 별칭 + 그날 추출 키워드."""
    videos, aliases = {}, {}
    aliases_map = load_aliases()
    df_m = df[df["프레임"] != "종합방송"]
    cols = [c for c in ("채널명", "제목", "조회수", "링크", "프레임", "설명") if c in df_m.columns]
    for row in issue_records:
        name = row.get("이슈명")
        if not name:
            continue
        idxs = row.get("video_indices") or []
        if idxs:
            sel = df_m.loc[df_m.index.intersection(idxs)]
        else:
            kws = list(row.get("keywords") or issue_keywords.get(name) or issue_keywords.get(f"{name}{DYNAMIC_TAG}") or [])
            if not kws:
                continue
            must = row.get("must") or AUTO_ISSUE_MUST.get(name) or AUTO_ISSUE_MUST.get(f"{name}{DYNAMIC_TAG}")
            mask = issue_match_mask(df_m["제목_정제"], kws, must)
            sel = df_m[mask]
        videos[name] = sel.sort_values(by="조회수", ascending=False).head(5)[cols].to_dict("records") if len(sel) and cols else []
        aliases[name] = aliases_for_qa(name, row.get("keywords") or issue_keywords.get(name) or [], aliases_map)
    return build_defs(issue_records, videos, source="yeogwon", aliases=aliases)


def run_monitoring():
    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    all_data = []

    no_upload_channels = []
    failed_channels = []

    for channel_name, channel_id in TARGET_CHANNELS.items():
        try:
            uploads_id = get_channel_uploads_playlist_id(youtube, channel_id)
            videos = fetch_recent_videos(youtube, uploads_id, channel_name, channel_id)
            if videos:
                all_data.extend(videos)
            else:
                no_upload_channels.append(channel_name)
        except Exception as e:
            print(f"❌ [{channel_name}] 수집 중 예외 발생: {redact_secrets(e)}")
            failed_channels.append(channel_name)

    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    total_target_channels = len(TARGET_CHANNELS)

    if not all_data:
        send_telegram_message(
            f"<b>[여권 성향 유튜브 동향 리포트]</b>\n({now_str} KST)\n\n"
            f"최근 수집 범위 이내에 업로드된 일반 영상이 없습니다."
        )
        return

    df = pd.DataFrame(all_data)
    save_raw_csv(df, datetime.now(KST))
    # 같은 방송의 본방송·재업로드 중복 제거 (이슈·인물·프레임 집계 전에 1건으로)
    df, _dedupe_log = dedupe_reuploads(df)
    df = df.reset_index(drop=True)

    df_top = df.sort_values(by="조회수", ascending=False).reset_index(drop=True)

    def is_stabilized_vph(row):
        if row["is_live"]:
            return row["경과시간_hours"] >= 6.0
        return row["경과시간_hours"] >= 1.0

    df_vph_candidates = df[df.apply(is_stabilized_vph, axis=1)]
    if not df_vph_candidates.empty:
        df_vph = df_vph_candidates.sort_values(by="시간당조회수", ascending=False).reset_index(drop=True)
    else:
        df_vph = df.sort_values(by="시간당조회수", ascending=False).reset_index(drop=True)

    total_videos = len(df)
    collected_count = total_target_channels - len(no_upload_channels) - len(failed_channels)
    hot_100k_count = len(df[df["조회수"] >= 100000])

    # ----- [1. 프레임별 건수 및 조회수 비중 집계 & 전일 대비 변동 계산] -----
    prev_data = load_previous_data()
    prev_frames = prev_data.get("frames", {}) if prev_data else {}

    total_views = df["조회수"].sum()
    frame_group = df.groupby("프레임").agg(
        건수=("조회수", "count"),
        총조회수=("조회수", "sum")
    ).reset_index()

    frame_group["건수비중"] = (frame_group["건수"] / total_videos * 100).round(1)
    frame_group["조회비중"] = (frame_group["총조회수"] / total_views * 100).round(1)
    frame_group = frame_group.sort_values(by="총조회수", ascending=False)

    frame_summary_lines = []
    frame_stat_summary_for_ai = []
    frame_trend_for_ai = []
    curr_frames_data = {}

    for _, row in frame_group.iterrows():
        f_name = row["프레임"]
        cnt = int(row["건수"])
        cnt_ratio = row["건수비중"]
        view_ratio = row["조회비중"]

        curr_frames_data[f_name] = {"cnt": cnt, "views": int(row["총조회수"]), "view_ratio": view_ratio}

        note = " (코너 혼재, 프레임 판정 불가)" if f_name == "종합방송" else ""

        frame_summary_lines.append(f"• {f_name} : <b>{cnt}개</b> ({cnt_ratio}%) | <b>{view_ratio}%</b>{note}")
        frame_stat_summary_for_ai.append(f"- {f_name}: {cnt}개({cnt_ratio}%) | 조회비중 {view_ratio}%{note}")

        if f_name in prev_frames:
            prev_ratio = prev_frames[f_name].get("view_ratio", 0)
            diff_ratio = round(view_ratio - prev_ratio, 1)
            sign = "+" if diff_ratio >= 0 else ""
            frame_trend_for_ai.append(f"- {f_name} 조회비중: 전일 {prev_ratio}% -> 금일 {view_ratio}% ({sign}{diff_ratio}%p)")

    frame_summary_text = "\n".join(frame_summary_lines)
    frame_stat_summary_str = "\n".join(frame_stat_summary_for_ai)
    frame_trend_str_for_ai = "\n".join(frame_trend_for_ai) if frame_trend_for_ai else "전일 프레임 비중 비교 데이터 없음"

    # ----- [채널별 수집 비중] -----
    ch_counts = df["채널명"].value_counts()
    channel_volume_parts = [
        f"{html.escape(str(ch))} {int(c)}개({round(int(c) / total_videos * 100, 1)}%)"
        for ch, c in ch_counts.head(3).items()
    ]
    top_ch_volume_share = round(int(ch_counts.iloc[0]) / total_videos * 100, 1)
    channel_volume_flag = " ⚠편중" if top_ch_volume_share > CONCENTRATION_TOP_SHARE else ""
    channel_volume_text = ", ".join(channel_volume_parts) + channel_volume_flag
    channel_volume_str_for_ai = ", ".join(
        f"{ch} {int(c)}개({round(int(c) / total_videos * 100, 1)}%)" for ch, c in ch_counts.head(3).items()
    ) + (" [편중]" if channel_volume_flag else "")

    # ----- [2-0. 오늘의 이슈/인물 자동 추출 (고정 칸 없음, 실패 시 오프라인 후보)] -----
    log_thresholds()
    if DRY_RUN:
        print("[DRY_RUN] 텔레그램·KV 미게시, previous_data·data 브랜치 미기록으로 실행")
    pool_mask = df["프레임"] != "종합방송"
    pool_titles = df.loc[pool_mask, "제목_정제"].drop_duplicates().tolist()
    high_view_titles = df.loc[pool_mask & (df["조회수"] >= DYNAMIC_HIGH_VIEW), "제목_정제"].drop_duplicates().tolist()
    dyn_candidates = extract_dynamic_entities(df["제목_정제"].drop_duplicates().tolist(), pool_titles, high_view_titles)
    dyn_issues, dyn_persons, issue_absorbed = filter_dynamic_entities(df, dyn_candidates, pool_mask)
    if issue_absorbed:
        print(f"ℹ️ [자동 추출] 겹침 통합: {issue_absorbed}")
    issue_keywords_today = dict(dyn_issues)
    person_aliases_today = {**TRACK_PERSONS, **dyn_persons}

    # ----- [2. 주요 확산 이슈 집계 및 미분류 상위 5 추출] -----
    df_issues, df_unclassified_top5 = extract_major_issues(df, issue_keywords_today, issue_absorbed)
    issue_section_text, issue_summary_text, issue_summary_str_for_ai, issue_history = build_issue_section(
        df_issues, prev_data, datetime.now(KST).strftime("%Y-%m-%d"), issue_keywords_today)
    

    unclassified_summary_lines = []
    for _, row in df_unclassified_top5.iterrows():
        safe_t = html.escape(str(row['제목']))
        unclassified_summary_lines.append(
            f"• {row['조회수']:,}회 {safe_t} ([{row['채널명']}])"
        )
    unclassified_summary_text = "\n".join(unclassified_summary_lines) if unclassified_summary_lines else "• 미분류 상위 영상 없음"

    # ----- [3. 주요 인물별 언급 및 단독 조회수 우선 집계 & 종합방송 15% 가중치 보정] -----
    prev_persons = prev_data.get("persons", {}) if prev_data else {}

    curr_persons_data = {}
    person_summary_lines = []
    hanun_person_rows = []
    person_summary_for_ai = []
    trend_summary_for_ai = []

    for p, aliases in person_aliases_today.items():
        sel = df[match_titles(df["제목_정제"], aliases) | context_mask(df["제목_정제"], [p])]
        p_cnt = len(sel)

        sel_standalone = sel[sel["프레임"] != "종합방송"]
        sel_omnibus = sel[sel["프레임"] == "종합방송"]

        p_views_standalone = int(sel_standalone["조회수"].sum()) if len(sel_standalone) > 0 else 0
        p_views_omnibus = int(sel_omnibus["조회수"].sum()) if len(sel_omnibus) > 0 else 0
        p_views_total = int(sel["조회수"].sum()) if p_cnt > 0 else 0
        
        # 종합방송 가중치 분할: 15% 가중치 반영
        p_views_weighted = p_views_standalone + int(p_views_omnibus * 0.15)

        curr_persons_data[p] = {
            "cnt": p_cnt,
            "views_standalone": p_views_standalone,
            "views_total": p_views_total,
            "views_weighted": p_views_weighted
        }

        if p_cnt > 0:
            disp = person_display_name(p)
            _, prev_rec = find_prev_person(p, prev_persons)
            prev_v = int((prev_rec or {}).get("views_standalone") or 0)
            # 신작: 단독 영상이 모두 업로드 1시간 미만이거나 단독 조회 100회 미만 (예: 정청래 37회·업로드 6분 후)
            is_new_clip = len(sel_standalone) > 0 and (
                p_views_standalone < NEW_CLIP_MAX_VIEWS
                or bool((sel_standalone["경과시간_hours"] < NEW_CLIP_MAX_HOURS).all()))
            diff_str, trend_ai = person_trend(p_views_standalone, prev_rec, is_new_clip)
            trend_summary_for_ai.append(f"- {disp}: {trend_ai}")

            if len(sel_omnibus) > 0 and len(sel_standalone) == 0:
                views_disp = f"단독 0회 (보정합산 {p_views_weighted:,}회 | 풀버전 {len(sel_omnibus)}건 포함 {p_views_total:,}회)"
            elif len(sel_omnibus) > 0:
                views_disp = f"단독 {p_views_standalone:,}회 (보정합산 {p_views_weighted:,}회 | 풀버전 포함 {p_views_total:,}회)"
            else:
                views_disp = f"단독 {p_views_standalone:,}회"

            conc = None if is_new_clip else channel_concentration(sel_standalone)   # 편중 %: 단독 기준 한 가지
            cnt_disp = f"{p_cnt}건" if len(sel_omnibus) == 0 else f"{p_cnt}건(단독 {len(sel_standalone)}+종합 {len(sel_omnibus)})"
            line_text = f"• {html.escape(disp)} : <b>{cnt_disp}</b> ({views_disp}{format_concentration_report(conc)}{diff_str})"
            # 정렬 우선순위 키: 1순위 p_views_standalone(단독 조회수), 2순위 p_views_weighted(보정 조회수)
            person_summary_lines.append((p, p_cnt, p_views_standalone, p_views_weighted, p_views_total, line_text))
            person_summary_for_ai.append(f"- {disp}: {p_cnt}건 (금일 단독 {p_views_standalone:,}회 / 전일 단독 {prev_v:,}회 / 보정합산 {p_views_weighted:,}회){format_concentration_ai(conc)}")
            # 한눈 카드용
            _pct = None
            if (not is_new_clip) and prev_rec is not None and "views_standalone" in prev_rec:
                _pv = int(prev_rec.get("views_standalone") or 0)
                if _pv >= MIN_PREV_VIEWS_FOR_PCT:
                    _pct = round(((p_views_standalone - _pv) / _pv) * 100, 1)
            hanun_person_rows.append({
                "name": p,
                "display": disp,
                "cnt": p_cnt,
                "views_standalone": p_views_standalone,
                "top_share": (conc or {}).get("top_share") if conc else None,
                "n_ch": (conc or {}).get("n_ch") if conc else sel_standalone["채널명"].nunique() if len(sel_standalone) else 0,
                "pct_change": _pct,
                "trend_short": (f"{_pct:+.0f}%" if _pct is not None else ("신작" if is_new_clip else "")),
                "from_issue": False,
            })

    # 단독 조회수(Standalone Views) 기준 1순위 정렬 고정 (착시 차단)
    person_summary_lines.sort(key=lambda x: (x[2], x[3], x[4]), reverse=True)
    
    p_text_list = [item[5] for item in person_summary_lines]
    person_summary_text = "\n".join(p_text_list) if p_text_list else "• 특이 언급 인물 없음"
    person_summary_str_for_ai = "\n".join(person_summary_for_ai) if person_summary_for_ai else "특이 사항 없음"
    trend_summary_str_for_ai = "\n".join(trend_summary_for_ai) if trend_summary_for_ai else "전일 데이터 대비 유의미한 변동 없음"

    # ----- [한눈 카드: 이슈 스냅샷·인물 보강] -----
    issue_records = df_issues.to_dict("records") if df_issues is not None and len(df_issues) else []
    for row in issue_records:
        if "한동훈" in str(row.get("이슈명") or ""):
            for pr in hanun_person_rows:
                if "한동훈" in str(pr.get("name") or ""):
                    pr["from_issue"] = True
    prev_issues = (prev_data or {}).get("issues") if prev_data else None
    hanun_top_rows = df_top.head(10).to_dict("records") if df_top is not None and len(df_top) else []
    hanun_uncl_rows = df_unclassified_top5.to_dict("records") if df_unclassified_top5 is not None and len(df_unclassified_top5) else []
    hanun_html = build_hanun_card(
        now_str=now_str,
        issue_rows=issue_records,
        prev_issues=prev_issues,
        person_rows=hanun_person_rows,
        df_top_rows=hanun_top_rows,
        unclassified_rows=hanun_uncl_rows,
    )

    save_current_data({
        "timestamp": now_str,
        "persons": curr_persons_data,
        "frames": curr_frames_data,
        "issue_history": issue_history,
        "issues": issues_snapshot(issue_records),
    })

    ai_insight_text = generate_ai_insight(
        df_top, frame_stat_summary_str, person_summary_str_for_ai, 
        trend_summary_str_for_ai, frame_trend_str_for_ai, 
        issue_summary_str_for_ai, unclassified_summary_text, channel_volume_str_for_ai
    )

    ai_insight_text = normalize_report_text(ai_insight_text, "yeogwon")   # 장동혁 직함 고정·동명이인·금지어 '구조'
    title_warns = audit_person_titles(ai_insight_text, "ai_insight", PERSON_FACTS)
    camp_warns = audit_party_camp(ai_insight_text, "ai_insight", PERSON_FACTS)
    audit_footer = format_audit_footer(title_warns, camp_warns, mode="html")

    # ----- [보고서 메시지 작성] -----
    msg = f"<b>[여권 성향 유튜브 동향 리포트]</b>\n"
    msg += f"▪ 기준 시각: KST {now_str} (쇼츠 제외)\n\n"
    
    msg += f"<b>■ 모니터링 개요</b>\n"
    msg += f"• 대상 채널: 총 {total_target_channels}개 (수집 {collected_count}개)\n"
    if no_upload_channels:
        msg += f"• 신규 업로드 없음: [{', '.join(no_upload_channels)}]\n"
    if failed_channels:
        msg += f"• 수집 실패(API 오류): [{', '.join(failed_channels)}]\n"
    msg += f"• 수집 영상: 총 {total_videos}개 (10만+ 대박 영상: {hot_100k_count}개)\n"
    msg += f"• 채널별 수집 비중: {channel_volume_text}\n\n"

    msg += f"<b>■ 프레임별 현황 (건수 | 조회수 비중)</b>\n"
    msg += f"{frame_summary_text}\n\n"

    msg += f"<b>■ 주요 이슈 언급 (건수 | 채널 수 | 조회수)</b>\n"
    msg += "※ 종합방송(풀버전/OMNIBUS) 제외 집계 — 인물란(풀버전 포함)·조회 TOP10과 분모가 다름\n"
    msg += f"{issue_section_text}\n\n"

    msg += f"<b>■ 이슈 미분류 상위 5 (새 쟁점 후보)</b>\n"
    msg += f"{unclassified_summary_text}\n\n"

    msg += f"<b>■ 주요 인물 언급 현황 (건수 | 단독 조회수 기준 정렬)</b>\n"
    msg += f"{person_summary_text}\n\n"

    msg += f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"{ai_insight_text}\n\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━━━\n\n"

    msg += f"<b>■ 누적 조회수 TOP 10 (채널별 최대 2개)</b>\n\n"

    channel_counts_top = {}
    rank = 1

    for idx, row in df_top.iterrows():
        channel = row["채널명"]
        if channel_counts_top.get(channel, 0) >= 2:
            continue

        channel_counts_top[channel] = channel_counts_top.get(channel, 0) + 1

        views = row["조회수"]
        safe_title = html.escape(str(row["제목"]))
        safe_channel = html.escape(str(channel))
        frame_tag = f"[{row['프레임']}] " if row['프레임'] != "기타" else ""

        if is_stabilized_vph(row):
            vph_str = f"시간당 +{row['시간당조회수']:,}회"
        else:
            vph_str = "시간당 산출 제외"

        msg += f"<b>{rank}. [{safe_channel}]</b> ({row['게시일시']} | {row['경과시간']})\n"
        msg += f"   • 조회수: <b>{views:,}회</b> ({vph_str}) {frame_tag}\n"
        msg += f"   • 제목: {safe_title}\n"
        msg += f'   • 링크: <a href="{row["링크"]}">[영상 보기]</a>\n\n'

        rank += 1
        if rank > 10:
            break

    msg += f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"<b>■ 시간당 상승세 TOP 5 (라이브 6h/일반 1h 보정 / 채널별 1개)</b>\n\n"

    channel_counts_vph = {}
    vph_rank = 1

    for idx, row in df_vph.iterrows():
        channel = row["채널명"]
        if channel_counts_vph.get(channel, 0) >= 1:
            continue

        channel_counts_vph[channel] = 1

        safe_title = html.escape(str(row["제목"]))
        safe_channel = html.escape(str(channel))

        msg += f"<b>{vph_rank}. [{safe_channel}]</b> (시간당 +{row['시간당조회수']:,}회)\n"
        msg += f"   • 누적 조회수: {row['조회수']:,}회 ({row['경과시간']})\n"
        msg += f"   • 제목: {safe_title}\n"
        msg += f'   • 링크: <a href="{row["링크"]}">[영상 보기]</a>\n\n'

        vph_rank += 1
        if vph_rank > 5:
            break

    # 한눈 카드 먼저, 원문 리포트 이어짐 (텔레그램 메시지 2통+)
    send_telegram_message(hanun_html)
    # [Q&A Phase A] 이슈 한 줄 정의 → Cloudflare KV (실패해도 리포트 발송 계속)
    try:
        if DRY_RUN:
            defs = build_issue_defs(df, issue_records, issue_keywords_today)
            print(f"[DRY_RUN] KV 게시 생략 ({len((defs or {}).get('issues') or [])}건 정의 생성만)")
        else:
            publish_defs(build_issue_defs(df, issue_records, issue_keywords_today))
    except Exception as e:
        print(f"⚠️ [issue_defs] 생략: {redact_secrets(e)}")
    send_telegram_message(msg, audit_footer, expandable=True)


if __name__ == "__main__":
    run_monitoring()