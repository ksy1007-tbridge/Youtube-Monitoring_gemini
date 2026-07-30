import os
import requests
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
    """채널의 업로드 전용 재생목록 ID 조회"""
    try:
        response = youtube.channels().list(part="contentDetails", id=channel_id).execute()
        items = response.get("items", [])
        if items:
            return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    except Exception as e:
        print(f"채널 ID({channel_id}) 조회 실패: {e}")
    return None

def fetch_recent_videos(youtube, playlist_id, channel_name, max_results=5):
    """최근 24시간 이내, 라이브 대기방 제외 영상 수집"""
    video_list = []
    now_kst = datetime.now(KST)
    twenty_four_hours_ago = now_kst - timedelta(hours=24)

    try:
        # 재생목록에서 최신 목록 가져오기
        playlist_response = youtube.playlistItems().list(
            part="snippet", playlistId=playlist_id, maxResults=max_results
        ).execute()
        
        video_ids = [item["snippet"]["resourceId"]["videoId"] for item in playlist_response.get("items", [])]
        if not video_ids:
            return video_list

        # 영상 상세정보 및 라이브 상태 조회
        videos_response = youtube.videos().list(
            part="snippet,statistics,liveStreamingDetails", id=",".join(video_ids)
        ).execute()

        for item in videos_response.get("items", []):
            snippet = item["snippet"]
            stats = item.get("statistics", {})
            
            # 1. 라이브 대기방(upcoming) 필터링 (완료되었거나 일반 영상만 수집)
            live_status = snippet.get("liveBroadcastContent", "none")
            if live_status == "upcoming":
                continue

            # 2. 업로드 시각 KST 변환 및 24시간 이내 필터링
            pub_time_utc = datetime.strptime(snippet["publishedAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            pub_time_kst = pub_time_utc.astimezone(KST)

            if pub_time_kst >= twenty_four_hours_ago:
                video_list.append({
                    "채널명": channel_name,
                    "제목": snippet["title"],
                    "조회수": int(stats.get("viewCount", 0)),
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
            videos = fetch_recent_videos(youtube, uploads_id, channel_name, max_results=5)
            all_data.extend(videos)

    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    if not all_data:
        send_telegram_message(f"⚠️ <b>[여권 성향 유튜브 리포트]</b>\n({now_str} KST)\n\n최근 24시간 이내에 업로드된 신규 영상이 없습니다.")
        return

    df = pd.DataFrame(all_data)

    # 3. 채널 중복 방지: 채널당 가장 최신/대표 영상 1개만 추출
    df = df.sort_values(by=["채널명", "게시일시_dt"], ascending=[True, False])
    df = df.drop_duplicates(subset=["채널명"], keep="first")

    # 4. 전체 수집 건 중 조회수 높은 순으로 최종 정렬
    df = df.sort_values(by="조회수", ascending=False)

    # 5. 시각적 하이라이트 적용 메시지 작성
    msg = f"📊 <b>여권 성향 유튜브 모니터링 (KST {now_str})</b>\n"
    msg += f"<i>(최근 24시간 이내 채널별 대표 영상)</i>\n\n"
    
    rank = 1
    for idx, row in df.iterrows():
        views = row['조회수']
        views_formatted = f"{views:,}"
        
        # 조회수 10만 회 이상은 🔥 불꽃 이모지, 50만 이상은 💥 이모지 붙이기
        if views >= 500000:
            badge = "💥 <b>[TOP]</b> "
        elif views >= 100000:
            badge = "🔥 "
        else:
            badge = "🔹 "

        msg += f"{rank}. {badge}<b>[{row['채널명']}]</b> ({row['게시일시']})\n"
        msg += f"   👁 조회수: <b>{views_formatted}회</b>\n"
        msg += f"   🎬 <a href='{row['링크']}'>{row['제목']}</a>\n\n"
        rank += 1

    send_telegram_message(msg)
    print("✅ 개선된 텔레그램 리포트 발송 완료!")

if __name__ == "__main__":
    run_monitoring()