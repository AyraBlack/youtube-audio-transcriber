import yt_dlp
import os
import shutil
import re # For parsing VTT
from flask import Flask, request, jsonify, send_from_directory, url_for
import logging
import uuid # For unique temporary transcript file names

# --- Flask App Setup ---
app = Flask(__name__)

# --- Logging Setup (Gunicorn will manage this in production) ---
# If running locally with `python transcriber.py`, Flask's default logger is used.
# You can configure Flask's logger further if needed.
# For Gunicorn, it captures stdout/stderr, so print statements and logging module calls work.
app.logger.setLevel(logging.INFO) # Set default logging level for app.logger

# --- Configuration ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_BASE_DIR = os.path.join(BASE_DIR, "api_downloads") # For MP3s that will be served
TRANSCRIPTS_TEMP_DIR = os.path.join(BASE_DIR, "api_transcripts_temp") # For temporary VTT files

if not os.path.exists(DOWNLOADS_BASE_DIR):
    os.makedirs(DOWNLOADS_BASE_DIR)
    app.logger.info(f"Created base MP3 downloads directory: {DOWNLOADS_BASE_DIR}")
if not os.path.exists(TRANSCRIPTS_TEMP_DIR):
    os.makedirs(TRANSCRIPTS_TEMP_DIR)
    app.logger.info(f"Created temporary transcripts directory: {TRANSCRIPTS_TEMP_DIR}")

def is_ffmpeg_available():
    return shutil.which("ffmpeg") is not None

def sanitize_filename(name_str, max_length=80):
    """Sanitizes a string to be a safe filename component."""
    s = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in name_str).strip()
    return s[:max_length]

def vtt_to_plaintext(vtt_content):
    """Converts VTT subtitle content to plain text."""
    lines = vtt_content.splitlines()
    text_lines = []
    in_dialogue_block = False # To handle multi-line cues correctly

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped: # Empty line often separates blocks
            in_dialogue_block = False
            continue
        if "-->" in line_stripped: # Timestamp line
            in_dialogue_block = True # Next non-empty lines are dialogue
            continue
        if line_stripped.isdigit() and not in_dialogue_block: # Cue numbers
            continue
        
        if in_dialogue_block:
            # Remove VTT tags like <v Author>Text</v> or <i>Text</i> or &nbsp; etc.
            cleaned_line = re.sub(r'<[^>]+>', '', line_stripped)
            cleaned_line = cleaned_line.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
            if cleaned_line: # Only add if there's actual text after cleaning
                text_lines.append(cleaned_line)
                
    return "\n".join(text_lines)

def extract_audio_from_video(video_url, audio_format="mp3"):
    """
    Downloads audio from a video URL (YouTube, TikTok, Instagram) using yt-dlp.
    Returns paths for serving the audio file.
    """
    app.logger.info(f"Audio extraction requested for URL: {video_url}")
    if not is_ffmpeg_available():
        error_msg = "FFmpeg is not installed or not found. It's required for audio conversion."
        app.logger.error(error_msg)
        return {"error": error_msg, "audio_server_path": None, "audio_relative_path": None}

    result_paths = {"audio_server_path": None, "audio_relative_path": None, "error": None}

    try:
        info_opts = {'quiet': True, 'noplaylist': True, 'extract_flat': 'in_playlist'}
        with yt_dlp.YoutubeDL(info_opts) as ydl_info:
            app.logger.info(f"Fetching video metadata for audio: {video_url}...")
            info_dict = ydl_info.extract_info(video_url, download=False)
            video_title = info_dict.get('title', f'unknown_video_{uuid.uuid4().hex[:6]}')
            app.logger.info(f"Video title for audio: '{video_title}'")

        base_output_filename_safe = sanitize_filename(video_title)
        request_folder_name = base_output_filename_safe # Subfolder for this video's audio
        request_download_dir_abs = os.path.join(DOWNLOADS_BASE_DIR, request_folder_name)

        if not os.path.exists(request_download_dir_abs):
            os.makedirs(request_download_dir_abs)

        output_template_audio_abs = os.path.join(request_download_dir_abs, f'{base_output_filename_safe}.%(ext)s')

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': output_template_audio_abs,
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': audio_format}],
            'noplaylist': True, 'noprogress': True, 'verbose': False, 'logger': app.logger,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            app.logger.info(f"Starting audio download/extraction for {video_url}...")
            error_code = ydl.download([video_url])
            if error_code != 0:
                result_paths["error"] = f"yt-dlp audio process failed (code {error_code})."
                return result_paths

        result_paths["audio_server_path"] = os.path.join(request_download_dir_abs, f"{base_output_filename_safe}.{audio_format}")
        result_paths["audio_relative_path"] = os.path.join(request_folder_name, f"{base_output_filename_safe}.{audio_format}")

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
    """
    Downloads YouTube transcript (VTT), parses to plain text, and returns it.
    Deletes the temporary VTT file.
    """
    app.logger.info(f"Transcript requested for YouTube URL: {video_url}")
    if not is_ffmpeg_available(): # FFmpeg might be needed by yt-dlp for some subtitle operations
        error_msg = "FFmpeg is not installed or not found. It might be needed for some transcript operations."
        app.logger.warning(error_msg) # Non-fatal warning, yt-dlp might still work for simple VTT
        # We can proceed but log a warning.

    result_data = {"transcript_text": None, "language_detected": None, "error": None}
    
    # Unique filename for temporary VTT to avoid clashes if multiple requests happen
    temp_vtt_basename = f"transcript_{uuid.uuid4().hex}"
    temp_vtt_dir = TRANSCRIPTS_TEMP_DIR 
    # yt-dlp needs full path for outtmpl
    output_template_transcript_abs = os.path.join(temp_vtt_dir, f'{temp_vtt_basename}.%(ext)s')

    ydl_opts = {
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['en', 'ro'], # Try English first, then Romanian
        'subtitlesformat': 'vtt',
        'skip_download': True, # IMPORTANT: Only download subtitles
        'outtmpl': output_template_transcript_abs,
        'noplaylist': True, 'noprogress': True, 'verbose': False, 'logger': app.logger,
    }

    downloaded_vtt_path = None
    actual_lang_code = None

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            app.logger.info(f"Starting transcript download for {video_url}...")
            info_dict = ydl.extract_info(video_url, download=True) # Download=True for subtitles
            
            # Determine the actual downloaded VTT file path and language
            requested_subs = info_dict.get('requested_subtitles')
            if requested_subs:
                # yt-dlp provides info about the downloaded subtitle file
                # We asked for 'en' then 'ro'. It picks the first one it finds.
                for lang_code, sub_info in requested_subs.items():
                    if sub_info.get('filepath') and os.path.exists(sub_info['filepath']):
                        downloaded_vtt_path = sub_info['filepath']
                        actual_lang_code = lang_code
                        app.logger.info(f"Transcript downloaded: {downloaded_vtt_path} (Language: {actual_lang_code})")
                        break # Take the first one found
            
            if not downloaded_vtt_path:
                # Fallback: scan directory if filepath not in info_dict as expected (less reliable)
                for lang in ['en', 'ro']: # Check for expected languages
                    potential_path = os.path.join(temp_vtt_dir, f"{temp_vtt_basename}.{lang}.vtt")
                    if os.path.exists(potential_path):
                        downloaded_vtt_path = potential_path
                        actual_lang_code = lang
                        app.logger.info(f"Transcript found by scanning: {downloaded_vtt_path} (Language: {actual_lang_code})")
                        break
            
            if not downloaded_vtt_path:
                result_data["error"] = "Transcript VTT file not found after download attempt or not available in EN/RO."
                app.logger.warning(result_data["error"])
                return result_data

        with open(downloaded_vtt_path, 'r', encoding='utf-8') as f:
            vtt_content = f.read()
        
        result_data["transcript_text"] = vtt_to_plaintext(vtt_content)
        result_data["language_detected"] = actual_lang_code
        app.logger.info(f"Transcript parsed successfully for language: {actual_lang_code}")

    except Exception as e:
        app.logger.error(f"Error in get_youtube_transcript_text: {e}", exc_info=True)
        result_data["error"] = f"Unexpected error during transcript processing: {str(e)}"
    finally:
        # Clean up the temporary VTT file
        if downloaded_vtt_path and os.path.exists(downloaded_vtt_path):
            try:
                os.remove(downloaded_vtt_path)
                app.logger.info(f"Deleted temporary transcript file: {downloaded_vtt_path}")
            except Exception as e_del:
                app.logger.error(f"Error deleting temporary transcript file {downloaded_vtt_path}: {e_del}")
        # We could also clean up the temp_vtt_basename folder if it's empty or only contains this file.
        # For now, individual file deletion is fine.

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
        
    # Basic check if it's a YouTube URL (can be improved)
    if not ("youtube.com/" in video_url_param or "youtu.be/" in video_url_param):
        app.logger.warning(f"Non-YouTube URL provided for transcript: {video_url_param}")
        return jsonify({"error": "This endpoint only supports YouTube URLs for transcripts."}), 400

    result = get_youtube_transcript_text(video_url_param)
    status_code = 500 if result.get("error") else 200
    return jsonify(result), status_code

@app.route('/files/<path:relative_file_path>')
def serve_downloaded_file(relative_file_path):
    app.logger.info(f"Request to serve file: {relative_file_path} from base: {DOWNLOADS_BASE_DIR}")
    try:
        return send_from_directory(DOWNLOADS_BASE_DIR, relative_file_path, as_attachment=True)
    except FileNotFoundError:
        app.logger.error(f"File not found for serving: {relative_file_path}")
        return jsonify({"error": "File not found or path is incorrect"}), 404
    except Exception as e:
        app.logger.error(f"Error serving file {relative_file_path}: {e}")
        return jsonify({"error": "Could not serve file"}), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy"}), 200

# --- Main execution (for local testing) ---
if __name__ == '__main__':
    app.logger.info("--- Starting Flask app locally (for development) ---")
    if not is_ffmpeg_available():
        app.logger.critical("CRITICAL: FFmpeg is not installed or not found. This API requires FFmpeg.")
    else:
        app.logger.info("FFmpeg found (local check).")
    app.logger.info(f"MP3s will be saved under: {DOWNLOADS_BASE_DIR}")
    app.logger.info(f"Temp transcripts under: {TRANSCRIPTS_TEMP_DIR}")
    app.run(host='0.0.0.0', port=5001, debug=True)
