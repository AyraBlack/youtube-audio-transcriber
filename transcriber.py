print("--- DEBUG: transcriber.py execution - TOP ---") # First debug line

import yt_dlp
import os
import shutil
from flask import Flask, request, jsonify
import logging # Keep the import, just comment out basicConfig

print("--- DEBUG: transcriber.py - Imports complete ---")

# --- Flask App Setup ---
print("--- DEBUG: transcriber.py - About to define Flask app ---")
app = Flask(__name__)
print(f"--- DEBUG: transcriber.py - Flask app defined: {app} ---") # Crucial debug line

# --- Logging Setup ---
# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s') # THIS LINE IS COMMENTED OUT
print("--- DEBUG: transcriber.py - Logging basicConfig SKIPPED ---")

# --- Configuration ---
DOWNLOADS_BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_downloads")
if not os.path.exists(DOWNLOADS_BASE_DIR):
    os.makedirs(DOWNLOADS_BASE_DIR)
    # Use standard print for now if basicConfig is out, or app.logger once app is running
    print(f"INFO: Created base downloads directory: {DOWNLOADS_BASE_DIR}") 
print("--- DEBUG: transcriber.py - DOWNLOADS_BASE_DIR configured ---")


def is_ffmpeg_available():
    """Checks if ffmpeg is installed and accessible."""
    return shutil.which("ffmpeg") is not None

def process_video_with_ytdlp(video_url, download_transcript=False, audio_format="mp3"):
    """
    Downloads audio and optionally transcripts from a video URL using yt-dlp.
    """
    # Use app.logger if app context is available, otherwise standard logging/print
    # For now, using standard logging which will be configured by Gunicorn or Flask's default
    current_logger = logging.getLogger(__name__) # Get a logger instance

    if not is_ffmpeg_available() and (audio_format != 'best' or download_transcript):
        error_msg = "FFmpeg is not installed or not found. It's required for audio conversion and transcript processing."
        current_logger.error(error_msg)
        return {"error": error_msg, "audio_path": None, "transcript_path": None}

    downloaded_files = {"audio_path": None, "transcript_path": None, "error": None}

    try:
        info_opts = {
            'quiet': True,
            'noplaylist': True,
            'extract_flat': 'in_playlist',
        }
        with yt_dlp.YoutubeDL(info_opts) as ydl_info:
            current_logger.info(f"Fetching video metadata for URL: {video_url}...")
            try:
                info_dict = ydl_info.extract_info(video_url, download=False)
                if '_type' in info_dict and info_dict['_type'] == 'playlist':
                    video_title = info_dict.get('entries', [{}])[0].get('title', 'unknown_video_in_playlist')
                else:
                    video_title = info_dict.get('title', 'unknown_video')
                current_logger.info(f"Video title: '{video_title}'")
            except yt_dlp.utils.DownloadError as e:
                current_logger.error(f"Failed to extract video info: {e}")
                downloaded_files["error"] = f"Failed to extract video info: {str(e)}"
                return downloaded_files

        base_output_filename = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in video_title).rstrip()
        base_output_filename = base_output_filename[:80]
        
        request_download_dir = os.path.join(DOWNLOADS_BASE_DIR, base_output_filename)
        if not os.path.exists(request_download_dir):
            os.makedirs(request_download_dir)
            current_logger.info(f"Created request-specific download directory: {request_download_dir}")

        output_template_audio = os.path.join(request_download_dir, f'{base_output_filename}.%(ext)s')
        output_template_transcript = os.path.join(request_download_dir, f'{base_output_filename}.%(ext)s')

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': output_template_audio,
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': audio_format}],
            'noplaylist': True,
            'quiet': False, # Let yt-dlp print its progress
            'noprogress': False, # Show progress
            'verbose': False, # Keep this false unless deep debugging yt-dlp
            # Using 'logger': current_logger can direct yt-dlp logs to Flask/Gunicorn logger
            'logger': current_logger,
        }

        if download_transcript:
            ydl_opts.update({
                'writesubtitles': True,
                'writeautomaticsub': True,
                'subtitleslangs': ['en', 'ro', 'all'], # Prioritize English, Romanian
                'subtitlesformat': 'vtt', # vtt is good for text, srt is also common
                'outtmpl': output_template_transcript, # yt-dlp handles naming for subs
            })
        
        current_logger.info(f"Preparing to download. Options: {ydl_opts}")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            current_logger.info(f"Starting download process for {video_url}...")
            error_code = ydl.download([video_url])
            if error_code != 0:
                error_msg = f"yt-dlp download process failed with error code {error_code}."
                current_logger.error(error_msg)
                downloaded_files["error"] = error_msg
                return downloaded_files

        downloaded_files["audio_path"] = os.path.join(request_download_dir, f"{base_output_filename}.{audio_format}")
        
        if download_transcript:
            transcript_file_found = None
            # Scan for the transcript file, yt-dlp might add language code
            for f_name in os.listdir(request_download_dir):
                if f_name.startswith(base_output_filename) and f_name.endswith((".vtt", ".srt")): # Check for common subtitle extensions
                    transcript_file_found = os.path.join(request_download_dir, f_name)
                    break # Take the first one found
            if transcript_file_found and os.path.exists(transcript_file_found):
                downloaded_files["transcript_path"] = transcript_file_found
                current_logger.info(f"Transcript found: {transcript_file_found}")
            else:
                current_logger.warning(f"Transcript download was requested, but no VTT/SRT file found starting with '{base_output_filename}' in {request_download_dir}.")
                downloaded_files["transcript_path"] = "Transcript not found or not available."

        if not os.path.exists(downloaded_files["audio_path"]):
            current_logger.error(f"Audio file not found at expected path: {downloaded_files['audio_path']}")
            downloaded_files["error"] = "Audio file processing failed or file not found."
            downloaded_files["audio_path"] = None

        current_logger.info(f"Process complete. Results: {downloaded_files}")
        return downloaded_files

    except yt_dlp.utils.DownloadError as de:
        current_logger.error(f"yt-dlp DownloadError: {de}")
        downloaded_files["error"] = f"yt-dlp DownloadError: {str(de)}"
        return downloaded_files
    except Exception as e:
        current_logger.error(f"Unexpected general error in process_video_with_ytdlp: {e}", exc_info=True)
        downloaded_files["error"] = f"Unexpected error: {str(e)}"
        return downloaded_files

# --- API Endpoint ---
@app.route('/api/process_video', methods=['GET'])
def api_process_video():
    # Use app.logger here as we are in a Flask context
    app.logger.info("Received request for /api/process_video") 
    video_url = request.args.get('url')
    get_transcript_str = request.args.get('get_transcript', 'false').lower()

    if not video_url:
        app.logger.warning("Missing 'url' parameter in request.")
        return jsonify({"error": "Missing 'url' parameter"}), 400

    download_transcript_bool = get_transcript_str == 'true'
    
    app.logger.info(f"Processing URL: {video_url}, Download transcript: {download_transcript_bool}")
    
    result = process_video_with_ytdlp(video_url, download_transcript=download_transcript_bool)

    if result.get("error"):
        return jsonify(result), 500
    else:
        return jsonify(result), 200

# --- Main execution (for local testing, Gunicorn won't run this block) ---
if __name__ == '__main__':
    print("--- DEBUG: transcriber.py - In __main__ block (for local run) ---") 
    # When running with Gunicorn, Gunicorn handles logging setup.
    # For local `python transcriber.py` execution, we might want basicConfig.
    # However, since Gunicorn is the target, let's keep it commented out
    # to ensure consistency with the production environment's loading.
    # If you need to test logging locally, you can temporarily uncomment it.
    # logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')


    if not is_ffmpeg_available():
        # Use app.logger if available, otherwise print or standard logging
        print("CRITICAL: FFmpeg is not installed or not found. This API requires FFmpeg for its core functionality.")
        print("Please install FFmpeg and ensure it's in your system's PATH.")
    else:
        print("FFmpeg found (local check).")

    print(f"Downloads (local run) will be saved in subdirectories under: {DOWNLOADS_BASE_DIR}")
    # The app.run() below is only for local development and won't be used by Gunicorn.
    # Gunicorn uses the 'app' instance directly.
    app.run(host='0.0.0.0', port=5001, debug=True) # debug=True provides more Flask output

print("--- DEBUG: transcriber.py execution - BOTTOM ---") # Last debug line
