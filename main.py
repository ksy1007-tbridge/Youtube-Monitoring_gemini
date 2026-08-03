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
                    "링크": f"https://youtu.be/{item['id']}"
                })
    except Exception as e:
        print(f"영상 수집 오류 ({channel_name}): {e}")

    return video_list


def generate_ai_insight(df):
    """Gemini AI를 활용해 수집된 영상 기반 심층 분석 및 인사이트 생성"""
    if not GEMINI_API_KEY:
        print("❌ [DEBUG] GEMINI_API_KEY 환경변수를 읽어오지 못했습니다.")
        return "⚠️ GEMINI_API_KEY가 설정되지 않아 AI 심층 분석을 스킵합니다."

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)

        top_videos = df.head(10)[["채널명", "제목", "프레임", "조회수", "시간당조회수"]].to_dict(orient="records")

        prompt = f"""
        아래는 주요 여권 성향 정치 유튜브 채널들의 상위 영상 데이터입니다:
        {top_videos}

        이 데이터를 분석하여 텔레그램 리포트에 포함할 심층 인사이트를 작성해 주세요.
        반드시 다음 HTML 양식 구조 그대로 작성해 주세요:

        <b>🧠 AI 심층 분석 인사이트</b>
        • <b>💡 핵심 기류</b>: (주요 이슈 요약 2문장)
        • <b>⚠️ 리스크/논쟁 키워드</b>: (주요 인물, 논쟁 키워드)
        • <b>🎯 대응 시사점</b>: (전략적 제언 1문장)
        """

        response = client.models.generate_content(
            model='gemini-3-flash-preview',
            contents=prompt,
        )
        return response.text
    except Exception as e:
        print(f"❌ Gemini API 호출 상세 오류: {e}")
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
        res_json = response.json()
        if not res_json.get("ok"):
            print(f"❌ 텔레그램 발송 실패: {res_json}")
        else:
            print("✅ 텔레그램 리포트 성공적으로 전송됨!")
        return res_json
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

    if not all_data:
        send_telegram_message(
            f"⚠️ <b>[여권 성향 유튜브 리포트]</b>\n({now_str} KST)\n\n"
            f"최근 24시간 이내에 업로드된 일반 영상이 없습니다."
        )
        return

    df = pd.DataFrame(all_data)
    df = df.sort_values(by="조회수", ascending=False).reset_index(drop=True)

    total_videos = len(df)
    unique_channels = df["채널명"].nunique()
    hot_100k_count = len(df[df["조회수"] >= 100000])
    top_video = df.iloc[0]

    df_min15 = df[df["경과시간_hours"] >= 0.25]
    if not df_min15.empty:
        df_vph = df_min15.sort_values(by="시간당조회수", ascending=False).reset_index(drop=True)
    else:
        df_vph = df.sort_values(by="시간당조회수", ascending=False).reset_index(drop=True)

    top_vph = df_vph.iloc[0]

    frame_counts = df["프레임"].value_counts().to_dict()
    frame_summary_parts = []
    for frame, cnt in sorted(frame_counts.items(), key=lambda x: -x[1]):
        if frame != "기타" or cnt >= 2:
            frame_summary_parts.append(f"{frame} {cnt}개")
    frame_summary = ", ".join(frame_summary_parts) if frame_summary_parts else "분류 없음"

    top_channel_safe = html.escape(str(top_video["채널명"]))
    top_vph_channel_safe = html.escape(str(top_vph["채널명"]))

    ai_insight_text = generate_ai_insight(df)

    msg = f"📊 <b>여권 성향 유튜브 모니터링</b>\n"
    msg += f"⏱ 기준: KST {now_str} (쇼츠 제외)\n\n"
    msg += f"💡 <b>[오늘의 요약]</b>\n"
    msg += f"• 수집 영상: <b>{total_videos}개</b> (채널 {unique_channels}개)\n"
    msg += f"• 🔥 10만+ 대박 영상: <b>{hot_100k_count}개</b>\n"
    msg += f"• 👑 조회수 1위: <b>[{top_channel_safe}]</b> ({top_video['조회수']:,}회)\n"
    msg += f"• 🚀 시당 1위: <b>[{top_vph_channel_safe}]</b> (시당 +{top_vph['시간당조회수']:,}회)\n"
    msg += f"• 📌 주요 프레임: {frame_summary}\n\n"

    msg += f"───────────────────\n\n"
    msg += f"{ai_insight_text}\n\n"
    msg += f"───────────────────\n\n"

    # ----- 1. 조회수 기준 TOP -----
    msg += f"<b>📈 조회수 TOP (채널별 상위 2개 제한)</b>\n\n"

    channel_counts_top = {}
    rank = 1

    for idx, row in df.iterrows():
        channel = row["채널명"]
        current_count = channel_counts_top.get(channel, 0)
        if current_count >= 2:
            continue

        channel_counts_top[channel] = current_count + 1

        views = row["조회수"]
        views_formatted = f"{views:,}"
        vph_formatted = f"{row['시간당조회수']:,}"

        safe_title = html.escape(str(row["제목"]))
        safe_channel = html.escape(str(channel))
        frame_tag = f"[{row['프레임']}] " if row["프레임"] != "기타" else ""

        if views >= 500000:
            badge = "💥 "
        elif views >= 100000:
            badge = "🔥 "
        else:
            badge = "🔹 "

        msg += f"{rank}. {badge}<b>[{safe_channel}]</b> ({row['게시일시']} | {row['경과시간']})\n"
        msg += f"   👁 <b>{views_formatted}회</b> (시당 +{vph_formatted}회) {frame_tag}\n"
        msg += f"   🎬 {safe_title}\n"
        msg += f'   👉 <a href="{row["링크"]}">[영상 보기]</a>\n\n'

        rank += 1
        if rank > 10:
            break

    # ----- 2. 시당 조회수 기준 TOP 5 -----
    msg += f"───────────────────\n\n"
    msg += f"<b>🚀 지금 뜨는 영상 TOP 5 (15분 이상 경과 / 채널별 1개)</b>\n\n"

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