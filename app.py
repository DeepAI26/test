
import os
from dotenv import load_dotenv
# from pydub import AudioSegment
import yt_dlp
import whisper
from transformers import pipeline, BartForConditionalGeneration, BartTokenizer
import torch
from flask import Flask, render_template, request, jsonify
import tempfile
import traceback
import requests
import json
import hashlib
import datetime
import sqlite3
import threading
import time
from typing import Optional
import logging
import urllib.parse  # needed for encoding share URLs

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

# -------------------------------
# Configuration from Environment Variables
# -------------------------------
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
DISCORD_CHANNEL_ID = os.getenv('DISCORD_CHANNEL_ID')

discord_configured = bool(DISCORD_BOT_TOKEN) and bool(DISCORD_CHANNEL_ID)


# Check if required environment variables are set
# def check_environment():
#     missing_vars = []
#     if not TELEGRAM_BOT_TOKEN:
#         missing_vars.append('TELEGRAM_BOT_TOKEN')
#     if not TELEGRAM_CHAT_ID:
#         missing_vars.append('TELEGRAM_CHAT_ID')
#     if not discord_configured:
#         missing_vars.append('DISCORD_BOT_CONFIG')

#     if missing_vars:
#         print("⚠️  Warning: The following environment variables are not set:")
#         for var in missing_vars:
#             print(f"   - {var}")
#         if 'DISCORD_BOT_CONFIG' in missing_vars:
#             print("   Discord posting will use copy-to-clipboard")
#         print("⚠️  Social media posting will not work without these variables.")
#     else:
#         print("✅ All environment variables are set!")
def check_environment():
    missing_vars = []
    if not TELEGRAM_BOT_TOKEN:
        missing_vars.append('TELEGRAM_BOT_TOKEN')
    else:
        print(f"✅ TELEGRAM_BOT_TOKEN: {TELEGRAM_BOT_TOKEN[:10]}...{TELEGRAM_BOT_TOKEN[-10:]}")

    if not TELEGRAM_CHAT_ID:
        missing_vars.append('TELEGRAM_CHAT_ID')
    else:
        print(f"✅ TELEGRAM_CHAT_ID: {TELEGRAM_CHAT_ID}")

    if not discord_configured:
        missing_vars.append('DISCORD_BOT_CONFIG')

    if missing_vars:
        print("⚠️  Warning: The following environment variables are not set:")
        for var in missing_vars:
            print(f"   - {var}")
        if 'DISCORD_BOT_CONFIG' in missing_vars:
            print("   Discord posting will use copy-to-clipboard")
        print("⚠️  Social media posting will not work without these variables.")
    else:
        print("✅ All environment variables are set!")

check_environment()

# -------------------------------
# Device setup
# -------------------------------
device = "cuda" if torch.cuda.is_available() else "cpu"
print("Using device:", device)

# -------------------------------
# Load models
# -------------------------------
model = whisper.load_model("base")

# Load a better summarization model
try:
    # Try to use BART model for better summarization
    summarization_model_name = "facebook/bart-large-cnn"
    summarizer = pipeline(
        "summarization",
        model=summarization_model_name,
        tokenizer=summarization_model_name,
        device=0 if device == "cuda" else -1
    )
    print("✅ Using BART model for summarization")
except Exception as e:
    print(f"⚠️  Could not load BART model, using default: {e}")
    summarizer = pipeline("summarization", device=0 if device == "cuda" else -1)

print("✅ Models loaded: Whisper (transcription) and Transformers (summarization)")


# -------------------------------
# Helper functions
# -------------------------------
def get_video_id(url):
    if "watch?v=" in url:
        return url.split("watch?v=")[1].split("&")[0]
    return url


def download_audio(url, output_name="audio/audio"):
    os.makedirs("audio", exist_ok=True)
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_name,
        'quiet': True,
        'no_warnings': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return output_name + ".mp3"


def transcribe_audio(file_path):
    """
    Transcribe audio without splitting into chunks
    """
    try:
        print(f"Transcribing audio file: {file_path}")
        result = model.transcribe(file_path)
        transcript = result['text'].strip()
        print(f"Transcription completed. Length: {len(transcript)} characters")
        return transcript
    except Exception as e:
        print(f"Error transcribing audio: {e}")
        raise


def chunk_text_for_summarization(text, max_chunk_size=1024):
    """
    Split text into chunks suitable for summarization while preserving sentence boundaries
    """
    sentences = text.split('. ')
    chunks = []
    current_chunk = ""

    for sentence in sentences:
        # If adding this sentence would exceed max chunk size, save current chunk and start new one
        if len(current_chunk) + len(sentence) + 2 > max_chunk_size and current_chunk:
            chunks.append(current_chunk.strip())
            current_chunk = sentence + '. '
        else:
            current_chunk += sentence + '. '

    # Add the last chunk if it's not empty
    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    print(f"Split text into {len(chunks)} chunks for summarization")
    return chunks


def summarize_text(text, max_length=150):
    """
    Improved summarization with better chunking and handling of long texts
    """
    if not text or len(text.strip()) < 100:
        return "Text too short for meaningful summary."

    try:
        # Clean and preprocess text
        text = text.replace('\n', ' ').strip()

        # If text is short, summarize directly
        if len(text) < 800:
            summary = summarizer(
                text,
                max_length=max_length,
                min_length=max(30, max_length // 3),
                do_sample=False,
                truncation=True
            )
            return summary[0]['summary_text']

        # For longer texts, use chunking strategy
        chunks = chunk_text_for_summarization(text)

        if len(chunks) == 1:
            # Single chunk - summarize directly
            summary = summarizer(
                chunks[0],
                max_length=max_length,
                min_length=max(30, max_length // 3),
                do_sample=False,
                truncation=True
            )
            return summary[0]['summary_text']
        else:
            # Multiple chunks - summarize each and combine
            chunk_summaries = []
            for i, chunk in enumerate(chunks):
                try:
                    chunk_max_len = max(min(max_length // len(chunks), 100), 50)
                    chunk_summary = summarizer(
                        chunk,
                        max_length=chunk_max_len,
                        min_length=max(20, chunk_max_len // 3),
                        do_sample=False,
                        truncation=True
                    )
                    chunk_summaries.append(chunk_summary[0]['summary_text'])
                    print(f"Summarized chunk {i + 1}/{len(chunks)}")
                except Exception as e:
                    print(f"Error summarizing chunk {i + 1}: {e}")
                    # Fallback: take first few sentences
                    sentences = chunk.split('. ')
                    chunk_summaries.append('. '.join(sentences[:3]) + '.')

            # Combine chunk summaries and create final summary
            combined_text = ' '.join(chunk_summaries)

            # If combined text is still long, do a final summarization
            if len(combined_text) > 500:
                final_summary = summarizer(
                    combined_text,
                    max_length=max_length,
                    min_length=max(30, max_length // 3),
                    do_sample=False,
                    truncation=True
                )
                return final_summary[0]['summary_text']
            else:
                return combined_text

    except Exception as e:
        print(f"Summarization error: {e}")
        # Fallback: return the most important parts
        sentences = text.split('. ')
        if len(sentences) > 10:
            # Take first 3 and last 2 sentences for context
            important_sentences = sentences[:3] + sentences[-2:]
            return '. '.join(important_sentences) + '.'
        else:
            return text


def get_video_details(url):
    try:
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                'title': info.get('title', 'No title'),
                'description': info.get('description', 'No description'),
                'duration': info.get('duration', 0),
                'uploader': info.get('uploader', 'Unknown'),
                'view_count': info.get('view_count', 0),
                'thumbnail': info.get('thumbnail', ''),
                'video_id': info.get('id', '')
            }
    except Exception as e:
        print(f"Error getting video details: {e}")
        return None


# def post_to_telegram(message, photo_url=None):
#     if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
#         return {"success": False, "error": "Telegram credentials not configured"}
#     try:
#         if photo_url:
#             url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
#             data = {"chat_id": TELEGRAM_CHAT_ID, "photo": photo_url, "caption": message, "parse_mode": "HTML"}
#         else:
#             url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
#             data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
#         response = requests.post(url, data=data)
#         if response.status_code == 200:
#             return {"success": True, "message": "✅ Posted to Telegram successfully!"}
#         else:
#             return {"success": False, "error": f"Telegram API error: {response.text}"}
#     except Exception as e:
#         return {"success": False, "error": f"Telegram posting failed: {str(e)}"}

def post_to_telegram(message, photo_url=None):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        error_msg = "Telegram credentials not configured"
        print(f"❌ Telegram Error: {error_msg}")
        return {"success": False, "error": error_msg}

    try:
        print(f"📱 Attempting to post to Telegram...")
        print(f"   Chat ID: {TELEGRAM_CHAT_ID}")
        print(f"   Message length: {len(message)}")
        print(f"   Photo URL: {photo_url}")

        # Telegram limits
        CAPTION_LIMIT = 1024  # Maximum for photo captions
        MESSAGE_LIMIT = 4096  # Maximum for regular messages

        if photo_url:
            print(f"   Using sendPhoto API")
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"

            # Extract video title from message for the caption
            video_title = "YouTube Video"
            if "🎥 <b>" in message and "</b>" in message:
                start = message.find("🎥 <b>") + len("🎥 <b>")
                end = message.find("</b>", start)
                video_title = message[start:end]

            # Extract the summary part from the message
            summary_text = message
            if "\n\n" in message:
                summary_text = message.split("\n\n", 1)[1]  # Get everything after title
                if "#YouTube #Summary" in summary_text:
                    summary_text = summary_text.split("#YouTube #Summary")[0].strip()

            # Create a safe caption that definitely fits
            safe_caption = create_telegram_safe_message(video_title, summary_text)

            print(f"   Safe caption length: {len(safe_caption)}")
            print(f"   Safe caption preview: {safe_caption[:100]}...")

            # Send photo with the safe caption
            photo_data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "photo": photo_url,
                "caption": safe_caption,
                "parse_mode": "HTML"
            }

            response = requests.post(url, data=photo_data, timeout=30)
            print(f"   Photo Response Status: {response.status_code}")
            print(f"   Photo Response Text: {response.text}")

            if response.status_code == 200:
                print("✅ Photo posted to Telegram successfully!")

                # Check if we should also send the full message
                if len(message) > len(safe_caption) + 200:  # If there's significant additional content
                    print("   Sending additional content as separate message...")
                    message_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

                    # Create continuation message
                    continuation_msg = "📝 Full Summary:\n\n" + summary_text
                    if len(continuation_msg) > MESSAGE_LIMIT:
                        continuation_msg = continuation_msg[:MESSAGE_LIMIT - 3] + "..."

                    message_data = {
                        "chat_id": TELEGRAM_CHAT_ID,
                        "text": continuation_msg,
                        "parse_mode": "HTML"
                    }

                    message_response = requests.post(message_url, data=message_data, timeout=30)
                    if message_response.status_code == 200:
                        print("✅ Additional content sent successfully!")
                        return {"success": True,
                                "message": "✅ Posted to Telegram successfully! (photo + additional content)"}
                    else:
                        print("⚠️  Photo sent but additional content failed")
                        return {"success": True, "message": "✅ Photo posted, but additional content failed"}

                return {"success": True, "message": "✅ Posted to Telegram successfully!"}
            else:
                error_msg = f"Telegram API error: {response.text}"
                print(f"❌ {error_msg}")
                return {"success": False, "error": error_msg}
        else:
            # No photo, just send the message
            print(f"   Using sendMessage API")
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

            # Ensure message fits within Telegram's message limit
            if len(message) > MESSAGE_LIMIT:
                print(f"   Message too long ({len(message)} chars), truncating...")
                message = message[:MESSAGE_LIMIT - 3] + "..."

            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML"
            }

            response = requests.post(url, data=data, timeout=30)
            print(f"   Response Status: {response.status_code}")
            print(f"   Response Text: {response.text}")

            if response.status_code == 200:
                print("✅ Posted to Telegram successfully!")
                return {"success": True, "message": "✅ Posted to Telegram successfully!"}
            else:
                error_msg = f"Telegram API error: {response.text}"
                print(f"❌ {error_msg}")
                return {"success": False, "error": error_msg}

    except Exception as e:
        error_msg = f"Telegram posting failed: {str(e)}"
        print(f"❌ {error_msg}")
        return {"success": False, "error": error_msg}


def create_telegram_safe_message(video_title, summary, max_caption_length=900):
    """
    Create a Telegram-safe message that fits in photo captions
    Uses a shorter summary specifically for the photo caption
    """
    # Create a much shorter summary for the caption
    sentences = summary.split('. ')
    short_summary = ""

    # Take only the first 2-3 sentences or until we hit the limit
    for sentence in sentences:
        potential_summary = short_summary + sentence + '. '
        if len(potential_summary) > max_caption_length - len(video_title) - 50:  # Leave room for title and hashtags
            break
        short_summary = potential_summary
        if len(short_summary.split('. ')) >= 2:  # Stop after 2 sentences
            break

    # If still too long, truncate
    short_summary = short_summary.strip()
    if len(short_summary) > max_caption_length - len(video_title) - 50:
        short_summary = short_summary[:max_caption_length - len(video_title) - 60] + '...'

    # Build the final caption
    caption = f"🎥 <b>{video_title}</b>\n\n{short_summary}\n\n#YouTube #Summary"

    # Final safety check
    if len(caption) > max_caption_length:
        # Emergency truncation
        caption = caption[:max_caption_length - 3] + "..."

    return caption


def post_to_discord(summary, video_title, video_details):
    if not discord_configured:
        return {"success": False, "error": "Discord bot not configured"}
    try:
        headers = {'Authorization': f'Bot {DISCORD_BOT_TOKEN}', 'Content-Type': 'application/json'}
        embed = {
            "title": f"🎥 {video_title}",
            "description": summary,
            "color": 5814783,
            "fields": [
                {"name": "Channel", "value": video_details.get('uploader', 'Unknown'), "inline": True},
                {"name": "Duration",
                 "value": f"{video_details.get('duration', 0) // 60}:{video_details.get('duration', 0) % 60:02d}",
                 "inline": True},
                {"name": "Views", "value": f"{video_details.get('view_count', 0):,}", "inline": True}
            ],
            "footer": {"text": "Generated by YouTube Summarizer"},
            "timestamp": datetime.datetime.utcnow().isoformat()
        }
        thumbnail = video_details.get('thumbnail')
        if thumbnail:
            embed["thumbnail"] = {"url": thumbnail}
        data = {"embeds": [embed], "content": "📺 **New YouTube Video Summary**"}
        url = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages"
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            message_data = response.json()
            return {"success": True, "message": "Posted to Discord successfully"}
        else:
            return {"success": False, "error": f"Discord API error: {response.text}"}
    except Exception as e:
        return {"success": False, "error": f"Discord posting failed: {str(e)}"}


def create_discord_message(summary, video_title, video_details):
    duration = video_details.get('duration', 0)
    duration_str = f"{duration // 60}:{duration % 60:02d}"
    return f"""**🎥 {video_title}**

{summary}

**Channel:** {video_details.get('uploader', 'Unknown')}
**Duration:** {duration_str}
**Views:** {video_details.get('view_count', 0):,}

*Generated by YouTube Summarizer*"""


# -------------------------------
# Twitter Functions
# -------------------------------
def create_twitter_summary(summary, video_title, video_details, video_id):
    """
    Create a Twitter-friendly summary with character limits
    """
    # Twitter character limit is 280, but we need space for URL and hashtags
    max_summary_length = 200  # Leave room for other elements

    # Truncate summary if needed
    if len(summary) > max_summary_length:
        twitter_summary = summary[:max_summary_length - 3] + "..."
    else:
        twitter_summary = summary

    # Create the base message
    message = f"🎥 {video_title}\n\n{twitter_summary}"

    # Add hashtags if we have room
    remaining_chars = 280 - len(message)
    if remaining_chars > 20:
        message += "\n\n#YouTube #Summary"

    return message


def create_twitter_share_url(message, url="", hashtags=""):
    """
    Generate Twitter share URL with pre-filled content

    Args:
        message (str): The tweet text
        url (str): Optional URL to include
        hashtags (str): Optional comma-separated hashtags
    """
    base_url = "https://twitter.com/intent/tweet"

    params = {'text': message}
    if url:
        params['url'] = url
    if hashtags:
        # Clean hashtags (remove # symbols and spaces)
        clean_hashtags = hashtags.replace('#', '').replace(' ', '')
        params['hashtags'] = clean_hashtags

    query_string = urllib.parse.urlencode(params)
    return f"{base_url}?{query_string}"


def generate_twitter_post(summary, video_title, video_details, video_id):
    """
    Main function to generate Twitter post data
    """
    try:
        # Create Twitter-friendly message
        twitter_message = create_twitter_summary(summary, video_title, video_details, video_id)

        # Generate share URL
        twitter_url = create_twitter_share_url(twitter_message)

        return {
            "success": True,
            "twitter_url": twitter_url,
            "twitter_message": twitter_message,
            "message": "Twitter share URL generated successfully!"
        }
    except Exception as e:
        return {"success": False, "error": f"Twitter post generation failed: {str(e)}"}


# -------------------------------
# Database and Scheduling Functions
# -------------------------------
VIDEO_DATA_FILE = "video_data.json"


def load_video_data():
    """Load video data from JSON file"""
    try:
        if os.path.exists(VIDEO_DATA_FILE):
            with open(VIDEO_DATA_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Error loading video data: {e}")
    return {}


def save_video_data(video_data):
    """Save video data to JSON file"""
    try:
        with open(VIDEO_DATA_FILE, 'w') as f:
            json.dump(video_data, f)
    except Exception as e:
        logger.error(f"Error saving video data: {e}")


# Load video data at startup
video_data = load_video_data()
logger.info(f"Loaded video data for {len(video_data)} videos")

DB_FILE = "schedules.db"


def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
              CREATE TABLE IF NOT EXISTS scheduled_posts
              (
                  id
                  INTEGER
                  PRIMARY
                  KEY
                  AUTOINCREMENT,
                  video_id
                  TEXT
                  NOT
                  NULL,
                  platform
                  TEXT
                  NOT
                  NULL,
                  schedule_time_utc
                  TEXT
                  NOT
                  NULL,
                  status
                  TEXT
                  NOT
                  NULL,
                  attempt_count
                  INTEGER
                  DEFAULT
                  0,
                  last_result
                  TEXT
                  DEFAULT
                  NULL,
                  created_at
                  TEXT
                  NOT
                  NULL
              );
              """)
    conn.commit()
    conn.close()
    logger.info("Database initialized")


init_db()


def insert_scheduled_post(video_id: str, platform: str, schedule_dt_utc: datetime.datetime):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
              INSERT INTO scheduled_posts (video_id, platform, schedule_time_utc, status, created_at)
              VALUES (?, ?, ?, 'scheduled', ?)
              """, (video_id, platform, schedule_dt_utc.isoformat(), datetime.datetime.utcnow().isoformat()))
    conn.commit()
    row_id = c.lastrowid
    conn.close()
    logger.info(f"Scheduled post {row_id} for video {video_id} on {platform} at {schedule_dt_utc}")
    return row_id


def update_scheduled_post_status(row_id: int, status: str, last_result: Optional[str] = None,
                                 attempt_count: Optional[int] = None):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if attempt_count is None:
        c.execute("UPDATE scheduled_posts SET status = ?, last_result = ? WHERE id = ?", (status, last_result, row_id))
    else:
        c.execute("UPDATE scheduled_posts SET status = ?, last_result = ?, attempt_count = ? WHERE id = ?",
                  (status, last_result, attempt_count, row_id))
    conn.commit()
    conn.close()
    logger.info(f"Updated post {row_id} to status: {status}")


def get_due_scheduled_posts(limit=10):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    now = datetime.datetime.utcnow().isoformat()
    c.execute("""
              SELECT id, video_id, platform
              FROM scheduled_posts
              WHERE status = 'scheduled'
                AND schedule_time_utc <= ?
              ORDER BY schedule_time_utc ASC LIMIT ?
              """, (now, limit))
    rows = c.fetchall()
    conn.close()
    if rows:
        logger.info(f"Found {len(rows)} due scheduled posts")
    return rows


def local_datetime_string_to_utc(dt_local_str: str) -> datetime.datetime:
    try:
        logger.info(f"Parsing datetime string: {dt_local_str}")

        # HTML datetime-local input format: "YYYY-MM-DDTHH:MM"
        # Example: "2024-01-15T14:30"

        # Parse the string directly - it should be in local time
        naive_local = datetime.datetime.strptime(dt_local_str, "%Y-%m-%dT%H:%M")
        logger.info(f"Parsed naive local datetime: {naive_local}")

        # Get local timezone
        local_tz = datetime.datetime.now().astimezone().tzinfo
        logger.info(f"Local timezone: {local_tz}")

        # Make it timezone-aware
        aware_local = naive_local.replace(tzinfo=local_tz)
        logger.info(f"Timezone-aware local: {aware_local}")

        # Convert to UTC
        utc_dt = aware_local.astimezone(datetime.timezone.utc)
        logger.info(f"Converted to UTC: {utc_dt}")

        # Return naive UTC datetime (without timezone info)
        return utc_dt.replace(tzinfo=None)

    except ValueError as e:
        logger.error(f"Error parsing datetime '{dt_local_str}': {e}")
        # Fallback: try to parse with different formats
        try:
            # Try with space separator instead of T
            if ' ' in dt_local_str:
                naive_local = datetime.datetime.strptime(dt_local_str, "%Y-%m-%d %H:%M")
            else:
                # Try the original format again with more debugging
                parts = dt_local_str.split('T')
                if len(parts) != 2:
                    raise ValueError(f"Invalid format, expected 'YYYY-MM-DDTHH:MM', got: {dt_local_str}")
                naive_local = datetime.datetime.strptime(dt_local_str, "%Y-%m-%dT%H:%M")

            local_tz = datetime.datetime.now().astimezone().tzinfo
            aware_local = naive_local.replace(tzinfo=local_tz)
            utc_dt = aware_local.astimezone(datetime.timezone.utc)
            return utc_dt.replace(tzinfo=None)

        except Exception as fallback_error:
            logger.error(f"Fallback parsing also failed: {fallback_error}")
            raise ValueError(f"Invalid datetime format: {dt_local_str}. Expected format: YYYY-MM-DDTHH:MM")


def scheduled_poster_worker(poll_interval_seconds=30):
    logger.info(f"Scheduled poster worker started (poll interval: {poll_interval_seconds}s)")
    while True:
        try:
            due_posts = get_due_scheduled_posts(limit=20)
            logger.info(f"Checking due posts: {len(due_posts)} found")

            for row in due_posts:
                row_id, video_id, platform = row
                logger.info(f"Processing scheduled post {row_id} for video {video_id} on {platform}")

                try:
                    update_scheduled_post_status(row_id, 'posting', last_result='Posting started')

                    # Load video data from file
                    current_video_data = load_video_data()
                    video_info = current_video_data.get(video_id)

                    if not video_info:
                        error_msg = f'Missing video data for {video_id}'
                        logger.error(error_msg)
                        update_scheduled_post_status(row_id, 'failed', last_result=error_msg)
                        continue

                    summary = video_info['summaries'].get(platform)
                    video_title = video_info['details']['title']
                    video_details = video_info['details']
                    thumbnail = video_details.get('thumbnail')

                    logger.info(f"Posting to {platform}: {video_title}")

                    if platform == "telegram":
                        message = f"🎥 <b>{video_title}</b>\n\n{summary}\n\n#YouTube #Summary"
                        result = post_to_telegram(message, photo_url=thumbnail)
                    elif platform == "discord":
                        if discord_configured:
                            result = post_to_discord(summary, video_title, video_details)
                        else:
                            message = create_discord_message(summary, video_title, video_details)
                            result = {"success": True, "message": "Discord message ready - copy/paste",
                                      "discord_message": message}
                    elif platform == "twitter":
                        result = generate_twitter_post(summary, video_title, video_details, video_id)
                    else:
                        result = {"success": False, "error": "Unsupported platform"}

                    if result.get('success'):
                        update_scheduled_post_status(row_id, 'posted', last_result=json.dumps(result), attempt_count=1)
                        logger.info(f"Successfully posted scheduled post {row_id}")
                    else:
                        update_scheduled_post_status(row_id, 'failed', last_result=json.dumps(result), attempt_count=1)
                        logger.error(f"Failed to post scheduled post {row_id}: {result.get('error')}")

                except Exception as e:
                    error_msg = f"Error processing scheduled post: {str(e)}"
                    logger.error(error_msg)
                    update_scheduled_post_status(row_id, 'failed', last_result=error_msg)

        except Exception as e:
            logger.error(f"Scheduled poster worker exception: {e}")

        time.sleep(poll_interval_seconds)


# Start the scheduler thread
scheduler_thread = threading.Thread(target=scheduled_poster_worker, daemon=True)
scheduler_thread.start()
logger.info("✅ Scheduler thread started and running")

# -------------------------------
# Flask app
# -------------------------------
app = Flask(__name__)

# Move the add_flask_route import and call to AFTER all function definitions
from ai_agent import add_flask_route

add_flask_route(app, video_data, save_video_data, download_audio, transcribe_audio, summarize_text, get_video_details,
                post_to_telegram, post_to_discord)


@app.route("/", methods=["GET"])
def index():
    return render_template('index.html')


@app.route("/get_transcript", methods=["POST"])
def get_transcript():
    url = request.json.get("youtube_url")
    if not url:
        return jsonify({"error": "Please enter a valid URL."}), 400
    try:
        video_details = get_video_details(url)
        if not video_details:
            return jsonify({"error": "Could not fetch video details."}), 400

        # Download audio
        audio_file = download_audio(url)

        # Transcribe without chunking
        transcript = transcribe_audio(audio_file)

        # Generate summarized transcript (longer summary)
        summarized_transcript = summarize_text(transcript, max_length=300)

        video_id = hashlib.md5(url.encode()).hexdigest()

        # Update video data and save to file
        video_data[video_id] = {
            'transcript': transcript,
            'summarized_transcript': summarized_transcript,
            'details': video_details
        }
        save_video_data(video_data)

        # Clean up audio file
        if os.path.exists(audio_file):
            os.remove(audio_file)

        return jsonify({
            "success": True,
            "video_details": video_details,
            "transcript": transcript,
            "summarized_transcript": summarized_transcript,
            "video_id": video_id
        })
    except Exception as e:
        logger.error(f"Error getting transcript: {e}")
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@app.route("/get_summary", methods=["POST"])
def get_summary():
    video_id = request.json.get("video_id")
    if not video_id or video_id not in video_data:
        return jsonify({"error": "No transcript found. Please get transcript first."}), 400

    transcript = video_data[video_id]['transcript']
    video_title = video_data[video_id]['details']['title']

    # Generate platform-specific summaries
    summaries = {
        "twitter": summarize_text(transcript, max_length=100),
        "telegram": summarize_text(transcript, max_length=800),
        "discord": summarize_text(transcript, max_length=1000),
        "full": video_data[video_id].get('summarized_transcript', '')  # Use the pre-generated full summary
    }

    video_data[video_id]['summaries'] = summaries
    save_video_data(video_data)  # Save updated data with summaries

    return jsonify({
        "success": True,
        "summaries": summaries,
        "discord_configured": discord_configured
    })


@app.route("/post_to_social", methods=["POST"])
def post_to_social():
    video_id = request.json.get("video_id")
    platform = request.json.get("platform")
    if not video_id or video_id not in video_data:
        return jsonify({"error": "No video data found."}), 400

    video_info = video_data[video_id]
    summary = video_info['summaries'].get(platform)
    video_title = video_info['details']['title']
    video_details = video_info['details']

    if platform == "telegram":
        telegram_message = f"🎥 <b>{video_title}</b>\n\n{summary}\n\n#YouTube #Summary"
        result = post_to_telegram(telegram_message, photo_url=video_details.get('thumbnail'))
        # message = f"🎥 <b>{video_title}</b>\n\n{summary}\n\n#YouTube #Summary"
        # result = post_to_telegram(message, photo_url=video_details.get('thumbnail'))
    elif platform == "discord":
        if discord_configured:
            result = post_to_discord(summary, video_title, video_details)
        else:
            message = create_discord_message(summary, video_title, video_details)
            result = {"success": True, "message": "Discord message ready - copy/paste", "discord_message": message}
    elif platform == "twitter":
        result = generate_twitter_post(summary, video_title, video_details, video_id)
    else:
        result = {"success": False, "error": "Unsupported platform"}

    return jsonify(result)


@app.route("/schedule_post", methods=["POST"])
def schedule_post():
    data = request.json
    video_id = data.get("video_id")
    platform = data.get("platform")
    schedule_time = data.get("schedule_time")
    post_now_flag = data.get("post_now", False)

    logger.info(f"Scheduling post - Video: {video_id}, Platform: {platform}, Time: {schedule_time}")

    if not video_id or video_id not in video_data:
        return jsonify({"error": "No video data found."}), 400
    if not platform:
        return jsonify({"error": "Platform missing."}), 400

    if post_now_flag or not schedule_time:
        # Immediate posting
        video_info = video_data[video_id]
        summary = video_info['summaries'].get(platform)
        video_title = video_info['details']['title']
        video_details = video_info['details']

        if platform == "telegram":
            message = f"🎥 <b>{video_title}</b>\n\n{summary}\n\n#YouTube #Summary"
            result = post_to_telegram(message, photo_url=video_details.get('thumbnail'))
        elif platform == "discord":
            if discord_configured:
                result = post_to_discord(summary, video_title, video_details)
            else:
                message = create_discord_message(summary, video_title, video_details)
                result = {"success": True, "message": "Discord message ready - copy/paste", "discord_message": message}
        elif platform == "twitter":
            result = generate_twitter_post(summary, video_title, video_details, video_id)
        else:
            result = {"success": False, "error": "Unsupported platform"}
        return jsonify(result)

    try:
        # Schedule for later
        utc_dt = local_datetime_string_to_utc(schedule_time)
        row_id = insert_scheduled_post(video_id, platform, utc_dt)

        # Format local time for display
        local_dt = datetime.datetime.fromisoformat(schedule_time)
        formatted_time = local_dt.strftime("%Y-%m-%d %H:%M:%S")

        return jsonify({
            "success": True,
            "message": f"Post scheduled successfully for {formatted_time}",
            "scheduled_id": row_id,
            "schedule_time_utc": utc_dt.isoformat(),
            "schedule_time_local": formatted_time
        })
    except Exception as e:
        logger.error(f"Scheduling error: {e}")
        return jsonify({"success": False, "error": f"Scheduling failed: {str(e)}"}), 500


@app.route("/custom_tweet", methods=["POST"])
def custom_tweet():
    """
    Generate Twitter share URL for custom text
    """
    try:
        data = request.json
        text = data.get("text", "")
        url = data.get("url", "")
        hashtags = data.get("hashtags", "")

        if not text:
            return jsonify({"success": False, "error": "Tweet text is required"}), 400

        twitter_url = create_twitter_share_url(text, url, hashtags)

        return jsonify({
            "success": True,
            "twitter_url": twitter_url,
            "tweet_text": text,
            "message": "Twitter share URL generated successfully!"
        })

    except Exception as e:
        logger.error(f"Custom tweet error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/debug_schedules", methods=["GET"])
def debug_schedules():
    """Debug endpoint to check scheduled posts"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
              SELECT id, video_id, platform, schedule_time_utc, status, created_at
              FROM scheduled_posts
              ORDER BY schedule_time_utc DESC LIMIT 10
              """)
    rows = c.fetchall()
    conn.close()

    schedules = []
    for row in rows:
        schedules.append({
            'id': row[0],
            'video_id': row[1],
            'platform': row[2],
            'schedule_time_utc': row[3],
            'status': row[4],
            'created_at': row[5]
        })

    return jsonify({
        'current_time_utc': datetime.datetime.utcnow().isoformat(),
        'schedules': schedules,
        'video_data_keys': list(video_data.keys())
    })


@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "scheduler_alive": scheduler_thread.is_alive()
    })


# -------------------------------
# Admin Routes
# -------------------------------
@app.route("/admin")
def admin():
    """Admin dashboard page"""
    return render_template("admin.html")


@app.route("/admin/api/scheduled_posts")
def admin_scheduled_posts():
    """API endpoint to get all scheduled posts"""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("""
                  SELECT id,
                         video_id,
                         platform,
                         schedule_time_utc,
                         status,
                         attempt_count,
                         last_result,
                         created_at
                  FROM scheduled_posts
                  ORDER BY schedule_time_utc DESC
                  """)
        rows = c.fetchall()
        conn.close()

        posts = []
        for row in rows:
            posts.append({
                'id': row[0],
                'video_id': row[1],
                'platform': row[2],
                'schedule_time_utc': row[3],
                'status': row[4],
                'attempt_count': row[5],
                'last_result': row[6],
                'created_at': row[7]
            })

        return jsonify({"success": True, "posts": posts})
    except Exception as e:
        logger.error(f"Error fetching scheduled posts: {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route("/admin/api/video_data")
def admin_video_data():
    """API endpoint to get all video data"""
    try:
        video_data = load_video_data()
        return jsonify({"success": True, "video_data": video_data})
    except Exception as e:
        logger.error(f"Error fetching video data: {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route("/admin/api/system_status")
def admin_system_status():
    """API endpoint to get system status"""
    try:
        # Database status
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        # Count scheduled posts by status
        c.execute("""
                  SELECT status, COUNT(*) as count
                  FROM scheduled_posts
                  GROUP BY status
                  """)
        status_counts = dict(c.fetchall())

        # Total videos processed
        video_count = len(video_data)

        # Scheduler status
        scheduler_alive = scheduler_thread.is_alive()

        conn.close()

        return jsonify({
            "success": True,
            "system_status": {
                "scheduler_alive": scheduler_alive,
                "video_count": video_count,
                "post_status_counts": status_counts,
                "current_time_utc": datetime.datetime.utcnow().isoformat(),
                "database_file": DB_FILE,
                "video_data_file": VIDEO_DATA_FILE
            }
        })
    except Exception as e:
        logger.error(f"Error fetching system status: {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route("/admin/api/delete_scheduled_post/<int:post_id>", methods=["DELETE"])
def delete_scheduled_post(post_id):
    """Delete a scheduled post"""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("DELETE FROM scheduled_posts WHERE id = ?", (post_id,))
        conn.commit()
        deleted = c.rowcount > 0
        conn.close()

        if deleted:
            logger.info(f"Deleted scheduled post {post_id}")
            return jsonify({"success": True, "message": f"Post {post_id} deleted successfully"})
        else:
            return jsonify({"success": False, "error": f"Post {post_id} not found"})
    except Exception as e:
        logger.error(f"Error deleting scheduled post {post_id}: {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route("/admin/api/update_post_status", methods=["POST"])
def update_post_status():
    """Update post status manually"""
    try:
        data = request.json
        post_id = data.get('post_id')
        status = data.get('status')

        if not post_id or not status:
            return jsonify({"success": False, "error": "Missing post_id or status"})

        update_scheduled_post_status(post_id, status, "Manually updated by admin")

        return jsonify({"success": True, "message": f"Post {post_id} status updated to {status}"})
    except Exception as e:
        logger.error(f"Error updating post status: {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route("/admin/api/delete_video/<video_id>", methods=["DELETE"])
def delete_video(video_id):
    """Delete video data"""
    try:
        if video_id in video_data:
            del video_data[video_id]
            save_video_data(video_data)
            logger.info(f"Deleted video data for {video_id}")
            return jsonify({"success": True, "message": f"Video {video_id} deleted successfully"})
        else:
            return jsonify({"success": False, "error": f"Video {video_id} not found"})
    except Exception as e:
        logger.error(f"Error deleting video {video_id}: {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route("/admin/api/run_post_now/<int:post_id>", methods=["POST"])
def run_post_now(post_id):
    """Run a scheduled post immediately"""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT video_id, platform FROM scheduled_posts WHERE id = ?", (post_id,))
        row = c.fetchone()
        conn.close()

        if not row:
            return jsonify({"success": False, "error": f"Post {post_id} not found"})

        video_id, platform = row

        # Load video data
        current_video_data = load_video_data()
        video_info = current_video_data.get(video_id)

        if not video_info:
            return jsonify({"success": False, "error": f"Video data for {video_id} not found"})

        summary = video_info['summaries'].get(platform)
        video_title = video_info['details']['title']
        video_details = video_info['details']

        # Post immediately
        if platform == "telegram":
            message = f"🎥 <b>{video_title}</b>\n\n{summary}\n\n#YouTube #Summary"
            result = post_to_telegram(message, photo_url=video_details.get('thumbnail'))
        elif platform == "discord":
            if discord_configured:
                result = post_to_discord(summary, video_title, video_details)
            else:
                message = create_discord_message(summary, video_title, video_details)
                result = {"success": True, "message": "Discord message ready - copy/paste", "discord_message": message}
        elif platform == "twitter":
            result = generate_twitter_post(summary, video_title, video_details, video_id)
        else:
            result = {"success": False, "error": "Unsupported platform"}

        # Update status
        if result.get('success'):
            update_scheduled_post_status(post_id, 'posted', last_result=json.dumps(result), attempt_count=1)
            return jsonify({"success": True, "message": f"Post {post_id} executed successfully"})
        else:
            update_scheduled_post_status(post_id, 'failed', last_result=json.dumps(result), attempt_count=1)
            return jsonify({"success": False, "error": result.get('error', 'Unknown error')})

    except Exception as e:
        logger.error(f"Error running post {post_id}: {e}")
        return jsonify({"success": False, "error": str(e)})


# -------------------------------
# Main execution
# -------------------------------
if __name__ == "__main__":
    print("🚀 Starting YouTube Summarizer Server...")
    print("✅ Scheduler is running in background")
    print("📊 Debug info available at /debug_schedules")
    app.run(host="0.0.0.0", port=5000, debug=True)

# from functions import *
# import os
# from flask import Flask, render_template, request, jsonify
# import traceback
# import json
# import hashlib
# import datetime
# import sqlite3
#
#
# @app.route("/", methods=["GET"])
# def index():
#     return render_template("index.html")
#
#
# @app.route("/get_transcript", methods=["POST"])
# def get_transcript():
#     url = request.json.get("youtube_url")
#     if not url:
#         return jsonify({"error": "Please enter a valid URL."}), 400
#     try:
#         video_details = get_video_details(url)
#         if not video_details:
#             return jsonify({"error": "Could not fetch video details."}), 400
#
#         # Download audio
#         audio_file = download_audio(url)
#
#         # Transcribe without chunking
#         transcript = transcribe_audio(audio_file)
#
#         # Generate summarized transcript (longer summary)
#         summarized_transcript = summarize_text(transcript, max_length=300)
#
#         video_id = hashlib.md5(url.encode()).hexdigest()
#
#         # Update video data and save to file
#         video_data[video_id] = {
#             'transcript': transcript,
#             'summarized_transcript': summarized_transcript,
#             'details': video_details
#         }
#         save_video_data(video_data)
#
#         # Clean up audio file
#         if os.path.exists(audio_file):
#             os.remove(audio_file)
#
#         return jsonify({
#             "success": True,
#             "video_details": video_details,
#             "transcript": transcript,
#             "summarized_transcript": summarized_transcript,
#             "video_id": video_id
#         })
#     except Exception as e:
#         logger.error(f"Error getting transcript: {e}")
#         print(traceback.format_exc())
#         return jsonify({"error": str(e)}), 500
#
#
# @app.route("/get_summary", methods=["POST"])
# def get_summary():
#     video_id = request.json.get("video_id")
#     if not video_id or video_id not in video_data:
#         return jsonify({"error": "No transcript found. Please get transcript first."}), 400
#
#     transcript = video_data[video_id]['transcript']
#     video_title = video_data[video_id]['details']['title']
#
#     # Generate platform-specific summaries
#     summaries = {
#         "twitter": summarize_text(transcript, max_length=100),
#         "telegram": summarize_text(transcript, max_length=800),
#         "discord": summarize_text(transcript, max_length=1000),
#         "full": video_data[video_id].get('summarized_transcript', '')  # Use the pre-generated full summary
#     }
#
#     video_data[video_id]['summaries'] = summaries
#     save_video_data(video_data)  # Save updated data with summaries
#
#     return jsonify({
#         "success": True,
#         "summaries": summaries,
#         "discord_configured": discord_configured
#     })
#
#
# @app.route("/post_to_social", methods=["POST"])
# def post_to_social():
#     video_id = request.json.get("video_id")
#     platform = request.json.get("platform")
#     if not video_id or video_id not in video_data:
#         return jsonify({"error": "No video data found."}), 400
#
#     video_info = video_data[video_id]
#     summary = video_info['summaries'].get(platform)
#     video_title = video_info['details']['title']
#     video_details = video_info['details']
#
#     if platform == "telegram":
#         telegram_message = f"🎥 <b>{video_title}</b>\n\n{summary}\n\n#YouTube #Summary"
#         result = post_to_telegram(telegram_message, photo_url=video_details.get('thumbnail'))
#         # message = f"🎥 <b>{video_title}</b>\n\n{summary}\n\n#YouTube #Summary"
#         # result = post_to_telegram(message, photo_url=video_details.get('thumbnail'))
#     elif platform == "discord":
#         if discord_configured:
#             result = post_to_discord(summary, video_title, video_details)
#         else:
#             message = create_discord_message(summary, video_title, video_details)
#             result = {"success": True, "message": "Discord message ready - copy/paste", "discord_message": message}
#     elif platform == "twitter":
#         result = generate_twitter_post(summary, video_title, video_details, video_id)
#     else:
#         result = {"success": False, "error": "Unsupported platform"}
#
#     return jsonify(result)
#
#
# @app.route("/schedule_post", methods=["POST"])
# def schedule_post():
#     data = request.json
#     video_id = data.get("video_id")
#     platform = data.get("platform")
#     schedule_time = data.get("schedule_time")
#     post_now_flag = data.get("post_now", False)
#
#     logger.info(f"Scheduling post - Video: {video_id}, Platform: {platform}, Time: {schedule_time}")
#
#     if not video_id or video_id not in video_data:
#         return jsonify({"error": "No video data found."}), 400
#     if not platform:
#         return jsonify({"error": "Platform missing."}), 400
#
#     if post_now_flag or not schedule_time:
#         # Immediate posting
#         video_info = video_data[video_id]
#         summary = video_info['summaries'].get(platform)
#         video_title = video_info['details']['title']
#         video_details = video_info['details']
#
#         if platform == "telegram":
#             message = f"🎥 <b>{video_title}</b>\n\n{summary}\n\n#YouTube #Summary"
#             result = post_to_telegram(message, photo_url=video_details.get('thumbnail'))
#         elif platform == "discord":
#             if discord_configured:
#                 result = post_to_discord(summary, video_title, video_details)
#             else:
#                 message = create_discord_message(summary, video_title, video_details)
#                 result = {"success": True, "message": "Discord message ready - copy/paste", "discord_message": message}
#         elif platform == "twitter":
#             result = generate_twitter_post(summary, video_title, video_details, video_id)
#         else:
#             result = {"success": False, "error": "Unsupported platform"}
#         return jsonify(result)
#
#     try:
#         # Schedule for later
#         utc_dt = local_datetime_string_to_utc(schedule_time)
#         row_id = insert_scheduled_post(video_id, platform, utc_dt)
#
#         # Format local time for display
#         local_dt = datetime.datetime.fromisoformat(schedule_time)
#         formatted_time = local_dt.strftime("%Y-%m-%d %H:%M:%S")
#
#         return jsonify({
#             "success": True,
#             "message": f"Post scheduled successfully for {formatted_time}",
#             "scheduled_id": row_id,
#             "schedule_time_utc": utc_dt.isoformat(),
#             "schedule_time_local": formatted_time
#         })
#     except Exception as e:
#         logger.error(f"Scheduling error: {e}")
#         return jsonify({"success": False, "error": f"Scheduling failed: {str(e)}"}), 500
#
#
# @app.route("/custom_tweet", methods=["POST"])
# def custom_tweet():
#     """
#     Generate Twitter share URL for custom text
#     """
#     try:
#         data = request.json
#         text = data.get("text", "")
#         url = data.get("url", "")
#         hashtags = data.get("hashtags", "")
#
#         if not text:
#             return jsonify({"success": False, "error": "Tweet text is required"}), 400
#
#         twitter_url = create_twitter_share_url(text, url, hashtags)
#
#         return jsonify({
#             "success": True,
#             "twitter_url": twitter_url,
#             "tweet_text": text,
#             "message": "Twitter share URL generated successfully!"
#         })
#
#     except Exception as e:
#         logger.error(f"Custom tweet error: {e}")
#         return jsonify({"success": False, "error": str(e)}), 500
#
#
# @app.route("/debug_schedules", methods=["GET"])
# def debug_schedules():
#     """Debug endpoint to check scheduled posts"""
#     conn = sqlite3.connect(DB_FILE)
#     c = conn.cursor()
#     c.execute("""
#               SELECT id, video_id, platform, schedule_time_utc, status, created_at
#               FROM scheduled_posts
#               ORDER BY schedule_time_utc DESC LIMIT 10
#               """)
#     rows = c.fetchall()
#     conn.close()
#
#     schedules = []
#     for row in rows:
#         schedules.append({
#             'id': row[0],
#             'video_id': row[1],
#             'platform': row[2],
#             'schedule_time_utc': row[3],
#             'status': row[4],
#             'created_at': row[5]
#         })
#
#     return jsonify({
#         'current_time_utc': datetime.datetime.utcnow().isoformat(),
#         'schedules': schedules,
#         'video_data_keys': list(video_data.keys())
#     })
#
#
# @app.route("/health", methods=["GET"])
# def health_check():
#     return jsonify({
#         "status": "healthy",
#         "timestamp": datetime.datetime.utcnow().isoformat(),
#         "scheduler_alive": scheduler_thread.is_alive()
#     })
#
#
# # -------------------------------
# # Admin Routes
# # -------------------------------
# @app.route("/admin")
# def admin():
#     """Admin dashboard page"""
#     return render_template("admin.html")
#
#
# @app.route("/admin/api/scheduled_posts")
# def admin_scheduled_posts():
#     """API endpoint to get all scheduled posts"""
#     try:
#         conn = sqlite3.connect(DB_FILE)
#         c = conn.cursor()
#         c.execute("""
#                   SELECT id,
#                          video_id,
#                          platform,
#                          schedule_time_utc,
#                          status,
#                          attempt_count,
#                          last_result,
#                          created_at
#                   FROM scheduled_posts
#                   ORDER BY schedule_time_utc DESC
#                   """)
#         rows = c.fetchall()
#         conn.close()
#
#         posts = []
#         for row in rows:
#             posts.append({
#                 'id': row[0],
#                 'video_id': row[1],
#                 'platform': row[2],
#                 'schedule_time_utc': row[3],
#                 'status': row[4],
#                 'attempt_count': row[5],
#                 'last_result': row[6],
#                 'created_at': row[7]
#             })
#
#         return jsonify({"success": True, "posts": posts})
#     except Exception as e:
#         logger.error(f"Error fetching scheduled posts: {e}")
#         return jsonify({"success": False, "error": str(e)})
#
#
# @app.route("/admin/api/video_data")
# def admin_video_data():
#     """API endpoint to get all video data"""
#     try:
#         video_data = load_video_data()
#         return jsonify({"success": True, "video_data": video_data})
#     except Exception as e:
#         logger.error(f"Error fetching video data: {e}")
#         return jsonify({"success": False, "error": str(e)})
#
#
# @app.route("/admin/api/system_status")
# def admin_system_status():
#     """API endpoint to get system status"""
#     try:
#         # Database status
#         conn = sqlite3.connect(DB_FILE)
#         c = conn.cursor()
#
#         # Count scheduled posts by status
#         c.execute("""
#                   SELECT status, COUNT(*) as count
#                   FROM scheduled_posts
#                   GROUP BY status
#                   """)
#         status_counts = dict(c.fetchall())
#
#         # Total videos processed
#         video_count = len(video_data)
#
#         # Scheduler status
#         scheduler_alive = scheduler_thread.is_alive()
#
#         conn.close()
#
#         return jsonify({
#             "success": True,
#             "system_status": {
#                 "scheduler_alive": scheduler_alive,
#                 "video_count": video_count,
#                 "post_status_counts": status_counts,
#                 "current_time_utc": datetime.datetime.utcnow().isoformat(),
#                 "database_file": DB_FILE,
#                 "video_data_file": VIDEO_DATA_FILE
#             }
#         })
#     except Exception as e:
#         logger.error(f"Error fetching system status: {e}")
#         return jsonify({"success": False, "error": str(e)})
#
#
# @app.route("/admin/api/delete_scheduled_post/<int:post_id>", methods=["DELETE"])
# def delete_scheduled_post(post_id):
#     """Delete a scheduled post"""
#     try:
#         conn = sqlite3.connect(DB_FILE)
#         c = conn.cursor()
#         c.execute("DELETE FROM scheduled_posts WHERE id = ?", (post_id,))
#         conn.commit()
#         deleted = c.rowcount > 0
#         conn.close()
#
#         if deleted:
#             logger.info(f"Deleted scheduled post {post_id}")
#             return jsonify({"success": True, "message": f"Post {post_id} deleted successfully"})
#         else:
#             return jsonify({"success": False, "error": f"Post {post_id} not found"})
#     except Exception as e:
#         logger.error(f"Error deleting scheduled post {post_id}: {e}")
#         return jsonify({"success": False, "error": str(e)})
#
#
# @app.route("/admin/api/update_post_status", methods=["POST"])
# def update_post_status():
#     """Update post status manually"""
#     try:
#         data = request.json
#         post_id = data.get('post_id')
#         status = data.get('status')
#
#         if not post_id or not status:
#             return jsonify({"success": False, "error": "Missing post_id or status"})
#
#         update_scheduled_post_status(post_id, status, "Manually updated by admin")
#
#         return jsonify({"success": True, "message": f"Post {post_id} status updated to {status}"})
#     except Exception as e:
#         logger.error(f"Error updating post status: {e}")
#         return jsonify({"success": False, "error": str(e)})
#
#
# @app.route("/admin/api/delete_video/<video_id>", methods=["DELETE"])
# def delete_video(video_id):
#     """Delete video data"""
#     try:
#         if video_id in video_data:
#             del video_data[video_id]
#             save_video_data(video_data)
#             logger.info(f"Deleted video data for {video_id}")
#             return jsonify({"success": True, "message": f"Video {video_id} deleted successfully"})
#         else:
#             return jsonify({"success": False, "error": f"Video {video_id} not found"})
#     except Exception as e:
#         logger.error(f"Error deleting video {video_id}: {e}")
#         return jsonify({"success": False, "error": str(e)})
#
#
# @app.route("/admin/api/run_post_now/<int:post_id>", methods=["POST"])
# def run_post_now(post_id):
#     """Run a scheduled post immediately"""
#     try:
#         conn = sqlite3.connect(DB_FILE)
#         c = conn.cursor()
#         c.execute("SELECT video_id, platform FROM scheduled_posts WHERE id = ?", (post_id,))
#         row = c.fetchone()
#         conn.close()
#
#         if not row:
#             return jsonify({"success": False, "error": f"Post {post_id} not found"})
#
#         video_id, platform = row
#
#         # Load video data
#         current_video_data = load_video_data()
#         video_info = current_video_data.get(video_id)
#
#         if not video_info:
#             return jsonify({"success": False, "error": f"Video data for {video_id} not found"})
#
#         summary = video_info['summaries'].get(platform)
#         video_title = video_info['details']['title']
#         video_details = video_info['details']
#
#         # Post immediately
#         if platform == "telegram":
#             message = f"🎥 <b>{video_title}</b>\n\n{summary}\n\n#YouTube #Summary"
#             result = post_to_telegram(message, photo_url=video_details.get('thumbnail'))
#         elif platform == "discord":
#             if discord_configured:
#                 result = post_to_discord(summary, video_title, video_details)
#             else:
#                 message = create_discord_message(summary, video_title, video_details)
#                 result = {"success": True, "message": "Discord message ready - copy/paste", "discord_message": message}
#         elif platform == "twitter":
#             result = generate_twitter_post(summary, video_title, video_details, video_id)
#         else:
#             result = {"success": False, "error": "Unsupported platform"}
#
#         # Update status
#         if result.get('success'):
#             update_scheduled_post_status(post_id, 'posted', last_result=json.dumps(result), attempt_count=1)
#             return jsonify({"success": True, "message": f"Post {post_id} executed successfully"})
#         else:
#             update_scheduled_post_status(post_id, 'failed', last_result=json.dumps(result), attempt_count=1)
#             return jsonify({"success": False, "error": result.get('error', 'Unknown error')})
#
#     except Exception as e:
#         logger.error(f"Error running post {post_id}: {e}")
#         return jsonify({"success": False, "error": str(e)})
#
#
# # -------------------------------
# # Main execution
# # -------------------------------
# if __name__ == "__main__":
#     print("🚀 Starting YouTube Summarizer Server...")
#     print("✅ Scheduler is running in background")
#     print("📊 Debug info available at /debug_schedules")
#     app.run(debug=True)