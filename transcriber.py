# ... (other imports and functions) ...
import yt_dlp # Ensure this is at the top
import os
import shutil
import re
import uuid
from datetime import datetime
# --- (your existing imports)

# ... (your existing Flask app setup, sanitize_filename, vtt_to_plaintext, etc.) ...

def get_youtube_details_and_transcript(video_url): # Renamed for clarity, or create a new one
    app.logger.info(f"Details and transcript requested for YouTube URL: {video_url}")
    # Initialize all fields to None or appropriate defaults
    result_data = {
        "title": None,
        "channel_name": None,
        "transcript_text": None,
        "language_detected": None,
        "error": None
    }
    
    temp_vtt_basename = f"transcript_{uuid.uuid4().hex}"
    temp_vtt_dir = TRANSCRIPTS_TEMP_DIR # Make sure TRANSCRIPTS_TEMP_DIR is defined
    output_template_transcript_abs = os.path.join(temp_vtt_dir, temp_vtt_basename)

    ydl_opts = {
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['ro', 'en'], # Prioritize Romanian, then English
        'subtitlesformat': 'vtt',
        'skip_download': True, # Don't download the video itself
        'outtmpl': output_template_transcript_abs, 
        'noplaylist': True,
        'noprogress': True,
        'verbose': False, 
        'logger': app.logger, # Assuming app.logger is configured
        'socket_timeout': SOCKET_TIMEOUT_SECONDS # Make sure SOCKET_TIMEOUT_SECONDS is defined
    }
    downloaded_vtt_path = None
    actual_lang_code = None

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            app.logger.info(f"Fetching info and transcript for {video_url} (langs: ro, en)...")
            info_dict = ydl.extract_info(video_url, download=True) # download=True for subtitles

            # Extract title and channel name
            result_data["title"] = info_dict.get('title', 'Unknown Title')
            result_data["channel_name"] = info_dict.get('uploader', info_dict.get('channel', 'Unknown Channel'))
            app.logger.info(f"Title: {result_data['title']}, Channel: {result_data['channel_name']}")

            requested_subs = info_dict.get('requested_subtitles')
            if requested_subs:
                for lang_code in ['ro', 'en']: # Check in order of preference
                    if lang_code in requested_subs:
                        sub_info = requested_subs[lang_code]
                        # yt-dlp might have already saved the file if 'filepath' is present
                        # and download=True was used in extract_info for subtitles
                        if sub_info.get('filepath') and os.path.exists(sub_info['filepath']):
                             downloaded_vtt_path = sub_info['filepath']
                        # If filepath isn't directly there, construct it based on outtmpl
                        # This part might need adjustment based on how yt-dlp names subtitle files
                        # when 'filepath' isn't in sub_info but subtitles were downloaded.
                        # For now, let's assume if 'filepath' is missing, we try the constructed path.
                        else:
                            potential_path = f"{output_template_transcript_abs}.{lang_code}.vtt"
                            if os.path.exists(potential_path):
                                downloaded_vtt_path = potential_path
                        
                        if downloaded_vtt_path:
                            actual_lang_code = lang_code
                            app.logger.info(f"Transcript downloaded/found: {downloaded_vtt_path} (Language: {actual_lang_code})")
                            break 
            
            # Fallback scan if not found via requested_subtitles (sometimes necessary)
            if not downloaded_vtt_path:
                app.logger.info("Transcript path not in 'requested_subtitles' or not directly found, scanning directory...")
                for lang in ['ro', 'en']:
                    potential_path = os.path.join(temp_vtt_dir, f"{temp_vtt_basename}.{lang}.vtt")
                    if os.path.exists(potential_path):
                        downloaded_vtt_path = potential_path
                        actual_lang_code = lang
                        app.logger.info(f"Transcript found by scanning: {downloaded_vtt_path} (Language: {actual_lang_code})")
                        break
            
            if not downloaded_vtt_path:
                result_data["error"] = "Transcript VTT file not found after download attempt or not available in RO/EN."
                app.logger.warning(result_data["error"])
                # Still return title and channel if found
                return result_data

            with open(downloaded_vtt_path, 'r', encoding='utf-8') as f:
                vtt_content = f.read()
            result_data["transcript_text"] = vtt_to_plaintext(vtt_content) # Ensure vtt_to_plaintext is defined
            result_data["language_detected"] = actual_lang_code
            app.logger.info(f"Transcript parsed successfully for language: {actual_lang_code}")

    except yt_dlp.utils.DownloadError as de_yt:
        error_message = str(de_yt)
        app.logger.error(f"yt-dlp DownloadError during YouTube processing for {video_url}: {error_message}")
        # Check if it's a "no subtitles" error specifically
        if "no subtitles found" in error_message.lower() or "subtitles not available" in error_message.lower():
            result_data["error"] = "No subtitles available for the requested languages (RO/EN)."
            app.logger.warning(result_data["error"])
            # Still return title and channel if they were fetched before this error
            # This requires fetching metadata separately if skip_download was truly effective
            # For simplicity, if extract_info for subtitles fails this way, title/channel might also be missing
            # A pre-fetch for metadata only might be more robust if subtitles often fail
        else:
            result_data["error"] = f"yt-dlp DownloadError: {error_message}"
    except Exception as e:
        app.logger.error(f"Error in get_youtube_details_and_transcript for {video_url}: {e}", exc_info=True)
        result_data["error"] = f"Unexpected error during YouTube processing: {str(e)}"
    finally:
        if downloaded_vtt_path and os.path.exists(downloaded_vtt_path):
            try:
                os.remove(downloaded_vtt_path)
                app.logger.info(f"Deleted temporary transcript file: {downloaded_vtt_path}")
            except Exception as e_del:
                app.logger.error(f"Error deleting temporary transcript file {downloaded_vtt_path}: {e_del}")
    return result_data

# Modify your API endpoint to use this new function or return more data
@app.route('/api/get_youtube_details', methods=['GET']) # New endpoint name
def api_get_youtube_details():
    app.logger.info("Received request for /api/get_youtube_details")
    video_url_param = request.args.get('url')
    if not video_url_param:
        app.logger.warning("Missing 'url' parameter in /api/get_youtube_details request.")
        return jsonify({"error": "Missing 'url' parameter"}), 400
    
    # Basic check if it's a YouTube URL (can be more robust)
    if not ("https://www.youtube.com/watch?v=JIU_H7gXy544" in video_url_param.lower() or "youtube.com" in video_url_param.lower()):
        app.logger.warning(f"Non-YouTube URL provided to /api/get_youtube_details: {video_url_param}")
        return jsonify({"error": "Invalid URL; only YouTube URLs are supported by this endpoint."}), 400
        
    result = get_youtube_details_and_transcript(video_url_param)
    status_code = 500 if result.get("error") and not result.get("transcript_text") else 200 # Be more lenient if title/channel are there but transcript failed
    if result.get("error") and not result.get("title"): # If even title failed, definitely an issue
        status_code = 500

    return jsonify(result), status_code

# ... (your existing /api/extract_audio, /files/, /health endpoints and main app run) ...
