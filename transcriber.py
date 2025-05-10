import yt_dlp
import os
import shutil
import re # For new sanitize_filename and VTT parsing
from flask import Flask, request, jsonify, send_from_directory, url_for # Ensure Flask and other necessary components are imported
import logging
import uuid # For unique temporary transcript file names
from datetime import datetime # Added for timestamped filenames

# --- Flask App Setup ---
# THIS MUST BE DEFINED BEFORE ANY @app.route DECORATORS
app = Flask(__name__)
app.logger.setLevel(logging.INFO)

# --- Configuration ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_BASE_DIR = os.path.join(BASE_DIR, "api_downloads") # For MP3s that will be served
TRANSCRIPTS_TEMP_DIR = os.path.join(BASE_DIR, "api_transcripts_temp") # For temporary VTT files
SOCKET_TIMEOUT_SECONDS = 180 # Define this if used globally

if not os.path.exists(DOWNLOADS_BASE_DIR):
    os.makedirs(DOWNLOADS_BASE_DIR)
    app.logger.info(f"Created base MP3 downloads directory: {DOWNLOADS_BASE_DIR}")
if not os.path.exists(TRANSCRIPTS_TEMP_DIR):
    os.makedirs(TRANSCRIPTS_TEMP_DIR)
    app.logger.info(f"Created temporary transcripts directory: {TRANSCRIPTS_TEMP_DIR}")

# --- Helper Functions ---
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
        if not line_stripped: # Empty line signifies end of a dialogue block or metadata
            in_dialogue_block = False
            continue
        if "-->" in line_stripped: # Timestamp line
            in_dialogue_block = True # The following lines are dialogue until an empty line
            continue
        # Skip VTT sequence numbers if they are not part of a dialogue block
        if line_stripped.isdigit() and not in_dialogue_block:
            continue
        
        if in_dialogue_block:
            # Remove VTT tags like <v Speaker Name> or alignment tags
            cleaned_line = re.sub(r'<[^>]+>', '', line_stripped)
            # Basic HTML entity handling that might appear in VTT
            # Using .replace('&', '&') is redundant, fixed others.
            cleaned_line = cleaned_line.replace('&', '&').replace('<', '<').replace('>', '>')
            if cleaned_line: # Add non-empty lines
                 text_lines.append(cleaned_line)
    return "\n".join(text_lines)

# --- Core Logic Functions ---

def extract_audio_from_video(video_url, audio_format="mp3"):
    # THIS IS YOUR EXISTING FUNCTION - UNCHANGED BY ME
    app.logger.info(f"Audio extraction requested for URL: {video_url}")
    if not is_ffmpeg_available():
        error_msg = "FFmpeg is not installed or not found. It's required for audio conversion."
        app.logger.error(error_msg)
        return {"error": error_msg, "audio_server_path": None, "audio_relative_path": None}

    result_paths = {"audio_server_path": None, "audio_relative_path": None, "error": None}
    try:
        info_opts = {'quiet': True, 'noplaylist': True, 'extract_flat': 'in_playlist', 'socket_timeout': SOCKET_TIMEOUT_SECONDS}
        with yt_dlp.YoutubeDL(info_opts) as ydl_info:
            app.logger.info(f"Fetching video metadata for audio: {video_url}...")
            info_dict = ydl_info.extract_info(video_url, download=False)
            video_title = info_dict.get('title', f'unknown_video_{uuid.uuid4().hex[:6]}')
            app.logger.info(f"Original video title for audio: '{video_title}'")

        current_time_str = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        sanitized_title = sanitize_filename(video_title)
        base_output_filename_safe = f"{current_time_str}_{sanitized_title}"
        app.logger.info(f"Timestamped and sanitized base filename: '{base_output_filename_safe}'")

        request_folder_name = base_output_filename_safe 
        request_download_dir_abs = os.path.join(DOWNLOADS_BASE_DIR, request_folder_name)
        
        if not os.path.exists(request_download_dir_abs):
            os.makedirs(request_download_dir_abs)
            app.logger.info(f"Created request-specific audio download directory: {request_download_dir_abs}")

        actual_disk_filename_template = f'{base_output_filename_safe}.%(ext)s' 
        output_template_audio_abs = os.path.join(request_download_dir_abs, actual_disk_filename_template)

        ydl_opts = { # Renamed from ydl_opts in your provided new function to avoid conflict if it was global
            'format': 'bestaudio/best',
            'outtmpl': output_template_audio_abs,
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': audio_format}],
            'noplaylist': True, 'noprogress': True, 'verbose': False, 'logger': app.logger,
            'socket_timeout': SOCKET_TIMEOUT_SECONDS
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
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

# --- START OF NEW FUNCTION TO ADD ---
def get_youtube_details_and_transcript(video_url):
    app.logger.info(f"Details and transcript requested for YouTube URL: {video_url}")
    result_data = {
        "title": None,
        "channel_name": None,
        "transcript_text": None,
        "language_detected": None,
        "error": None
    }
    
    temp_vtt_basename = f"transcript_{uuid.uuid4().hex}"
    # TRANSCRIPTS_TEMP_DIR and SOCKET_TIMEOUT_SECONDS should be defined globally
    output_template_transcript_abs = os.path.join(TRANSCRIPTS_TEMP_DIR, temp_vtt_basename)

    # ydl_opts for subtitles, distinct from the one in extract_audio_from_video
    ydl_opts_subs = { 
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
        'socket_timeout': SOCKET_TIMEOUT_SECONDS
    }
    downloaded_vtt_path = None
    actual_lang_code = None

    try:
        with yt_dlp.YoutubeDL(ydl_opts_subs) as ydl:
            app.logger.info(f"Fetching info and transcript for {video_url} (langs: ro, en)...")
            info_dict = ydl.extract_info(video_url, download=True) # download=True is needed for subtitles

            result_data["title"] = info_dict.get('title', 'Unknown Title')
            result_data["channel_name"] = info_dict.get('uploader', info_dict.get('channel', 'Unknown Channel'))
            app.logger.info(f"Title: {result_data['title']}, Channel: {result_data['channel_name']}")

            requested_subs = info_dict.get('requested_subtitles')
            if requested_subs:
                for lang_code_iter in ['ro', 'en']: 
                    if lang_code_iter in requested_subs:
                        sub_info = requested_subs[lang_code_iter]
                        if sub_info.get('filepath') and os.path.exists(sub_info['filepath']):
                            downloaded_vtt_path = sub_info['filepath']
                        else: 
                            # Construct potential path based on outtmpl and lang code
                            potential_path = f"{output_template_transcript_abs}.{lang_code_iter}.vtt"
                            if os.path.exists(potential_path):
                                downloaded_vtt_path = potential_path
                        
                        if downloaded_vtt_path:
                            actual_lang_code = lang_code_iter
                            app.logger.info(f"Transcript VTT found: {downloaded_vtt_path} (Language: {actual_lang_code})")
                            break 
            
            if not downloaded_vtt_path: 
                app.logger.info("Transcript path not directly in 'requested_subtitles', scanning directory for VTT files...")
                for lang_scan in ['ro', 'en']: 
                    # Corrected path construction for scanning: use temp_vtt_basename directly
                    # yt-dlp often names the file like <outtmpl>.<lang>.vtt
                    potential_path = os.path.join(TRANSCRIPTS_TEMP_DIR, f"{temp_vtt_basename}.{lang_scan}.vtt")
                    if os.path.exists(potential_path):
                        downloaded_vtt_path = potential_path
                        actual_lang_code = lang_scan
                        app.logger.info(f"Transcript found by scanning: {downloaded_vtt_path} (Language: {actual_lang_code})")
                        break
            
            if not downloaded_vtt_path:
                result_data["error"] = "Transcript VTT file not found after download attempt or not available in RO/EN."
                app.logger.warning(result_data["error"])
                # Return early if no transcript, but title/channel might be populated if fetched successfully before this point
                return result_data 

            with open(downloaded_vtt_path, 'r', encoding='utf-8') as f:
                vtt_content = f.read()
            result_data["transcript_text"] = vtt_to_plaintext(vtt_content) 
            result_data["language_detected"] = actual_lang_code
            app.logger.info(f"Transcript parsed successfully for language: {actual_lang_code}")

    except yt_dlp.utils.DownloadError as de_yt:
        error_message = str(de_yt)
        app.logger.error(f"yt-dlp DownloadError during YouTube processing for {video_url}: {error_message}")
        if "no subtitles found" in error_message.lower() or "subtitles not available" in error_message.lower():
            result_data["error"] = "No subtitles available for the requested languages (RO/EN)."
            # Attempt to get metadata even if subtitles fail, if not already fetched
            if not result_data["title"]: # Only if title wasn't fetched with subs attempt
                try:
                    # Use a simpler ydl_opts for metadata only if the subtitle-focused one failed early
                    with yt_dlp.YoutubeDL({'quiet': True, 'noplaylist': True, 'skip_download': True, 'logger': app.logger, 'socket_timeout': SOCKET_TIMEOUT_SECONDS}) as ydl_meta:
                        info_dict_meta = ydl_meta.extract_info(video_url, download=False)
                        result_data["title"] = info_dict_meta.get('title', 'Unknown Title')
                        result_data["channel_name"] = info_dict_meta.get('uploader', info_dict_meta.get('channel', 'Unknown Channel'))
                        app.logger.info(f"(Fallback metadata) Title: {result_data['title']}, Channel: {result_data['channel_name']}")
                except Exception as e_meta:
                    app.logger.error(f"Error fetching fallback metadata for {video_url}: {e_meta}")
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
# --- END OF NEW FUNCTION TO ADD ---


# --- API Endpoints ---
# THESE MUST BE AFTER 'app = Flask(__name__)' AND AFTER THE FUNCTIONS THEY CALL ARE DEFINED

@app.route('/api/extract_audio', methods=['GET'])
def api_extract_audio():
    # THIS IS YOUR EXISTING FUNCTION - UNCHANGED BY ME
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
        try:
            response_data["audio_download_url"] = url_for('serve_downloaded_file', relative_file_path=result["audio_relative_path"], _external=True)
        except RuntimeError: 
             app.logger.warning("url_for executed outside of request context for /api/extract_audio. Constructing URL manually.")
             try:
                 host_url = request.host_url 
             except RuntimeError: # Still might fail if no request context at all
                 # Fallback if request.host_url is not available (e.g. if app context not fully pushed)
                 # You might need to configure this base URL if this fallback is hit often
                 host_url = "http://localhost:5001" # Default to your local dev port, adjust if deployed elsewhere
                 app.logger.warning(f"Falling back to host_url: {host_url} for audio_download_url")
             response_data["audio_download_url"] = f"{host_url.rstrip('/')}/files/{result['audio_relative_path']}"

    status_code = 200
    if response_data.get("error"):
        # Only set 500 if essential parts are missing
        if not response_data.get("audio_download_url") and not response_data.get("audio_server_path"):
             status_code = 500
        # If there's an error but some info (like server path) is there, maybe still 200 or a specific error code
        # For simplicity, let's make it 500 if any error is reported by the function
        else: 
             status_code = 500 
    return jsonify(response_data), status_code

# --- START OF NEW ROUTE TO ADD ---
@app.route('/api/get_youtube_details', methods=['GET'])
def api_get_youtube_details_route(): # Renamed function for clarity
    app.logger.info("Received request for /api/get_youtube_details")
    video_url_param = request.args.get('url')
    if not video_url_param:
        app.logger.warning("Missing 'url' parameter in /api/get_youtube_details request.")
        return jsonify({"error": "Missing 'url' parameter"}), 400
    
    # Basic check if it's a YouTube URL (can be more robust)
    # Using generic youtube.com and youtu.be check
    if not ("https://www.youtube.com/watch?v=xqcl9dAAkC0" in video_url_param.lower() or "https://www.youtube.com/watch?v=tt6_v_LoOZw" in video_url_param.lower()):
        app.logger.warning(f"Non-YouTube URL provided to /api/get_youtube_details: {video_url_param}")
        return jsonify({"error": "Invalid URL; only YouTube URLs are supported by this endpoint."}), 400
        
    result = get_youtube_details_and_transcript(video_url_param) # Call the new combined function
    
    status_code = 200 # Default to success
    if result.get("error"):
        # If there's an error but we still got a title (e.g., transcript failed but metadata worked)
        # we might still consider it a partial success for returning metadata.
        if result.get("title") and ("No subtitles available" in result.get("error", "") or "Transcript VTT file not found" in result.get("error", "")):
            status_code = 206 # Partial Content (metadata okay, transcript failed for known reason)
            app.logger.info(f"Partial success for {video_url_param}: Metadata retrieved, but transcript error: {result.get('error')}")
        # If there's an error and no title (major failure)
        elif not result.get("title"):
            status_code = 500
            app.logger.error(f"Major failure for {video_url_param}: {result.get('error')}")
        # Other errors where title might be present but it's not a subtitle issue
        else:
            status_code = 500 
            app.logger.error(f"Other error for {video_url_param}: {result.get('error')}")
            
    return jsonify(result), status_code
# --- END OF NEW ROUTE TO ADD ---

@app.route('/files/<path:relative_file_path>')
def serve_downloaded_file(relative_file_path):
    # THIS IS YOUR EXISTING FUNCTION - UNCHANGED BY ME
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
    # THIS IS YOUR EXISTING FUNCTION - UNCHANGED BY ME
    return jsonify({"status": "healthy"}), 200

# --- Main Execution ---
if __name__ == '__main__':
    # THIS IS YOUR EXISTING MAIN EXECUTION BLOCK - UNCHANGED BY ME
    app.logger.info("--- Starting Flask app locally (for development) ---")
    if not is_ffmpeg_available():
        app.logger.critical("CRITICAL: FFmpeg is not installed or not found. This API requires FFmpeg.")
    else:
        app.logger.info("FFmpeg found (local check).")
    app.logger.info(f"MP3s will be saved under: {DOWNLOADS_BASE_DIR}")
    app.logger.info(f"Temp transcripts under: {TRANSCRIPTS_TEMP_DIR}")
    app.run(host='0.0.0.0', port=5001, debug=True) # Assuming port 5001 for local dev based on your earlier mention
