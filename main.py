import os
import json
import requests
import re
import html
from googleapiclient.discovery import build
from datetime import datetime, timedelta, timezone
import pandas as pd
from google import genai

# ---------------------------------------------------------
# [환경 변수 로드]
# ---------------------------------------------------------
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

KST = timezone(timedelta(hours=9))
PREV_DATA_FILE = os.environ.get("PREV_DATA_FILE", "previous_data.json")

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
}

# ---------------------------------------------------------
# [이슈 키워드 그룹 재정의 - 오탐 제거 및 구조화]
# 키워드는 re.escape 후 OR 매칭 (정규식 특수문자 안전)
# ---------------------------------------------------------
ISSUE_KEYWORDS = {
    "여론조사 조작 공방": [
        "여론조사 조작", "여조 조작", "여조 공작", "조작 폭로", "조작 방송",
        "허위 응답", "중복 투표", "1인 84표", "1인 80표", "강진구", "이상호 기자"
    ],
    "김어준 논란": ["김어준"],  # 정제 제목(제목_정제) 기준 매칭
    "당내 계파 갈등": ["친청", "친명", "당내 반란", "계파", "지도부 갈등", "최민희", "탈당"],
    "조희대/사법부": ["조희대", "대법원장"],
    "부동산·증시": ["부동산", "세제", "코스피", "삼성전자", "주주환원", "집값"],
    "유시민 논란": ["유시민"],
    "중수청/김지용": ["김지용", "중수청"],
    "외교·순방(UN·걸프·우크라)": ["순방", "유엔", "UN총회", "UN 연설", "걸프", "정상회담", "젤렌스키", "우크라", "포로 송환", "포로송환"],
    "DMZ 지뢰/안보": ["DMZ", "비무장지대", "지뢰"],
    "강훈식·성남라인": ["강훈식", "성남라인", "성남 라인"],
}

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

DYNAMIC_MAX_ISSUES = 5
DYNAMIC_MAX_PERSONS = 5
DYNAMIC_MIN_TITLES = 3
DYNAMIC_MIN_CHANNELS = 2
DYNAMIC_TAG = " (자동)"
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
    return f" | 최대채널 {html.escape(conc['top_ch'])} {conc['top_share']}%{flag}"


def format_concentration_ai(conc) -> str:
    if not conc:
        return ""
    flag = " [편중]" if conc["flagged"] else ""
    return f" | 채널집중: {conc['top_ch']} {conc['top_share']}%, 상위2개 영상 {conc['top2_vid_share']}%{flag}"


def match_titles(series, keywords):
    pattern = keywords_pattern(keywords)
    if not pattern:
        return series.str.contains(r"(?!x)x", regex=True, na=False)
    return series.str.contains(pattern, regex=True, na=False)


def extract_major_issues(df, issue_keywords=None):
    issue_keywords = issue_keywords or ISSUE_KEYWORDS
    issue_results = []
    matched_indices = set()
    issue_debug = {}

    df_matchable = df[df["프레임"] != "종합방송"]

    for issue_name, keywords in issue_keywords.items():
        titles = df_matchable["제목_정제"]
        mask = match_titles(titles, keywords) | context_mask(titles, ISSUE_CONTEXT_LINKS.get(issue_name))
        sel = df_matchable[mask]

        cnt = len(sel)
        if cnt > 0:
            matched_indices.update(sel.index)
            channel_cnt = sel["채널명"].nunique()
            total_views = int(sel["조회수"].sum())
            has_mbc = "MBC 라디오 시사" in sel["채널명"].values
            mbc_tag = " [MBC 포함]" if has_mbc else " [진영 내부]"

            issue_results.append({
                "이슈명": issue_name,
                "건수": cnt,
                "채널수": channel_cnt,
                "조회수": total_views,
                "mbc_tag": mbc_tag,
                "concentration": channel_concentration(sel),
            })

            top5 = sel.sort_values(by="조회수", ascending=False).head(5)
            issue_debug[issue_name] = [
                {"채널": r["채널명"], "조회수": int(r["조회수"]), "제목": r["제목"]}
                for _, r in top5.iterrows()
            ]

    df_issues = pd.DataFrame(issue_results)
    if not df_issues.empty:
        df_issues = df_issues.sort_values(by=["채널수", "조회수"], ascending=[False, False]).reset_index(drop=True)

    unclassified_df = df.drop(index=list(matched_indices), errors="ignore")
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


def extract_dynamic_entities(all_titles):
    """제목 목록에서 오늘의 신규 이슈/인물 후보를 Gemini로 1회 추출.
    반환: {"issues": [{"name", "keywords"}], "persons": [{"name", "aliases"}]} / 실패 시 빈 후보."""
    empty = {"issues": [], "persons": []}
    api_key = GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY")
    if not api_key or not api_key.strip() or not all_titles:
        return empty

    try:
        client = genai.Client(api_key=api_key.strip())
        titles_text = "\n".join(f"- {t}" for t in all_titles[:150])
        prompt = f"""
        아래는 오늘 수집된 한국 시사 유튜브 영상 제목 목록입니다.
        여러 제목에 반복 등장하는 오늘의 핵심 이슈와 인물을 추출하세요.

        [규칙]
        - keywords/aliases는 반드시 아래 제목에 글자 그대로 등장하는 문자열만 쓰세요.
        - 성씨+직함(예: '한 전 대표', '김 청장', '이 대통령'), 한 글자, 직함 단독('대통령', '의원'), 짧은 영문 약어('UN')는 쓰지 마세요.
        - 이슈는 최대 8개, 인물은 최대 8명. 제목 3개 이상에 등장하는 것만.
        - 반드시 JSON만 출력: {{"issues":[{{"name":"이슈명","keywords":["키워드1","키워드2"]}}],"persons":[{{"name":"인물명","aliases":["인물명","별칭"]}}]}}

        [제목 목록]
        {titles_text}
        """

        primary_model = 'gemini-3.5-flash-lite'
        fallback_model = 'gemini-3.1-flash-lite'
        config = {"response_mime_type": "application/json", "temperature": 0.2}

        try:
            response = client.models.generate_content(model=primary_model, contents=prompt, config=config)
        except Exception as primary_e:
            print(f"⚠️ [자동 추출] {primary_model} 실패 ({primary_e}), {fallback_model}로 재시도")
            response = client.models.generate_content(model=fallback_model, contents=prompt, config=config)

        data = _parse_json_loose(getattr(response, "text", None))
        if not isinstance(data, dict):
            print("ℹ️ [자동 추출] JSON 파싱 실패 → 고정 사전만 사용")
            return empty

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
        print(f"ℹ️ [자동 추출] 실패 → 고정 사전만 사용 ({e})")
        return empty


def filter_dynamic_entities(df, candidates):
    """자동 추출 후보를 코드로 결정적으로 검증: 제목 >= DYNAMIC_MIN_TITLES, 채널 >= DYNAMIC_MIN_CHANNELS,
    고정 사전과 중복 제외, 최대 개수 제한. 반환: (issue_dict, person_dict) — 키에 '(자동)' 태그."""
    dyn_issues, dyn_persons = {}, {}
    try:
        fixed_issue_kws = {k for kws in ISSUE_KEYWORDS.values() for k in kws}
        fixed_person_aliases = {a for als in TRACK_PERSONS.values() for a in als} | set(TRACK_PERSONS.keys())
        fixed_person_aliases |= {trig for rules in CONTEXT_ALIASES.values() for trig, _ in rules}
        df_matchable = df[df["프레임"] != "종합방송"]

        def passes(frame_df, kws):
            sel = frame_df[match_titles(frame_df["제목_정제"], kws)]
            return len(sel) >= DYNAMIC_MIN_TITLES and sel["채널명"].nunique() >= DYNAMIC_MIN_CHANNELS

        for it in candidates.get("issues", []):
            if len(dyn_issues) >= DYNAMIC_MAX_ISSUES:
                break
            name = it["name"]
            kws = [k for k in it["keywords"] if k not in fixed_issue_kws and k not in fixed_person_aliases]
            if not kws or name in ISSUE_KEYWORDS or f"{name}{DYNAMIC_TAG}" in dyn_issues:
                continue
            if passes(df_matchable, kws):
                dyn_issues[f"{name}{DYNAMIC_TAG}"] = kws

        for it in candidates.get("persons", []):
            if len(dyn_persons) >= DYNAMIC_MAX_PERSONS:
                break
            name = it["name"]
            als = it["aliases"]
            if name in TRACK_PERSONS or any(a in fixed_person_aliases for a in als) or f"{name}{DYNAMIC_TAG}" in dyn_persons:
                continue
            if passes(df, als):
                dyn_persons[f"{name}{DYNAMIC_TAG}"] = als
    except Exception as e:
        print(f"ℹ️ [자동 추출] 검증 실패 → 고정 사전만 사용 ({e})")
        return {}, {}

    if dyn_issues or dyn_persons:
        print(f"ℹ️ [자동 추출] 이슈 {list(dyn_issues)} / 인물 {list(dyn_persons)}")
    return dyn_issues, dyn_persons


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
        print(f"채널 ID({channel_id}) 재생목록 조회 실패: {e}")
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
            print(f"플레이리스트 수집 에러 ({channel_name}): {e}")

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
        print(f"Search API 수집 에러 ({channel_name}): {e}")

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
                    "조회수": views,
                    "시간당조회수": views_per_hour,
                    "경과시간_hours": elapsed_hours,
                    "경과시간": elapsed_str,
                    "게시일시_dt": pub_time_kst,
                    "게시일시": pub_time_kst.strftime("%m-%d %H:%M"),
                    "링크": f"https://youtu.be/{item['id']}",
                    "is_live": is_live_video
                })

        except Exception as e:
            print(f"영상 상세 수집 에러 ({channel_name}): {e}")

    print(f"✅ [{channel_name}] 최종 수집: 총 {len(video_list)}개 영상")
    return video_list


def load_previous_data():
    if os.path.exists(PREV_DATA_FILE):
        try:
            with open(PREV_DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"이전 데이터 로드 실패: {e}")
    return None


def save_current_data(data):
    try:
        with open(PREV_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"현재 데이터 저장 실패: {e}")


def generate_ai_insight(df_top, frame_stat_summary, person_summary_str, trend_summary_str, frame_trend_str, issue_summary_str, unclassified_summary_str, channel_volume_str=""):
    api_key = GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY")
    if not api_key or api_key.strip() == "":
        print("❌ GEMINI_API_KEY가 로드되지 않았거나 값이 비어 있습니다.")
        return "<b>[AI 심층 분석 스킵: GEMINI_API_KEY 미설정]</b>"

    try:
        client = genai.Client(api_key=api_key.strip())

        top_videos = df_top.head(10)[["채널명", "제목", "프레임", "조회수"]].to_dict(orient="records")

        prompt = f"""
        당신은 수치 데이터와 실시간 프레임 전환을 정교하게 분석하는 수석 데이터 분석가입니다.
        과거 어제의 분석 프레임이나 지나간 이슈를 재활용하지 말고, 오직 오늘 수집된 데이터와 전일 대비 증감 지표만을 근거로 분석하세요.

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
        5. [키워드 범주 제한]: '주요 언급 키워드'는 오늘 상위 영상 제목에 실제 등장한 [주요 인물, 핵심 정치 이슈, 법적 대응/대립 사건]만 8~10개 엄선하세요. (야권 인물이나 단순 축구/가십성 인물 제외)
        6. [채널 편중 보정]: 인물·이슈의 최대채널 비중이 50%를 넘거나 [편중] 표시가 있으면 '대중적 관심 견인', '관심 집중', '폭발적 반응' 등으로 일반화하지 말고 '○○ 채널(비중 N%) 중심 조회'로 편중 사실을 함께 쓰세요. 채널 수 2곳 이하 이슈에는 '확산' 표현을 쓰지 마세요.

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

        try:
            response = client.models.generate_content(
                model=primary_model,
                contents=prompt,
            )
            return response.text
        except Exception as primary_e:
            print(f"⚠️ {primary_model} 실패 ({primary_e}), {fallback_model}로 재시도")
            response = client.models.generate_content(
                model=fallback_model,
                contents=prompt,
            )
            return response.text

    except Exception as e:
        return f"<b>[AI 심층 분석 생성 오류: {e}]</b>"


def send_telegram_message(message):
    token = TELEGRAM_BOT_TOKEN or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = TELEGRAM_CHAT_ID or os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print(f"❌ 텔레그램 전송 실패: 토큰 또는 CHAT_ID 환경 변수가 제대로 설정되지 않았습니다. (TOKEN: {token}, CHAT_ID: {chat_id})")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    MAX_LEN = 3800
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

    for i, chunk in enumerate(chunks, 1):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
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
            print(f"❌ 텔레그램 통신 에러 (Part {i}/{len(chunks)}): {e}")


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
            print(f"❌ [{channel_name}] 수집 중 예외 발생: {e}")
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

    # ----- [2-0. 오늘의 신규 이슈/인물 자동 추출 (실패 시 고정 사전만 사용)] -----
    dyn_candidates = extract_dynamic_entities(df["제목_정제"].drop_duplicates().tolist())
    dyn_issues, dyn_persons = filter_dynamic_entities(df, dyn_candidates)
    issue_keywords_today = {**ISSUE_KEYWORDS, **dyn_issues}
    person_aliases_today = {**TRACK_PERSONS, **dyn_persons}

    # ----- [2. 주요 확산 이슈 집계 및 미분류 상위 5 추출] -----
    df_issues, df_unclassified_top5 = extract_major_issues(df, issue_keywords_today)
    
    issue_summary_lines = []
    issue_summary_for_ai = []
    for _, row in df_issues.iterrows():
        conc = row.get("concentration")
        issue_summary_lines.append(
            f"• {html.escape(str(row['이슈명']))} : <b>{row['건수']}건</b> | <b>{row['채널수']}채널</b> | {row['조회수']:,}회{row['mbc_tag']}{format_concentration_report(conc)}"
        )
        issue_summary_for_ai.append(
            f"- {row['이슈명']}: {row['건수']}건 | {row['채널수']}채널 | {row['조회수']:,}회{row['mbc_tag']}{format_concentration_ai(conc)}"
        )
    issue_summary_text = "\n".join(issue_summary_lines) if issue_summary_lines else "• 특이 이슈 없음"
    issue_summary_str_for_ai = "\n".join(issue_summary_for_ai) if issue_summary_for_ai else "특이 이슈 없음"

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
            prev_v = 0
            diff_str = ""
            if p in prev_persons:
                prev_v = prev_persons[p].get("views_standalone", prev_persons[p].get("views", 0))
                if prev_v > 0:
                    pct = round(((p_views_standalone - prev_v) / prev_v) * 100, 1)
                    sign = "+" if pct >= 0 else ""
                    diff_str = f" | 전일 {prev_v:,}회 -> {sign}{pct}%"
                    trend_summary_for_ai.append(f"- {p}: 전일 단독 {prev_v:,}회 -> 금일 단독 {p_views_standalone:,}회 ({sign}{pct}%)")
                else:
                    diff_str = " | 전일 0회"
                    trend_summary_for_ai.append(f"- {p}: 전일 단독 0회 -> 금일 단독 {p_views_standalone:,}회 (신규 진입/급증)")

            if len(sel_omnibus) > 0 and len(sel_standalone) == 0:
                views_disp = f"단독 0회 (보정합산 {p_views_weighted:,}회 | 풀버전 {len(sel_omnibus)}건 포함 {p_views_total:,}회)"
            elif len(sel_omnibus) > 0:
                views_disp = f"단독 {p_views_standalone:,}회 (보정합산 {p_views_weighted:,}회 | 풀버전 포함 {p_views_total:,}회)"
            else:
                views_disp = f"단독 {p_views_standalone:,}회"

            conc = channel_concentration(sel_standalone)
            line_text = f"• {html.escape(p)} : <b>{p_cnt}건</b> ({views_disp}{format_concentration_report(conc)}{diff_str})"
            # 정렬 우선순위 키: 1순위 p_views_standalone(단독 조회수), 2순위 p_views_weighted(보정 조회수)
            person_summary_lines.append((p, p_cnt, p_views_standalone, p_views_weighted, p_views_total, line_text))
            person_summary_for_ai.append(f"- {p}: {p_cnt}건 (금일 단독 {p_views_standalone:,}회 / 전일 단독 {prev_v:,}회 / 보정합산 {p_views_weighted:,}회){format_concentration_ai(conc)}")

    # 단독 조회수(Standalone Views) 기준 1순위 정렬 고정 (착시 차단)
    person_summary_lines.sort(key=lambda x: (x[2], x[3], x[4]), reverse=True)
    
    p_text_list = [item[5] for item in person_summary_lines]
    person_summary_text = "\n".join(p_text_list) if p_text_list else "• 특이 언급 인물 없음"
    person_summary_str_for_ai = "\n".join(person_summary_for_ai) if person_summary_for_ai else "특이 사항 없음"
    trend_summary_str_for_ai = "\n".join(trend_summary_for_ai) if trend_summary_for_ai else "전일 데이터 대비 유의미한 변동 없음"

    save_current_data({
        "timestamp": now_str,
        "persons": curr_persons_data,
        "frames": curr_frames_data
    })

    ai_insight_text = generate_ai_insight(
        df_top, frame_stat_summary_str, person_summary_str_for_ai, 
        trend_summary_str_for_ai, frame_trend_str_for_ai, 
        issue_summary_str_for_ai, unclassified_summary_text, channel_volume_str_for_ai
    )

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
    msg += f"{issue_summary_text}\n\n"

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

    send_telegram_message(msg)


if __name__ == "__main__":
    run_monitoring()