import os
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

# 개별 단일 프레임 키워드
FRAME_KEYWORDS = {
    "전당대회/경선": [
        "전당대회", "최고위원", "당대표", "경선", "후보", "짝짓기", "투표전략", "경선후보",
        "토론", "재검표", "부정선거", "윤리위", "당규", "합동연설회", "창원", "전국당원대회", "정청래"
    ],
    "당내/인물": [
        "김민석", "이재명", "송영길", "박지원", "박은정", "이석현", "신인규", "반명", "친명", "최민희"
    ],
    "검찰/수사": ["검찰", "검수완박", "수사권", "공수처", "기소", "수사", "공소취소", "특검"],
    "과거정권/윤": ["윤석열", "김건희", "이태원", "내란", "계엄", "윤 정권", "대통령"],
    "민생/경제/정책": ["교육", "경제", "민생", "물가", "부동산", "교실", "코스피", "삼성전자", "레버리지", "ETF", "실적발표", "코스닥", "사이드카", "증시"],
}

# 종합방송 판정용 전용 키워드 (길이 90분 이상 조건과 병행)
OMNIBUS_KEYWORDS = ["뉴스공장 2026", "뉴스공장 월요일", "뉴스공장 화요일", "뉴스공장 수요일", "뉴스공장 목요일", "뉴스공장 금요일", "[full]", "매불쇼 8월", "김용민 브리핑] 아침7시"]


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

    # [보정] 영상 길이가 90분(5400초) 이상이면서 종합방송 전용 키워드가 포함된 경우만 종합방송 판정
    if duration_sec >= 5400:
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


def fetch_recent_videos(youtube, playlist_id, channel_name, max_results=8):
    video_list = []
    now_kst = datetime.now(KST)
    twenty_four_hours_ago = now_kst - timedelta(hours=24)

    try:
        playlist_response = youtube.playlistItems().list(
            part="snippet", playlistId=playlist_id, maxResults=max_results
        ).execute()

        video_ids = [item["snippet"]["resourceId"]["videoId"] for item in playlist_response.get("items", [])]
        if not video_ids:
            return video_list

        videos_response = youtube.videos().list(
            part="snippet,statistics,contentDetails", id=",".join(video_ids)
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

            if pub_time_kst >= twenty_four_hours_ago:
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
                
                # 라이브 감지 (12시에 만나요 포함)
                live_keywords = ["live", "라이브", "🔴", "12시에 만나요", "뉴스공장 2026", "매불쇼", "브리핑", "뉴잼스", "현장"]
                is_live_video = any(kw in title.lower() for kw in live_keywords)

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
    except Exception as e:
        print(f"영상 수집 오류 ({channel_name}): {e}")

    return video_list


def generate_ai_insight(df, frame_stat_summary):
    if not GEMINI_API_KEY:
        return "[AI 심층 분석 스킵: GEMINI_API_KEY 미설정]"

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)

        top_videos = df.head(10)[["채널명", "제목", "프레임", "조회수", "시간당조회수"]].to_dict(orient="records")

        prompt = f"""
        당신은 수치 간의 명확한 차이와 인사이트를 도출하는 수석 데이터 분석가입니다.
        아래 제공된 [상위 영상 데이터]와 [프레임별 현황]을 바탕으로 예리하고 날카로운 보고서 인사이트를 작성하세요.

        [상위 영상 데이터]
        {top_videos}

        [프레임별 현황 (건수비중 | 조회수비중)]
        {frame_stat_summary}

        [작성 규칙 - 반드시 준수]
        1. [가장 강력한 대비와 신호를 첫 문장으로 배치]: 종합방송을 제외한 단일 프레임 중 '민생/경제/정책' 프레임(단 1건)의 조회수 비중과 '전당대회/경선' 또는 '당내/인물' 프레임의 건수/조회수 비중을 직접 비교하여, 경선 국면 속 시청자 관심이 경제/증시로 이동하고 있음을 가장 첫 문장으로 강렬하게 지적하세요. (예: "민생/경제 프레임은 단 1건만으로 조회수 X%를 점유하여, N건에 Y%인 전당대회 프레임 대비 개별 효율에서 압도적 차이를 보였습니다. 전당대회가 진행 중임에도 시청자의 실질적 화력은 증시/경제 현안으로 집중되고 있습니다.")
        2. [단순 나열 및 원인 추측 금지]: 단순히 수치를 나열하거나 "피로도가 가중되었다" 같은 주관적 추측을 피하고, 편당 조회 효율 차이와 구도 변화만 날카롭게 짚으세요.
        3. [패널 이름 제외]: 홍사훈, 주진우, 노근창, 이선민 등 단순 방송 패널/출연자 이름을 제외하고 정치인(정청래, 김민석 등) 및 핵심 이슈 단어만 포함하세요.
        4. [이모티콘 사용 금지]: 깔끔한 문체로 작성하세요.

        [출력 양식]
        <b>[AI 데이터 심층 분석]</b>

        1. 핵심 기류
        - (건수 대비 조회 효율의 차이와 관심사 이동 현황을 짚는 임팩트 있는 2문장)

        2. 주요 언급 키워드
        - (단순 패널을 제외한 핵심 정치인 및 이슈 키워드 나열)

        3. 모니터링 시사점
        - (데이터 수치에 기반한 전략적 관전 포인트 1문장)
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
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        response = requests.post(url, data=payload, timeout=10)
        return response.json()
    except Exception as e:
        print(f"❌ 텔레그램 통신 에러: {e}")
        return None


def run_monitoring():
    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    all_data = []

    for channel_name, channel_id in TARGET_CHANNELS.items():
        uploads_id = get_channel_uploads_playlist_id(youtube, channel_id)
        if uploads_id:
            videos = fetch_recent_videos(youtube, uploads_id, channel_name, max_results=8)
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

    # 누적 TOP용: 전체 영상 대상 (필터링 미적용, 실제 누적 조회수 정렬)
    df_top = df.sort_values(by="조회수", ascending=False).reset_index(drop=True)

    # 시당 TOP용: 라이브 6시간 / 일반 1시간 보정 필터 적용
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

    # ----- [프레임별 건수 및 조회수 비중 집계] -----
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

        if f_name == "종합방송":
            note = " (코너 혼재, 프레임 판정 불가)"
        else:
            note = ""

        frame_summary_lines.append(f"• {f_name} : <b>{cnt}개</b> ({cnt_ratio}%) | <b>{view_ratio}%</b>{note}")
        frame_stat_summary_for_ai.append(f"- {f_name}: {cnt}개({cnt_ratio}%) | 조회비중 {view_ratio}%{note}")

    frame_summary_text = "\n".join(frame_summary_lines)
    frame_stat_summary_str = "\n".join(frame_stat_summary_for_ai)

    ai_insight_text = generate_ai_insight(df_top, frame_stat_summary_str)

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

    msg += f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"{ai_insight_text}\n\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━━━\n\n"

    # ----- 1. 누적 조회수 TOP 10 (전체 대상 복원) -----
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

        # 누적 상위 표시 시, 시간당 수치는 쿨다운 대상일 경우 '(시당 산출 제외)'로 표기
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

    # ----- 2. 시간당 조회수 TOP 5 -----
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