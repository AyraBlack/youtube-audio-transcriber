import yt_dlp
import os
import shutil
import re # For parsing VTT
from flask import Flask, request, jsonify, send_from_directory, url_for
import logging
import uuid # For unique temporary transcript file names
from datetime import datetime # Added for timestamped filenames

# --- Flask App Setup ---
app = Flask(__name__)
app.logger.setLevel(logging.INFO)

# --- Configuration ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_BASE_DIR = os.path.join(BASE_DIR, "api_downloads")
TRANSCRIPTS_TEMP_DIR = os.path.join(BASE_DIR, "api_transcripts_temp")

if not os.path.exists(DOWNLOADS_BASE_DIR):
    os.makedirs(DOWNLOADS_BASE_DIR)
    app.logger.info(f"Created base MP3 downloads directory: {DOWNLOADS_BASE_DIR}")
if not os.path.exists(TRANSCRIPTS_TEMP_DIR):
    os.makedirs(TRANSCRIPTS_TEMP_DIR)
    app.logger.info(f"Created temporary transcripts directory: {TRANSCRIPTS_TEMP_DIR}")

# --- Constants ---
SOCKET_TIMEOUT_SECONDS = 180
# Common User-Agent string
COMMON_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

def is_ffmpeg_available():
    return shutil.which("ffmpeg") is not None

def sanitize_filename(name_str, max_length=60): # Reduced max_length to accommodate timestamp
    """Sanitizes a string to be a safe filename component."""
    s = name_str.replace(' ', '_')
    s = "".join(c if c.isalnum() or c in ('-', '_') else '_' for c in s)
    s = re.sub(r'_+', '_', s)
    s = s.strip('_')
    return s[:max_length]

def vtt_to_plaintext(vtt_content):
    lines = vtt_content.splitlines()
    text_lines = []
    in_dialogue_block = False
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            in_dialogue_block = False
            continue
        if "-->" in line_stripped:
            in_dialogue_block = True
            continue
        if line_stripped.isdigit() and not in_dialogue_block:
            continue
        if in_dialogue_block:
            cleaned_line = re.sub(r'<[^>]+>', '', line_stripped) # Remove VTT tags like <v Author>Text</v> or <i>Text</i>
            cleaned_line = cleaned_line.replace(' ', ' ').replace('&', '&').replace('<', '<').replace('>', '>') # Handle common HTML entities
            if cleaned_line:
                text_lines.append(cleaned_line)
    return "\n".join(text_lines)

def extract_audio_from_video(video_url, audio_format="mp3"):
    app.logger.info(f"Audio extraction requested for URL: {video_url}")
    if not is_ffmpeg_available():
        error_msg = "FFmpeg is not installed or not found. It's required for audio conversion."
        app.logger.error(error_msg)
        return {"error": error_msg, "audio_server_path": None, "audio_relative_path": None}

    result_paths = {"audio_server_path": None, "audio_relative_path": None, "error": None}
    try:
        # Add User-Agent to info_opts
        info_opts = {
            'quiet': True, 'noplaylist': True, 'extract_flat': 'in_playlist',
            'socket_timeout': SOCKET_TIMEOUT_SECONDS,
            'http_headers': {'User-Agent': COMMON_USER_AGENT} # Added User-Agent
        }
        with yt_dlp.YoutubeDL(info_opts) as ydl_info:
            app.logger.info(f"Fetching video metadata for audio: {video_url}...")
            info_dict = ydl_info.extract_info(video_url, download=False)
            video_title = info_dict.get('title', f'unknown_video_{uuid.uuid4().hex[:6]}')
            app.logger.info(f"Video title for audio: '{video_title}'")

        current_time_str = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        sanitized_title_part = sanitize_filename(video_title)
        base_output_filename_safe = f"{current_time_str}_{sanitized_title_part}"
        app.logger.info(f"Timestamped and sanitized base filename: '{base_output_filename_safe}'")
        
        request_folder_name = base_output_filename_safe
        request_download_dir_abs = os.path.join(DOWNLOADS_BASE_DIR, request_folder_name)
        if not os.path.exists(request_download_dir_abs):
            os.makedirs(request_download_dir_abs)
            app.logger.info(f"Created request-specific audio download directory: {request_download_dir_abs}")
        
        actual_disk_filename_template = f'{base_output_filename_safe}.%(ext)s'
        output_template_audio_abs = os.path.join(request_download_dir_abs, actual_disk_filename_template)

        # Add User-Agent to ydl_opts
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': output_template_audio_abs,
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': audio_format}],
            'noplaylist': True, 'noprogress': True, 'verbose': False, 'logger': app.logger,
            'socket_timeout': SOCKET_TIMEOUT_SECONDS,
            'http_headers': {'User-Agent': COMMON_USER_AGENT} # Added User-Agent
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            app.logger.info(f"Starting audio download/extraction for {video_url}...")
            error_code = ydl.download([video_url])
            if error_code != 0:
                result_paths["error"] = f"yt-dlp audio process failed (code {error_code})."
                return result_paths
        
        final_audio_filename_on_disk = f"{base_output_filename_safe}.{audio_format}"
        result_paths["audio_server_path"] = os.path.join(request_download_dir_abs, final_audio_filename_on_disk)
        result_paths["audio_relative_path"] = os.path.join(request_folder_name, final_audio_filename_on_disk)
        
        if not os.path.exists(result_paths["audio_server_path"]):
            result_paths["error"] = "Audio file not found post-processing."
            result_paths["audio_server_path"] = None
            result_paths["audio_relative_path"] = None
        else:
            app.logger.info(f"Audio extracted: {result_paths['audio_server_path']}")
        return result_paths
    except Exception as e:
        app.logger.error(f"Error in extract_audio_from_video: {e}", exc_info=True)
        result_paths["error"] = f"Unexpected error during audio extraction: {str(e)}"
        return result_paths

def get_youtube_transcript_text(video_url):
    app.logger.info(f"Transcript requested for YouTube URL: {video_url}")
    result_data = {"transcript_text": None, "language_detected": None, "error": None}
    
    temp_vtt_basename = f"transcript_{uuid.uuid4().hex}"
    temp_vtt_dir = TRANSCRIPTS_TEMP_DIR
    output_template_transcript_abs = os.path.join(temp_vtt_dir, temp_vtt_basename) 

    # Add User-Agent to ydl_opts
    ydl_opts = {
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['ro', 'en'], 
        'subtitlesformat': 'vtt',
        'skip_download': True,      
        'outtmpl': output_template_transcript_abs, 
        'noplaylist': True, 
        'noprogress': True, 
        'verbose': False, 
        'logger': app.logger,
        'socket_timeout': SOCKET_TIMEOUT_SECONDS,
        'http_headers': {'User-Agent': COMMON_USER_AGENT} # Added User-Agent
    }

    downloaded_vtt_path = None
    actual_lang_code = None

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            app.logger.info(f"Starting direct transcript download for {video_url} (langs: ro, en) with timeout {SOCKET_TIMEOUT_SECONDS}s...")
            info_dict = ydl.extract_info(video_url, download=True) 
            
            requested_subs = info_dict.get('requested_subtitles')
            if requested_subs:
                for lang_code in ['ro', 'en']: 
                    if lang_code in requested_subs:
                        sub_info = requested_subs[lang_code]
                        if sub_info.get('filepath') and os.path.exists(sub_info['filepath']):
                            downloaded_vtt_path = sub_info['filepath']
                            actual_lang_code = lang_code
                            app.logger.info(f"Transcript downloaded: {downloaded_vtt_path} (Language: {actual_lang_code})")
                            break 
            
            if not downloaded_vtt_path:
                app.logger.info("Transcript path not in 'requested_subtitles', scanning directory...")
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
                return result_data

        with open(downloaded_vtt_path, 'r', encoding='utf-8') as f:
            vtt_content = f.read()
        
        result_data["transcript_text"] = vtt_to_plaintext(vtt_content)
        result_data["language_detected"] = actual_lang_code
        app.logger.info(f"Transcript parsed successfully for language: {actual_lang_code}")

    except yt_dlp.utils.DownloadError as de_yt:
        app.logger.error(f"yt-dlp DownloadError during transcript processing for {video_url}: {de_yt}")
        result_data["error"] = f"yt-dlp DownloadError: {str(de_yt)}"
    except Exception as e:
        app.logger.error(f"Error in get_youtube_transcript_text for {video_url}: {e}", exc_info=True)
        result_data["error"] = f"Unexpected error during transcript processing: {str(e)}"
    finally:
        if downloaded_vtt_path and os.path.exists(downloaded_vtt_path):
            try:
                os.remove(downloaded_vtt_path)
                app.logger.info(f"Deleted temporary transcript file: {downloaded_vtt_path}")
            except Exception as e_del:
                app.logger.error(f"Error deleting temporary transcript file {downloaded_vtt_path}: {e_del}")
    return result_data

@app.route('/api/extract_audio', methods=['GET'])
def api_extract_audio():
    app.logger.info("Received request for /api/extract_audio")
    video_url_param = request.args.get('url')
    if not video_url_param:
        app.logger.warning("Missing 'url' parameter in /api/extract_audio request.")
        return jsonify({"error": "Missing 'url' parameter"}), 400
    result = extract_audio_from_video(video_url_param)
    response_data = {
        "audio_download_url": None,
        "audio_server_path": result.get("audio_server_path"),
        "error": result.get("error")
    }
    if result.get("audio_relative_path"):
        response_data["audio_download_url"] = url_for('serve_downloaded_file', relative_file_path=result["audio_relative_path"], _external=True)
    status_code = 500 if response_data.get("error") else 200
    return jsonify(response_data), status_code

@app.route('/api/get_youtube_transcript', methods=['GET'])
def api_get_youtube_transcript():
    app.logger.info("Received request for /api/get_youtube_transcript")
    video_url_param = request.args.get('url')
    if not video_url_param:
        app.logger.warning("Missing 'url' parameter in /api/get_youtube_transcript request.")
        return jsonify({"error": "Missing 'url' parameter"}), 400
    # Removed the specific YouTube URL check to allow yt-dlp to try any URL it supports.
    # The function name get_youtube_transcript_text still implies YouTube, but yt-dlp might handle others.
    # For now, let's keep it flexible.
    result = get_youtube_transcript_text(video_url_param)
    status_code = 500 if result.get("error") else 200
    return jsonify(result), status_code

@app.route('/files/<path:relative_file_path>')
def serve_downloaded_file(relative_file_path):
    app.logger.info(f"Attempting to serve file. Base directory: '{DOWNLOADS_BASE_DIR}', Relative path from URL: '{relative_file_path}'")
    try:
        return send_from_directory(DOWNLOADS_BASE_DIR, relative_file_path, as_attachment=True)
    except FileNotFoundError:
        app.logger.error(f"FileNotFoundError: File not found for serving. Checked path: '{os.path.join(DOWNLOADS_BASE_DIR, relative_file_path)}'")
        return jsonify({"error": "File not found. It may have been moved, deleted, or the path is incorrect after processing."}), 404
    except Exception as e:
        app.logger.error(f"Error serving file '{relative_file_path}': {type(e).__name__} - {str(e)}", exc_info=True)
        return jsonify({"error": "Could not serve file due to an internal issue."}), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy"}), 200

if __name__ == '__main__':
    # This import is only needed if you run the script directly (python transcriber.py)
    # and use the timestamped filename logic, which is in extract_audio_from_video.
    # from datetime import datetime # Already imported at the top level
    app.logger.info("--- Starting Flask app locally (for development) ---")
    if not is_ffmpeg_available():
        app.logger.critical("CRITICAL: FFmpeg is not installed or not found. This API requires FFmpeg.")
    else:
        app.logger.info("FFmpeg found (local check).")
    app.logger.info(f"MP3s will be saved under: {DOWNLOADS_BASE_DIR}")
    app.logger.info(f"Temp transcripts under: {TRANSCRIPTS_TEMP_DIR}")
    app.run(host='0.0.0.0', port=5001, debug=True)
