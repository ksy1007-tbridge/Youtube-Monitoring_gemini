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
PREV_DATA_FILE = "previous_data.json"

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

TRACK_PERSONS = ["정청래", "김민석", "최민희", "이재명", "송영길", "이석현", "한민수", "최강욱", "이성윤", "정봉주"]

FRAME_KEYWORDS = {
    "언론/미디어": ["언론", "진보언론", "편파보도", "기자", "방송", "세탁", "왜곡", "기괴한", "기사", "보도"],
    "민생/경제/정책": ["교육", "경제", "민생", "물가", "부동산", "교실", "코스피", "삼성전자", "레버리지", "ETF", "실적발표", "코스닥", "사이드카", "증시", "소상공인", "대통령", "세제"],
    "전당대회/경선": ["전당대회", "최고위원", "당대표", "경선", "후보", "짝짓기", "투표전략", "경선후보", "토론", "재검표", "부정선거", "윤리위", "당규", "합동연설회", "전국당원대회"],
    "당내/인물": ["정청래", "김민석", "이재명", "송영길", "박지원", "박은정", "이석현", "신인규", "반명", "친명", "최민희", "스캔들", "친청계", "반명몰이", "민심이반", "팀김어준", "뉴스비평", "신천지"],
    "검찰/수사": ["검찰", "검수완박", "수사권", "공수처", "기소", "수사", "공소취소", "특검", "보완수사권"],
    "과거정권/윤": ["윤석열", "김건희", "이태원", "내란", "계엄", "윤 정권"],
}

OMNIBUS_KEYWORDS = [
    "뉴스공장 2026", "뉴스공장 월요일", "뉴스공장 화요일", "뉴스공장 수요일", "뉴스공장 목요일", "뉴스공장 금요일",
    "[full]", "풀버전", "풀방송", "김용민 브리핑] 아침7시", "뉴스하이킥 full", "시선집중 full"
]


def parse_iso8601_duration(duration_str):
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


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
    return "기타"


def get_channel_uploads_playlist_id(youtube, channel_id):
    try:
        response = youtube.channels().list(part="contentDetails", id=channel_id).execute()
        items = response.get("items", [])
        if items:
            return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    except Exception as e:
        print(f"채널 ID({channel_id}) 조회 실패: {e}")
    return None


def fetch_recent_videos(youtube, playlist_id, channel_name):
    video_list = []
    now_kst = datetime.now(KST)
    twenty_four_hours_ago = now_kst - timedelta(hours=24)
    next_page_token = None

    while True:
        try:
            playlist_response = youtube.playlistItems().list(
                part="snippet",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=next_page_token
            ).execute()

            video_ids = [item["snippet"]["resourceId"]["videoId"] for item in playlist_response.get("items", [])]
            if not video_ids:
                break

            videos_response = youtube.videos().list(
                part="snippet,statistics,contentDetails",
                id=",".join(video_ids)
            ).execute()

            stop_pagination = False
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

                if pub_time_kst < twenty_four_hours_ago:
                    stop_pagination = True
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
                
                live_keywords = ["live", "라이브", "🔴", "12시에 만나요", "현장live"]
                is_live_video = any(kw in title.lower() for kw in live_keywords) or (duration_sec >= 5400 and "full" in title.lower())

                video_list.append({
                    "채널명": channel_name,
                    "제목": title,
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

            next_page_token = playlist_response.get("nextPageToken")
            if not next_page_token or stop_pagination:
                break

        except Exception as e:
            print(f"영상 수집 오류 ({channel_name}): {e}")
            break

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


def generate_ai_insight(df_top, frame_stat_summary, person_summary_str, trend_summary_str):
    if not GEMINI_API_KEY:
        return "[AI 심층 분석 스킵: GEMINI_API_KEY 미설정]"

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)

        top_videos = df_top.head(10)[["채널명", "제목", "프레임", "조회수"]].to_dict(orient="records")

        prompt = f"""
        당신은 수치와 현상 대립을 엄밀히 분석하는 수석 데이터 분석가입니다.
        아래 제공된 [상위 영상 데이터], [프레임별 현황], [주요 인물 현황 및 전일 대비 변동]만을 바탕으로 리포트를 작성하세요.

        [상위 영상 데이터]
        {top_videos}

        [프레임별 현황 (건수비중 | 조회수비중)]
        {frame_stat_summary}

        [주요 인물 언급 현황]
        {person_summary_str}

        [전일 대비 주요 변동 추세]
        {trend_summary_str}

        [엄격한 작성 규칙 - 반드시 준수]
        1. ['기타'/'종합방송' 프레임 오독 금지]: '기타'나 '종합방송' 프레임에 대해 "시청자의 집중적 관심을 받았다"는 식의 의미 부여를 하지 마세요. 미분류 프레임임을 전제로 단일 이슈 프레임 중심으로 해석하세요.
        2. [전일 대비 변동성 강조]: [전일 대비 주요 변동 추세] 데이터를 바탕으로 특정 인물(예: 최민희 등)의 조회수 급증/급감이나 프레임 공격-방어 전환(예: 신천지 프레임 반격 등)을 핵심 기류 첫 문장에서 지목하세요.
        3. [진영 간 대립구도 반영]: 동일 사건(예: 특정 방송 출연/질의)을 두고 채널 간 시각이 정반대로 엇갈리는 프레임 대립이 보일 경우 이를 짚어주세요.
        4. [키워드 범주 제한]: '주요 언급 키워드'는 파편화된 조각(예: 세탁 효과, 물구나무 등)을 배제하고, [주요 인물, 핵심 정치 이슈, 대립 사건]으로만 8~10개 엄선하여 작성하세요.
        5. [언급량 평가 금지 및 이모티콘 엄금]: "확보", "우세" 등의 단어와 이모티콘을 쓰지 마세요.

        [출력 양식]
        <b>[AI 데이터 심층 분석]</b>

        1. 핵심 기류
        - (전일 대비 인물/프레임 변동 추세와 진영 간 시각 대립 현황을 포함해 2문장 요약)

        2. 주요 언급 키워드
        - (주요 인물 및 핵심 정치 이슈 키워드 나열)

        3. 모니터링 관측 평가
        - (데이터 변동에 기반한 분석가 관점의 총평 1문장)
        """

        primary_model = 'gemini-3-flash-preview'
        fallback_model = 'gemini-2.0-flash'

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
        return f"[AI 심층 분석 생성 오류: {e}]"


def send_telegram_message(message):
    """텔레그램 메시지 길이에 맞춰 3,800자 단위 분할 전송 및 결과 로그 출력"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
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
            "chat_id": TELEGRAM_CHAT_ID,
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

    for channel_name, channel_id in TARGET_CHANNELS.items():
        uploads_id = get_channel_uploads_playlist_id(youtube, channel_id)
        if uploads_id:
            videos = fetch_recent_videos(youtube, uploads_id, channel_name)
            all_data.extend(videos)

    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    total_target_channels = len(TARGET_CHANNELS)

    if not all_data:
        send_telegram_message(
            f"<b>[여권 성향 유튜브 모니터링 리포트]</b>\n({now_str} KST)\n\n"
            f"최근 24시간 이내에 업로드된 일반 영상이 없습니다."
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
    collected_channels_set = set(df["채널명"].unique())
    all_channels_set = set(TARGET_CHANNELS.keys())
    missing_channels = list(all_channels_set - collected_channels_set)
    
    collected_count = len(collected_channels_set)
    missing_count = len(missing_channels)
    missing_str = ", ".join(missing_channels) if missing_channels else "없음"
    hot_100k_count = len(df[df["조회수"] >= 100000])

    # ----- [1. 프레임별 건수 및 조회수 비중 집계] -----
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

    for _, row in frame_group.iterrows():
        f_name = row["프레임"]
        cnt = int(row["건수"])
        cnt_ratio = row["건수비중"]
        view_ratio = row["조회비중"]

        note = " (코너 혼재, 프레임 판정 불가)" if f_name == "종합방송" else ""

        frame_summary_lines.append(f"• {f_name} : <b>{cnt}개</b> ({cnt_ratio}%) | <b>{view_ratio}%</b>{note}")
        frame_stat_summary_for_ai.append(f"- {f_name}: {cnt}개({cnt_ratio}%) | 조회비중 {view_ratio}%{note}")

    frame_summary_text = "\n".join(frame_summary_lines)
    frame_stat_summary_str = "\n".join(frame_stat_summary_for_ai)

    # ----- [2. 주요 인물별 언급 및 전일 대비 변동 계산] -----
    prev_data = load_previous_data()
    prev_persons = prev_data.get("persons", {}) if prev_data else {}

    curr_persons_data = {}
    person_summary_lines = []
    person_summary_for_ai = []
    trend_summary_for_ai = []

    for p in TRACK_PERSONS:
        sel = df[df["제목"].str.contains(p, regex=False)]
        p_cnt = len(sel)
        p_views = int(sel["조회수"].sum()) if p_cnt > 0 else 0

        curr_persons_data[p] = {"cnt": p_cnt, "views": p_views}

        if p_cnt > 0:
            diff_str = ""
            if p in prev_persons:
                prev_views = prev_persons[p].get("views", 0)
                diff = p_views - prev_views
                if prev_views > 0:
                    pct = round((diff / prev_views) * 100, 1)
                    sign = "+" if pct >= 0 else ""
                    diff_str = f" (전일 대비 {sign}{pct}%)"
                    trend_summary_for_ai.append(f"- {p}: 전일 {prev_views:,}회 -> 금일 {p_views:,}회 ({sign}{pct}%)")

            person_summary_lines.append((p, p_cnt, p_views, f"• {p} : <b>{p_cnt}건</b> (총 {p_views:,}회){diff_str}"))
            person_summary_for_ai.append(f"- {p}: {p_cnt}건 (조회수 {p_views:,}회)")

    person_summary_lines.sort(key=lambda x: x[2], reverse=True)
    
    p_text_list = [item[3] for item in person_summary_lines]
    person_summary_text = "\n".join(p_text_list) if p_text_list else "• 특이 언급 인물 없음"
    person_summary_str_for_ai = "\n".join(person_summary_for_ai) if person_summary_for_ai else "특이 사항 없음"
    trend_summary_str_for_ai = "\n".join(trend_summary_for_ai) if trend_summary_for_ai else "전일 데이터 대비 유의미한 변동 없음"

    save_current_data({"timestamp": now_str, "persons": curr_persons_data})

    ai_insight_text = generate_ai_insight(df_top, frame_stat_summary_str, person_summary_str_for_ai, trend_summary_str_for_ai)

    # ----- [보고서 메시지 작성] -----
    msg = f"<b>[여권 성향 유튜브 동향 리포트]</b>\n"
    msg += f"▪ 기준 시각: KST {now_str} (쇼츠 제외)\n\n"
    
    msg += f"<b>■ 모니터링 개요</b>\n"
    msg += f"• 대상 채널: 총 {total_target_channels}개 (수집 {collected_count}개 / 미수집 {missing_count}개)\n"
    if missing_channels:
        msg += f"• 미수집 채널: [{missing_str}]\n"
    msg += f"• 수집 영상: 총 {total_videos}개 (10만+ 대박 영상: {hot_100k_count}개)\n\n"

    msg += f"<b>■ 프레임별 현황 (건수 | 조회수 비중)</b>\n"
    msg += f"{frame_summary_text}\n\n"

    msg += f"<b>■ 주요 인물 언급 현황 (건수 | 총조회수)</b>\n"
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
            vph_str = "시당 산출 제외"

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