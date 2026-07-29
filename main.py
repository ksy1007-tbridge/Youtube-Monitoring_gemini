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
    "김어준의 겸손은힘들다 뉴스공장": "UCAAvO0ehWox1bbym3rXKBZw",
    "[팟빵] 최욱의 매불쇼": "UCMYhq9OyGI5UEz_NTAoHY7A",
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