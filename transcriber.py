import yt_dlp
import os
import shutil
import re # For VTT parsing and filename sanitization
from flask import Flask, request, jsonify, send_from_directory, url_for, Response # Added Response
import logging
import uuid # For unique temporary transcript file names
from datetime import datetime # For timestamped filenames

# --- Flask App Setup ---
app = Flask(__name__)
app.logger.setLevel(logging.INFO) # Set logging level for the application logger

# --- Directory Configuration ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_BASE_DIR = os.path.join(BASE_DIR, "api_downloads") # For MP3s
TRANSCRIPTS_TEMP_DIR = os.path.join(BASE_DIR, "api_transcripts_temp") # For temporary VTT files

if not os.path.exists(DOWNLOADS_BASE_DIR):
    os.makedirs(DOWNLOADS_BASE_DIR)
    app.logger.info(f"Created base MP3 downloads directory: {DOWNLOADS_BASE_DIR}")
if not os.path.exists(TRANSCRIPTS_TEMP_DIR):
    os.makedirs(TRANSCRIPTS_TEMP_DIR)
    app.logger.info(f"Created temporary transcripts directory: {TRANSCRIPTS_TEMP_DIR}")

# --- Constants ---
SOCKET_TIMEOUT_SECONDS = 180
COMMON_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

# --- Read Proxy from Environment Variable ---
PROXY_URL_FROM_ENV = os.environ.get('PROXY_URL')
if PROXY_URL_FROM_ENV:
    app.logger.info(f"Using proxy from environment variable: {PROXY_URL_FROM_ENV.split('@')[1] if '@' in PROXY_URL_FROM_ENV else 'Proxy configured (details hidden)'}")
else:
    app.logger.info("PROXY_URL environment variable not set. Operating without proxy.")

def is_ffmpeg_available():
    """Checks if FFmpeg is installed and accessible."""
    return shutil.which("ffmpeg") is not None

def sanitize_filename(name_str, max_length=60): # Reduced max_length to accommodate timestamp
    """Sanitizes a string to be a safe filename component."""
    s = name_str.replace(' ', '_') # Replace spaces with underscores first
    # Keep only alphanumeric, underscore, hyphen. Replace others with underscore.
    s = "".join(c if c.isalnum() or c in ('-', '_') else '_' for c in s)
    s = re.sub(r'_+', '_', s) # Remove consecutive underscores
    s = s.strip('_') # Remove leading/trailing underscores
    return s[:max_length]

def vtt_to_plaintext(vtt_content):
    """
    Converts VTT subtitle content to plain text, removing consecutive duplicates.
    """
    processed_lines = []
    for line in vtt_content.splitlines():
        line_stripped = line.strip()
        # Skip WEBVTT header, cue numbers, and timestamps
        if line_stripped == "WEBVTT" or \
           "-->" in line_stripped or \
           (line_stripped.isdigit() and not any(c.isalpha() for c in line_stripped)): # Check if it's purely numeric
            continue
        
        if line_stripped: # If not empty after stripping
            # Clean VTT tags (e.g., <v Author>Text</v> or <i>Text</i>)
            cleaned_line = re.sub(r'<[^>]+>', '', line_stripped)
            # Replace common HTML entities that might appear
            cleaned_line = cleaned_line.replace(' ', ' ').replace('&', '&').replace('<', '<').replace('>', '>')
            processed_lines.append(cleaned_line)

    if not processed_lines:
        return ""

    # Deduplicate consecutive identical lines after cleaning
    deduplicated_text_lines = []
    last_added_line_stripped = None 
    for text_line in processed_lines:
        current_line_stripped = text_line.strip() # Strip for comparison
        if current_line_stripped and current_line_stripped != last_added_line_stripped:
            deduplicated_text_lines.append(text_line) # Add the original line (with its spacing)
            last_added_line_stripped = current_line_stripped
        elif not current_line_stripped and last_added_line_stripped is not None: # Handle empty lines after text
            deduplicated_text_lines.append(text_line) # Keep the empty line if it was intentional
            last_added_line_stripped = None # Reset so the next non-empty line isn't seen as a duplicate of an empty line

    return "\n".join(deduplicated_text_lines)

def _get_common_ydl_opts():
    """Helper function for common yt-dlp options, including proxy."""
    opts = {
        'socket_timeout': SOCKET_TIMEOUT_SECONDS,
        'http_headers': {'User-Agent': COMMON_USER_AGENT},
        'logger': app.logger, # Direct yt-dlp logs to Flask/Gunicorn logger
        'noplaylist': True,
        'noprogress': True, # Good for API logs
        'verbose': False,   
    }
    if PROXY_URL_FROM_ENV:
        opts['proxy'] = PROXY_URL_FROM_ENV
    return opts

def extract_audio_from_video(video_url, audio_format="mp3"):
    """Downloads audio from a video URL."""
    app.logger.info(f"Audio extraction request for URL: {video_url}")
    if not is_ffmpeg_available():
        error_msg = "FFmpeg is not installed or not found. It is required for audio conversion."
        app.logger.error(error_msg)
        return {"error": error_msg, "audio_server_path": None, "audio_relative_path": None}

    result_paths = {"audio_server_path": None, "audio_relative_path": None, "error": None}
    try:
        common_opts = _get_common_ydl_opts()
        info_opts = {**common_opts, 'quiet': True, 'extract_flat': 'in_playlist'}
        
        with yt_dlp.YoutubeDL(info_opts) as ydl_info:
            app.logger.info(f"Fetching video metadata for audio: {video_url}...")
            info_dict = ydl_info.extract_info(video_url, download=False)
            video_title = info_dict.get('title', f'unknown_video_{uuid.uuid4().hex[:6]}')
            app.logger.info(f"Original video title for audio: '{video_title}'")

        current_time_str = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        sanitized_title_part = sanitize_filename(video_title)
        base_output_filename_safe = f"{current_time_str}_{sanitized_title_part}"
        app.logger.info(f"Timestamped and sanitized base filename: '{base_output_filename_safe}'")
        
        request_folder_name = base_output_filename_safe
        request_download_dir_abs = os.path.join(DOWNLOADS_BASE_DIR, request_folder_name)
        
        if not os.path.exists(request_download_dir_abs):
            os.makedirs(request_download_dir_abs)
            app.logger.info(f"Created request-specific audio download directory: {request_download_dir_abs}")
        
        actual_disk_filename_template = f'{base_output_filename_safe}.%(ext)s' # yt-dlp will fill in the extension
        output_template_audio_abs = os.path.join(request_download_dir_abs, actual_disk_filename_template)

        ydl_opts_download = {
            **common_opts,
            'format': 'bestaudio/best',
            'outtmpl': output_template_audio_abs, # yt-dlp uses this template
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': audio_format}],
            'quiet': False, # Allow yt-dlp to show its download progress
            'noprogress': False, # Ensure progress is shown
        }
        with yt_dlp.YoutubeDL(ydl_opts_download) as ydl:
            app.logger.info(f"Starting audio download/extraction for {video_url} to template {output_template_audio_abs}")
            error_code = ydl.download([video_url])
            if error_code != 0:
                result_paths["error"] = f"yt-dlp audio process failed (code {error_code})."
                return result_paths
        
        final_audio_filename_on_disk = f"{base_output_filename_safe}.{audio_format}"
        result_paths["audio_server_path"] = os.path.join(request_download_dir_abs, final_audio_filename_on_disk)
        result_paths["audio_relative_path"] = os.path.join(request_folder_name, final_audio_filename_on_disk)
        
        if not os.path.exists(result_paths["audio_server_path"]):
            result_paths["error"] = f"Audio file not found post-processing at {result_paths['audio_server_path']}. Check yt-dlp output template and FFmpeg conversion."
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
    """Downloads YouTube transcript (VTT), parses it to plain text, and returns it."""
    app.logger.info(f"Transcript request for YouTube URL: {video_url}")
    result_data = {"transcript_text": None, "language_detected": None, "error": None}
    
    temp_vtt_basename = f"transcript_{uuid.uuid4().hex}" # Unique base name for the temp file
    temp_vtt_dir = TRANSCRIPTS_TEMP_DIR
    # yt-dlp will append '.<lang>.vtt' to this path for the actual subtitle file
    output_template_transcript_abs = os.path.join(temp_vtt_dir, temp_vtt_basename) 

    common_opts = _get_common_ydl_opts()
    ydl_opts = {
        **common_opts,
        'writesubtitles': True,
        'writeautomaticsub': True,  # Attempt to get auto-generated if manual isn't found for lang
        'subtitleslangs': ['en', 'ro'], # Try English first, then Romanian
        'subtitlesformat': 'vtt',
        'skip_download': True,      # IMPORTANT: Only download subtitles
        'outtmpl': output_template_transcript_abs, # Base path for subtitle file
        'quiet': False, 
        'noprogress': False,
    }

    downloaded_vtt_path = None
    actual_lang_code = None

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            app.logger.info(f"Starting direct transcript download for {video_url} (langs: ro, en) with timeout {SOCKET_TIMEOUT_SECONDS}s...")
            # extract_info with download=True will trigger subtitle download based on ydl_opts
            info_dict = ydl.extract_info(video_url, download=True) 
            
            # After download, determine the actual file path and language
            # yt-dlp stores info about downloaded subtitles in 'requested_subtitles'
            requested_subs = info_dict.get('requested_subtitles')
            if requested_subs:
                # Check for 'ro' or 'en' in the downloaded subs, in our preferred order
                for lang_code in ['en', 'ro']: 
                    if lang_code in requested_subs:
                        sub_info = requested_subs[lang_code]
                        # Check if 'filepath' key exists and the file is actually on disk
                        if sub_info.get('filepath') and os.path.exists(sub_info['filepath']):
                            downloaded_vtt_path = sub_info['filepath']
                            actual_lang_code = lang_code
                            app.logger.info(f"Transcript downloaded: {downloaded_vtt_path} (Language: {actual_lang_code})")
                            break # Found preferred language
            
            # If not found via requested_subtitles (e.g., auto-subs might behave differently or if path isn't there)
            # Fallback: scan the directory for the expected file pattern
            if not downloaded_vtt_path:
                app.logger.info("Transcript path not in 'requested_subtitles', scanning directory...")
                for lang in ['ro', 'en']:
                    # yt-dlp usually names it <outtmpl>.<lang>.vtt
                    potential_path = os.path.join(temp_vtt_dir, f"{temp_vtt_basename}.{lang}.vtt")
                    if os.path.exists(potential_path):
                        downloaded_vtt_path = potential_path
                        actual_lang_code = lang
                        app.logger.info(f"Transcript found by scanning: {downloaded_vtt_path} (Language: {actual_lang_code})")
                        break # Found a transcript
            
            if not downloaded_vtt_path:
                result_data["error"] = "Transcript VTT file not found after download attempt or not available in RO/EN."
                app.logger.warning(result_data["error"])
                return result_data

        with open(downloaded_vtt_path, 'r', encoding='utf-8') as f:
            vtt_content = f.read()
        
        result_data["transcript_text"] = vtt_to_plaintext(vtt_content)
        result_data["language_detected"] = actual_lang_code
        app.logger.info(f"Transcript parsed successfully for language: {actual_lang_code}")

    except yt_dlp.utils.DownloadError as de_yt: # Catch specific yt-dlp download errors
        app.logger.error(f"yt-dlp DownloadError during transcript processing for {video_url}: {de_yt}")
        result_data["error"] = f"yt-dlp DownloadError: {str(de_yt)}" # Return the yt-dlp error message
    except Exception as e:
        app.logger.error(f"Error in get_youtube_transcript_text for {video_url}: {e}", exc_info=True)
        result_data["error"] = f"Unexpected error during transcript processing: {str(e)}"
    finally:
        # Clean up the temporary VTT file
        if downloaded_vtt_path and os.path.exists(downloaded_vtt_path):
            try:
                os.remove(downloaded_vtt_path)
                app.logger.info(f"Deleted temporary transcript file: {downloaded_vtt_path}")
            except Exception as e_del:
                app.logger.error(f"Error deleting temporary transcript file {downloaded_vtt_path}: {e_del}")
    return result_data

# --- API Endpoints ---
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
        "audio_server_path": result.get("audio_server_path"), # For internal debugging
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
    
    result = get_youtube_transcript_text(video_url_param)

    if result.get("error"):
        # Errors are still returned as JSON so the error message can be parsed
        return jsonify({"error": result["error"], "language_detected": result.get("language_detected"), "transcript_text": None}), 500
    elif result.get("transcript_text") is not None:
        # Return plain text directly, with Content-Type text/plain
        return Response(result["transcript_text"], mimetype='text/plain; charset=utf-8')
    else:
        app.logger.error("Unexpected result from get_youtube_transcript_text: no text and no error.")
        return jsonify({"error": "Unexpected internal error processing transcript."}), 500

@app.route('/files/<path:relative_file_path>')
def serve_downloaded_file(relative_file_path):
    app.logger.info(f"Request to serve file. Base directory: '{DOWNLOADS_BASE_DIR}', Relative path from URL: '{relative_file_path}'")
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
    """Simple health check endpoint."""
    return jsonify({"status": "healthy"}), 200

# --- Main execution (for local testing) ---
if __name__ == '__main__':
    app.logger.info("--- Starting Flask app locally (for development) ---")
    if PROXY_URL_FROM_ENV:
        app.logger.info(f"Local run would use proxy: {PROXY_URL_FROM_ENV.split('@')[1] if '@' in PROXY_URL_FROM_ENV else 'Proxy configured'}")
    if not is_ffmpeg_available():
        app.logger.critical("CRITICAL: FFmpeg is not installed or not found. This API requires FFmpeg.")
    else:
        app.logger.info("FFmpeg found (local check).")
    app.logger.info(f"MP3s will be saved under: {DOWNLOADS_BASE_DIR}")
    app.logger.info(f"Temp transcripts under: {TRANSCRIPTS_TEMP_DIR}")
    app.run(host='0.0.0.0', port=5001, debug=True) # debug=True provides more Flask output
