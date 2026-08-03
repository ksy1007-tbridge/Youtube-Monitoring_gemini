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

FRAME_KEYWORDS = {
    "전당대회/경선": [
        "전당대회", "최고위원", "당대표", "경선", "후보", "짝짓기", "투표전략", "경선후보",
        "토론", "재검표", "부정선거", "윤리위", "당규"
    ],
    "당내/인물": [
        "정청래", "김민석", "이재명", "송영길", "박지원", "박은정", "홍사훈",
        "주진우", "이석현", "신인규", "이이제이", "반명"
    ],
    "검찰/수사": ["검찰", "검수완박", "수사권", "공수처", "기소", "수사", "공소취소", "특검"],
    "과거정권/윤": ["윤석열", "김건희", "이태원", "내란", "계엄", "윤 정권", "대통령"],
    "민생/경제/정책": ["교육", "경제", "민생", "물가", "부동산", "교실", "코스피", "삼성전자", "레버리지", "ETF", "실적발표"],
}


def parse_iso8601_duration(duration_str):
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def classify_frame(title: str) -> str:
    title_lower = title.lower()
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
                is_live_video = any(kw in title.lower() for kw in ["live", "라이브"])

                video_list.append({
                    "채널명": channel_name,
                    "제목": title,
                    "프레임": classify_frame(title),
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
        return "⚠️ GEMINI_API_KEY가 설정되지 않아 AI 심층 분석을 스킵합니다."

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)

        top_videos = df.head(10)[["채널명", "제목", "프레임", "조회수", "시간당조회수"]].to_dict(orient="records")

        prompt = f"""
        당신은 수집된 유튜브 모니터링 데이터를 엄밀히 분석하는 수석 데이터 분석가입니다.
        아래 제공된 [상위 영상 데이터]와 [프레임별 건수 및 조회수 비중]만을 바탕으로 인사이트를 작성하세요.

        [상위 영상 데이터]
        {top_videos}

        [프레임별 현황 (건수 및 조회수 비중)]
        {frame_stat_summary}

        [엄격한 분석 및 작성 규칙]
        1. [건수 vs 조회수 대비 강조]: 영상 업로드 건수가 적더라도 조회수 비중이나 시당 순위가 높다면 "단 X건임에도 조회수 비중 Y%를 차지" 또는 "건수는 적으나 상위권 독점"과 같이 수량과 영향력의 차이를 반드시 대비시켜 설명하세요.
        2. [텍스트 팩트 엄수]: 채널명(예: 호남뉴탐사)에 포함된 지명이나 단어를 정치 현안 키워드로 왜곡하지 마세요. 오직 '영상 제목'에 직접 적힌 단어만 인용하세요.
        3. [지어내기 엄금]: 제목에 없는 경선 결과, 승리 여부, 외부 정치 뉴스, 의리/거짓말 논란 등을 절대 추측하여 쓰지 마세요.
        4. [제목 원문 반영]: 경제 관련 수치는 제목 표기('코스피 하락, 코스닥 매수 사이드카… 엇갈린 증시') 그대로 반영하고 '폭락'으로 뭉뚱그리지 마세요.

        [출력 양식]
        <b>🧠 AI 심층 분석 인사이트</b>
        • <b>💡 핵심 기류</b>: (건수 대비 조회수 집중도를 반영하여 영상 제목 현황 요약 2문장)
        • <b>⚠️ 주요 언급 키워드</b>: (제목에 등장한 주요 인물 및 명확한 단어만 나열)
        • <b>🎯 모니터링 시사점</b>: (데이터 현황에 기반한 시사점 1문장)
        """

        response = client.models.generate_content(
            model='gemini-3-flash-preview',
            contents=prompt,
        )
        return response.text
    except Exception as e:
        return f"⚠️ AI 심층 분석 생성 중 오류가 발생했습니다: {e}"


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
            f"⚠️ <b>[여권 성향 유튜브 리포트]</b>\n({now_str} KST)\n\n"
            f"최근 24시간 이내에 업로드된 일반 영상이 없습니다."
        )
        return

    df = pd.DataFrame(all_data)
    df = df.sort_values(by="조회수", ascending=False).reset_index(drop=True)

    total_videos = len(df)
    collected_channels = df["채널명"].nunique()
    zero_channels = total_target_channels - collected_channels
    hot_100k_count = len(df[df["조회수"] >= 100000])
    top_video = df.iloc[0]

    # ----- [라이브 시당 착시 방지 보정 필터] -----
    # 일반 영상: 1시간 이상 경과 / 라이브 영상: 6시간 이상 경과
    def filter_vph_candidate(row):
        if row["is_live"]:
            return row["경과시간_hours"] >= 6.0
        return row["경과시간_hours"] >= 1.0

    df_vph_candidates = df[df.apply(filter_vph_candidate, axis=1)]
    if not df_vph_candidates.empty:
        df_vph = df_vph_candidates.sort_values(by="시간당조회수", ascending=False).reset_index(drop=True)
    else:
        df_vph = df.sort_values(by="시간당조회수", ascending=False).reset_index(drop=True)

    top_vph = df_vph.iloc[0]

    # ----- [프레임별 건수 및 조회수 비중 집계] -----
    total_views = df["조회수"].sum()
    frame_group = df.groupby("프레임").agg(
        건수=("조회수", "count"),
        총조회수=("조회수", "sum")
    ).reset_index()

    frame_group["조회비중"] = (frame_group["총조회수"] / total_views * 100).round(1)
    frame_group = frame_group.sort_values(by="총조회수", ascending=False)

    frame_summary_lines = []
    frame_stat_summary_for_ai = []

    for _, row in frame_group.iterrows():
        f_name = row["프레임"]
        cnt = int(row["건수"])
        ratio = row["조회비중"]
        frame_summary_lines.append(f"• {f_name}: <b>{cnt}개</b> | <b>{ratio}%</b>")
        frame_stat_summary_for_ai.append(f"- {f_name}: {cnt}개 ({ratio}%)")

    frame_summary_text = "\n".join(frame_summary_lines)
    frame_stat_summary_str = "\n".join(frame_stat_summary_for_ai)

    top_channel_safe = html.escape(str(top_video["채널명"]))
    top_vph_channel_safe = html.escape(str(top_vph["채널명"]))

    ai_insight_text = generate_ai_insight(df, frame_stat_summary_str)

    msg = f"📊 <b>여권 성향 유튜브 모니터링</b>\n"
    msg += f"⏱ 기준: KST {now_str} (쇼츠 제외)\n\n"
    msg += f"💡 <b>[오늘의 요약]</b>\n"
    msg += f"• 모니터링 채널: <b>총 {total_target_channels}개</b> (수집: {collected_channels}개, 미수집: {zero_channels}개)\n"
    msg += f"• 수집 영상: <b>{total_videos}개</b> | 🔥 10만+ 대박: <b>{hot_100k_count}개</b>\n"
    msg += f"• 👑 누적 조회수 1위: <b>[{top_channel_safe}]</b> ({top_video['조회수']:,}회)\n"
    msg += f"• 🚀 시당 1위 (보정): <b>[{top_vph_channel_safe}]</b> (시당 +{top_vph['시간당조회수']:,}회)\n\n"

    msg += f"📌 <b>프레임 집계 (건수 | 조회수 비중)</b>\n"
    msg += f"{frame_summary_text}\n\n"

    msg += f"───────────────────\n\n"
    msg += f"{ai_insight_text}\n\n"
    msg += f"───────────────────\n\n"

    # ----- 1. 조회수 TOP -----
    msg += f"<b>📈 조회수 TOP (채널별 상위 2개 제한)</b>\n\n"

    channel_counts_top = {}
    rank = 1

    for idx, row in df.iterrows():
        channel = row["채널명"]
        if channel_counts_top.get(channel, 0) >= 2:
            continue

        channel_counts_top[channel] = channel_counts_top.get(channel, 0) + 1

        views = row["조회수"]
        safe_title = html.escape(str(row["제목"]))
        safe_channel = html.escape(str(channel))
        frame_tag = f"[{row['프레임']}] " if row["프레임"] != "기타" else ""

        badge = "💥 " if views >= 500000 else ("🔥 " if views >= 100000 else "🔹 ")

        msg += f"{rank}. {badge}<b>[{safe_channel}]</b> ({row['게시일시']} | {row['경과시간']})\n"
        msg += f"   👁 <b>{views:,}회</b> (시당 +{row['시간당조회수']:,}회) {frame_tag}\n"
        msg += f"   🎬 {safe_title}\n"
        msg += f'   👉 <a href="{row["링크"]}">[영상 보기]</a>\n\n'

        rank += 1
        if rank > 10:
            break

    # ----- 2. 시당 조회수 TOP 5 -----
    msg += f"───────────────────\n\n"
    msg += f"<b>🚀 지금 뜨는 영상 TOP 5 (라이브 6h/일반 1h 보정 / 채널별 1개)</b>\n\n"

    channel_counts_vph = {}
    vph_rank = 1

    for idx, row in df_vph.iterrows():
        channel = row["채널명"]
        if channel_counts_vph.get(channel, 0) >= 1:
            continue

        channel_counts_vph[channel] = 1

        safe_title = html.escape(str(row["제목"]))
        safe_channel = html.escape(str(channel))
        frame_tag = f"[{row['프레임']}] " if row["프레임"] != "기타" else ""

        msg += f"{vph_rank}. <b>[{safe_channel}]</b> 시당 +{row['시간당조회수']:,}회 ({row['경과시간']})\n"
        msg += f"   👁 현재 {row['조회수']:,}회 {frame_tag}\n"
        msg += f"   🎬 {safe_title}\n"
        msg += f'   👉 <a href="{row["링크"]}">[영상 보기]</a>\n\n'

        vph_rank += 1
        if vph_rank > 5:
            break

    send_telegram_message(msg)


if __name__ == "__main__":
    run_monitoring()