import os
import requests
import re
from googleapiclient.discovery import build
from datetime import datetime, timedelta, timezone
import pandas as pd

# ---------------------------------------------------------
# [환경 변수 로드]
# ---------------------------------------------------------
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# KST (한국 표준시) 시간대 설정 (UTC+9)
KST = timezone(timedelta(hours=9))

# ---------------------------------------------------------
# [모니터링 대상 채널 목록] (채널명: 실제 채널 ID)
# ---------------------------------------------------------
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

def parse_iso8601_duration(duration_str):
    """ISO 8601 영상 재생시간(PT1M30S 등)을 초(second) 단위로 변환"""
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds

def get_channel_uploads_playlist_id(youtube, channel_id):
    """채널의 업로드 전용 재생목록 ID 조회"""
    try:
        response = youtube.channels().list(part="contentDetails", id=channel_id).execute()
        items = response.get("items", [])
        if items:
            return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    except Exception as e:
        print(f"채널 ID({channel_id}) 조회 실패: {e}")
    return None

def fetch_recent_videos(youtube, playlist_id, channel_name, max_results=8):
    """최근 24시간 이내, 라이브 대기방 및 쇼츠(60초 이하) 제외 영상 수집"""
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

        # contentDetails(재생시간) 파트 추가 수집
        videos_response = youtube.videos().list(
            part="snippet,statistics,contentDetails,liveStreamingDetails", id=",".join(video_ids)
        ).execute()

        for item in videos_response.get("items", []):
            snippet = item["snippet"]
            stats = item.get("statistics", {})
            content_details = item.get("contentDetails", {})
            
            # 1. 라이브 대기방(upcoming) 필터링
            live_status = snippet.get("liveBroadcastContent", "none")
            if live_status == "upcoming":
                continue

            # 2. 쇼츠 제외 (재생시간 60초 이하 필터링)
            duration_sec = parse_iso8601_duration(content_details.get("duration", "PT0S"))
            if duration_sec > 0 and duration_sec <= 60:
                continue

            # 3. 업로드 시각 KST 변환 및 24시간 이내 필터링
            pub_time_utc = datetime.strptime(snippet["publishedAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            pub_time_kst = pub_time_utc.astimezone(KST)

            if pub_time_kst >= twenty_four_hours_ago:
                # 4. 경과 시간 및 시간당 조회수 계산
                elapsed_hours = (now_kst - pub_time_kst).total_seconds() / 3600.0
                elapsed_hours = max(elapsed_hours, 0.1) # 0으로 나누기 방지
                
                views = int(stats.get("viewCount", 0))
                views_per_hour = int(views / elapsed_hours)

                # 경과시간 텍스트 포맷팅 (예: 30분 전 / 5시간 전)
                if elapsed_hours < 1.0:
                    elapsed_str = f"{int(elapsed_hours * 60)}분 전"
                else:
                    elapsed_str = f"{int(elapsed_hours)}시간 전"

                video_list.append({
                    "채널명": channel_name,
                    "제목": snippet["title"],
                    "조회수": views,
                    "시간당조회수": views_per_hour,
                    "경과시간": elapsed_str,
                    "게시일시_dt": pub_time_kst,
                    "게시일시": pub_time_kst.strftime("%m-%d %H:%M"),
                    "링크": f"https://youtu.be/{item['id']}"
                })
    except Exception as e:
        print(f"영상 수집 오류 ({channel_name}): {e}")
        
    return video_list

def send_telegram_message(message):
    """텔레그램 메시지 발송"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    response = requests.post(url, data=payload)
    return response.json()

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
        send_telegram_message(f"⚠️ <b>[여권 성향 유튜브 리포트]</b>\n({now_str} KST)\n\n최근 24시간 이내에 업로드된 일반 영상(쇼츠 제외)이 없습니다.")
        return

    df = pd.DataFrame(all_data)

    # 채널 중복 방지: 채널당 가장 최신/대표 영상 1개만 추출
    df = df.sort_values(by=["채널명", "게시일시_dt"], ascending=[True, False])
    df = df.drop_duplicates(subset=["채널명"], keep="first")

    # 전체 수집 건 중 조회수 높은 순 정렬
    df = df.sort_values(by="조회수", ascending=False)

    # ---------------------------------------------------------
    # [헤더 & 3줄 요약 작성]
    # ---------------------------------------------------------
    total_channels = len(df)
    top_video = df.iloc[0]
    hot_100k_count = len(df[df['조회수'] >= 100000])

    msg = f"📊 <b>여권 성향 유튜브 모니터링</b>\n"
    msg += f"⏱ 기준: KST {now_str} (쇼츠 제외)\n\n"
    
    msg += f"💡 <b>[오늘의 3줄 요약]</b>\n"
    msg += f"• 수집 채널: 총 <b>{total_channels}개</b> 채널 신규 영상\n"
    msg += f"• 🔥 10만+ 대박 영상: <b>{hot_100k_count}개</b>\n"
    msg += f"• 👑 현재 1위: <b>[{top_video['채널명']}]</b> ({top_video['조회수']:,}회)\n\n"
    msg += f"───────────────────\n\n"

    # ---------------------------------------------------------
    # [개별 영상 목록 작성]
    # ---------------------------------------------------------
    rank = 1
    for idx, row in df.iterrows():
        views = row['조회수']
        views_formatted = f"{views:,}"
        vph_formatted = f"{row['시간당조회수']:,}"

        # 배지 설정
        if views >= 500000:
            badge = "💥 <b>[TOP]</b> "
        elif views >= 100000:
            badge = "🔥 "
        else:
            badge = "🔹 "

        msg += f"{rank}. {badge}<b>[{row['채널명']}]</b> ({row['게시일시']} | {row['경과시간']})\n"
        msg += f"   👁 조회수: <b>{views_formatted}회</b> (시당 +{vph_formatted}회)\n"
        msg += f"   🎬 {row['제목']}\n"
        msg += f"   👉 <a href='{row['링크']}'>[영상 보기]</a>\n\n"
        rank += 1

    send_telegram_message(msg)
    print("✅ 최종 업그레이드된 텔레그램 리포트 발송 완료!")

if __name__ == "__main__":
    run_monitoring()