from functions import *
import os
from flask import Flask, render_template, request, jsonify
import traceback
import json
import hashlib
import datetime
import sqlite3
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