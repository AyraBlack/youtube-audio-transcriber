print("--- DEBUG: transcriber.py execution - TOP ---")

import yt_dlp
import os
import shutil
from flask import Flask, request, jsonify, send_from_directory, url_for # Added send_from_directory, url_for
import logging

print("--- DEBUG: transcriber.py - Imports complete ---")

# --- Flask App Setup ---
print("--- DEBUG: transcriber.py - About to define Flask app ---")
app = Flask(__name__)
print(f"--- DEBUG: transcriber.py - Flask app defined: {app} ---")

# --- Logging Setup ---
# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s') # Commented out for Gunicorn
print("--- DEBUG: transcriber.py - Logging basicConfig SKIPPED ---")

# --- Configuration ---
# DOWNLOADS_BASE_DIR is the absolute path on the server
DOWNLOADS_BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_downloads")
if not os.path.exists(DOWNLOADS_BASE_DIR):
    os.makedirs(DOWNLOADS_BASE_DIR)
    print(f"INFO: Created base downloads directory: {DOWNLOADS_BASE_DIR}")
print("--- DEBUG: transcriber.py - DOWNLOADS_BASE_DIR configured ---")


def is_ffmpeg_available():
    return shutil.which("ffmpeg") is not None

def process_video_with_ytdlp(video_url, download_transcript=False, audio_format="mp3"):
    current_logger = logging.getLogger(__name__)

    if not is_ffmpeg_available() and (audio_format != 'best' or download_transcript):
        error_msg = "FFmpeg is not installed or not found. It's required for audio conversion and transcript processing."
        current_logger.error(error_msg)
        return {"error": error_msg, "audio_server_path": None, "transcript_server_path": None, "audio_relative_path": None, "transcript_relative_path": None}

    # Initialize paths as None
    downloaded_files = {
        "audio_server_path": None, 
        "transcript_server_path": None, 
        "audio_relative_path": None, # Path relative to DOWNLOADS_BASE_DIR
        "transcript_relative_path": None, # Path relative to DOWNLOADS_BASE_DIR
        "error": None
    }

    try:
        info_opts = {'quiet': True, 'noplaylist': True, 'extract_flat': 'in_playlist'}
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

        base_output_filename_safe = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in video_title).rstrip()
        base_output_filename_safe = base_output_filename_safe[:80]
        
        # This is the folder name relative to DOWNLOADS_BASE_DIR
        request_folder_name = base_output_filename_safe 
        request_download_dir_abs = os.path.join(DOWNLOADS_BASE_DIR, request_folder_name)

        if not os.path.exists(request_download_dir_abs):
            os.makedirs(request_download_dir_abs)
            current_logger.info(f"Created request-specific download directory: {request_download_dir_abs}")

        # yt-dlp needs absolute path for outtmpl
        output_template_audio_abs = os.path.join(request_download_dir_abs, f'{base_output_filename_safe}.%(ext)s')
        output_template_transcript_abs = os.path.join(request_download_dir_abs, f'{base_output_filename_safe}.%(ext)s')

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': output_template_audio_abs,
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': audio_format}],
            'noplaylist': True, 'quiet': False, 'noprogress': False, 'verbose': False, 'logger': current_logger,
        }

        if download_transcript:
            ydl_opts.update({
                'writesubtitles': True, 'writeautomaticsub': True,
                'subtitleslangs': ['en', 'ro', 'all'], 'subtitlesformat': 'vtt',
                'outtmpl': output_template_transcript_abs, # yt-dlp uses this for subs too
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

        # Store absolute server path
        downloaded_files["audio_server_path"] = os.path.join(request_download_dir_abs, f"{base_output_filename_safe}.{audio_format}")
        # Store relative path for URL generation
        downloaded_files["audio_relative_path"] = os.path.join(request_folder_name, f"{base_output_filename_safe}.{audio_format}")
        
        if download_transcript:
            transcript_file_found_abs = None
            transcript_file_found_relative = None
            for f_name in os.listdir(request_download_dir_abs):
                if f_name.startswith(base_output_filename_safe) and f_name.endswith((".vtt", ".srt")):
                    transcript_file_found_abs = os.path.join(request_download_dir_abs, f_name)
                    transcript_file_found_relative = os.path.join(request_folder_name, f_name)
                    break
            if transcript_file_found_abs and os.path.exists(transcript_file_found_abs):
                downloaded_files["transcript_server_path"] = transcript_file_found_abs
                downloaded_files["transcript_relative_path"] = transcript_file_found_relative
                current_logger.info(f"Transcript found: {transcript_file_found_abs}")
            else:
                current_logger.warning(f"Transcript requested, but no VTT/SRT file found in {request_download_dir_abs}.")
                # No error, just transcript not found
        
        if not os.path.exists(downloaded_files["audio_server_path"]):
            current_logger.error(f"Audio file not found at expected path: {downloaded_files['audio_server_path']}")
            downloaded_files["error"] = "Audio file processing failed or file not found post-download."
            downloaded_files["audio_server_path"] = None
            downloaded_files["audio_relative_path"] = None


        current_logger.info(f"Process complete. Results (server paths): {downloaded_files}")
        return downloaded_files

    except yt_dlp.utils.DownloadError as de:
        current_logger.error(f"yt-dlp DownloadError: {de}")
        downloaded_files["error"] = f"yt-dlp DownloadError: {str(de)}"
        return downloaded_files
    except Exception as e:
        current_logger.error(f"Unexpected general error in process_video_with_ytdlp: {e}", exc_info=True)
        downloaded_files["error"] = f"Unexpected error: {str(e)}"
        return downloaded_files

# --- New Route to Serve Downloaded Files ---
@app.route('/files/<path:relative_file_path>')
def serve_downloaded_file(relative_file_path):
    app.logger.info(f"Request to serve file: {relative_file_path} from base: {DOWNLOADS_BASE_DIR}")
    # relative_file_path will be like "Video_Title_Safe_Folder/Video_Title_Safe_File.mp3"
    try:
        return send_from_directory(DOWNLOADS_BASE_DIR, relative_file_path, as_attachment=True)
    except FileNotFoundError:
        app.logger.error(f"File not found for serving: {relative_file_path}")
        return jsonify({"error": "File not found or path is incorrect"}), 404
    except Exception as e:
        app.logger.error(f"Error serving file {relative_file_path}: {e}")
        return jsonify({"error": "Could not serve file"}), 500


# --- API Endpoint ---
@app.route('/api/process_video', methods=['GET'])
def api_process_video():
    app.logger.info("Received request for /api/process_video") 
    video_url_param = request.args.get('url') # Renamed to avoid clash with flask.url_for
    get_transcript_str = request.args.get('get_transcript', 'false').lower()

    if not video_url_param:
        app.logger.warning("Missing 'url' parameter in request.")
        return jsonify({"error": "Missing 'url' parameter"}), 400

    download_transcript_bool = get_transcript_str == 'true'
    
    app.logger.info(f"Processing URL: {video_url_param}, Download transcript: {download_transcript_bool}")
    
    result = process_video_with_ytdlp(video_url_param, download_transcript=download_transcript_bool)

    response_data = {
        "audio_download_url": None,
        "transcript_download_url": None,
        "audio_server_path": result.get("audio_server_path"), # Keep server path for debugging/internal use
        "transcript_server_path": result.get("transcript_server_path"),
        "error": result.get("error")
    }

    if result.get("audio_relative_path"):
        # _external=True generates an absolute URL like https://your-app.up.railway.app/...
        response_data["audio_download_url"] = url_for('serve_downloaded_file', relative_file_path=result["audio_relative_path"], _external=True)
    
    if result.get("transcript_relative_path"):
        response_data["transcript_download_url"] = url_for('serve_downloaded_file', relative_file_path=result["transcript_relative_path"], _external=True)

    if response_data.get("error"):
        return jsonify(response_data), 500
    else:
        return jsonify(response_data), 200

# --- Main execution (for local testing) ---
if __name__ == '__main__':
    print("--- DEBUG: transcriber.py - In __main__ block (for local run) ---") 
    if not is_ffmpeg_available():
        print("CRITICAL: FFmpeg is not installed or not found.")
    else:
        print("FFmpeg found (local check).")
    print(f"Downloads (local run) will be saved in subdirectories under: {DOWNLOADS_BASE_DIR}")
    app.run(host='0.0.0.0', port=5001, debug=True)

print("--- DEBUG: transcriber.py execution - BOTTOM ---")
