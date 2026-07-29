import os
import requests
from googleapiclient.discovery import build
from datetime import datetime
import pandas as pd

# ---------------------------------------------------------
# [환경 변수 로드]
# ---------------------------------------------------------
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# ---------------------------------------------------------
# [모니터링 대상 채널] (채널명: 채널 ID)
# ---------------------------------------------------------
TARGET_CHANNELS = {
    "김어준의 겸손은힘들다 뉴스공장": "UC-p2I93sX1_n7L_40Y4dY1A",
    "새날": "UCsP3AkyS7LqFp7A9dD_-EBA",
    "이동형TV": "UCcR__M8p9H22tX_mR9D1O3A",
    "박시영TV": "UCcK3R1lH7Jk0zN3Jp2X1NfA",
    "매불쇼(팟빵)": "UCz3M0X2C9P4pG_H0V28j7fQ",
    "뉴탐사": "UCx_E2L3W4j2l2m2m3E4J4FA",
    "서울의소리": "UCxN2n3L1W3V4_94I2V1J3A",
    "델리민주 (공식)": "UC88f1G9b2g0N1K6e9v4W_2A",
}

def get_channel_uploads_playlist_id(youtube, channel_id):
    try:
        response = youtube.channels().list(part="contentDetails", id=channel_id).execute()
        items = response.get("items", [])
        if items:
            return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    except Exception as e:
        print(f"채널 ID({channel_id}) 조회 실패: {e}")
    return None

def fetch_recent_videos(youtube, playlist_id, channel_name, max_results=2):
    video_list = []
    try:
        playlist_response = youtube.playlistItems().list(
            part="snippet", playlistId=playlist_id, maxResults=max_results
        ).execute()
        
        video_ids = [item["snippet"]["resourceId"]["videoId"] for item in playlist_response.get("items", [])]
        if not video_ids:
            return video_list

        videos_response = youtube.videos().list(
            part="snippet,statistics", id=",".join(video_ids)
        ).execute()

        for item in videos_response.get("items", []):
            snippet = item["snippet"]
            stats = item.get("statistics", {})
            
            pub_time = snippet["publishedAt"]
            pub_date = datetime.strptime(pub_time, "%Y-%m-%dT%H:%M:%SZ").strftime("%m-%d %H:%M")

            video_list.append({
                "채널명": channel_name,
                "제목": snippet["title"],
                "조회수": int(stats.get("viewCount", 0)),
                "게시일시": pub_date,
                "링크": f"https://youtu.be/{item['id']}"
            })
    except Exception as e:
        print(f"영상 수집 오류 ({channel_name}): {e}")
        
    return video_list

def send_telegram_message(message):
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
            recent_videos = fetch_recent_videos(youtube, uploads_id, channel_name, max_results=2)
            all_data.extend(recent_videos)

    if not all_data:
        send_telegram_message("⚠️ 모니터링 실행 결과: 수집된 데이터가 없습니다.")
        return

    df = pd.DataFrame(all_data)
    df = df.sort_values(by="조회수", ascending=False)

    # 텔레그램 메시지 작성 (상위 8개 영상 리포트)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    msg = f"<b>📊 여권 성향 유튜브 모니터링 ({now_str})</b>\n\n"
    
    top_df = df.head(8)
    for idx, row in top_df.iterrows():
        views_formatted = f"{row['조회수']:,}"
        msg += f"🔹 <b>[{row['채널명']}]</b> ({row['게시일시']})\n"
        msg += f"👁 조회수: <b>{views_formatted}회</b>\n"
        msg += f"🎬 <a href='{row['링크']}'>{row['제목']}</a>\n\n"

    send_telegram_message(msg)
    print("✅ 텔레그램 리포트 발송 완료!")

if __name__ == "__main__":
    run_monitoring()