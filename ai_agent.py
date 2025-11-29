from flask import request, jsonify
import threading
import hashlib
import traceback

def add_flask_route(
    app,
    video_data,
    save_video_data,
    download_audio,
    transcribe_audio,
    summarize_text,
    get_video_details,
    post_to_telegram,
    post_to_discord,
    # post_to_twitter,
):
    """
    Adds AI Agent routes to the Flask app.
    All necessary functions are passed in to avoid circular imports.
    """

    @app.route("/ask_agent", methods=["POST"])
    def ask_agent():
        """
        AI Agent endpoint: given a YouTube URL, it transcribes,
        summarizes, and posts to configured platforms.
        """
        try:
            data = request.json
            youtube_url = data.get("youtube_url")
            if not youtube_url:
                return jsonify({"success": False, "error": "Missing YouTube URL"}), 400

            # Step 1: Download audio
            audio_file = download_audio(youtube_url)

            # Step 2: Transcribe
            transcript = transcribe_audio(audio_file)

            # Step 3: Summarize
            summary = summarize_text(transcript)

            # Step 4: Get video details
            video_details = get_video_details(youtube_url)
            if not video_details:
                return jsonify({"success": False, "error": "Could not fetch video details"}), 400

            # Step 5: Save to video_data
            video_id = hashlib.md5(youtube_url.encode()).hexdigest()
            video_data[video_id] = {
                "transcript": transcript,
                "summarized_transcript": summary,
                "details": video_details,
                "summaries": {"full": summary}
            }
            save_video_data(video_data)

            # Step 6: Post to social media platforms
            post_results = {}
            video_title = video_details['title']
            thumbnail = video_details.get('thumbnail')

            # Post to Telegram with photo
            telegram_message = f"🎥 <b>{video_title}</b>\n\n{summary}\n\n#YouTube #Summary"
            telegram_result = post_to_telegram(telegram_message, photo_url=thumbnail)  # Keep photo_url here
            post_results["telegram"] = telegram_result

            # Post to Discord
            discord_result = post_to_discord(summary, video_title, video_details)
            post_results['discord'] = discord_result

            # Twitter (with thumbnail)
            # twitter_result = post_to_twitter(summary, thumbnail)
            # post_results["twitter"] = twitter_result

            # Clean up audio file
            import os
            if os.path.exists(audio_file):
                os.remove(audio_file)

            return jsonify({
                "success": True,
                "video_id": video_id,
                "summary": summary,
                "post_results": post_results
            })

        except Exception as e:
            traceback_str = traceback.format_exc()
            return jsonify({"success": False, "error": str(e), "traceback": traceback_str}), 500

def run_agent_thread(add_flask_route_fn):
    """
    Optional: Run AI agent as a background thread if needed.
    """
    thread = threading.Thread(target=add_flask_route_fn, daemon=True)
    thread.start()