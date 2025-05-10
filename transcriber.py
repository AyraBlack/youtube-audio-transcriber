import yt_dlp
import os
import shutil
import re  # For filename sanitization and VTT parsing
from flask import Flask, request, jsonify, send_from_directory, url_for
import logging
import uuid  # For unique temporary transcript file names
from datetime import datetime  # For timestamped filenames

# --- Flask App Setup ---
app = Flask(__name__)
app.logger.setLevel(logging.INFO)

# --- Configuration ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_BASE_DIR = os.path.join(BASE_DIR, "api_downloads")  # For MP3s that will be served
TRANSCRIPTS_TEMP_DIR = os.path.join(BASE_DIR, "api_transcripts_temp")  # For temporary VTT files

os.makedirs(DOWNLOADS_BASE_DIR, exist_ok=True)
os.makedirs(TRANSCRIPTS_TEMP_DIR, exist_ok=True)

# --- Constants ---
SOCKET_TIMEOUT_SECONDS = 180


def is_ffmpeg_available():
    return shutil.which("ffmpeg") is not None


def sanitize_filename(name_str, max_length=60):
    """Sanitizes a string to be a safe filename component."""
    s = name_str.replace(' ', '_')
    s = "".join(c if c.isalnum() or c in ('-', '_') else '_' for c in s)
    s = re.sub(r'_+', '_', s)
    return s.strip('_')[:max_length]


def extract_audio_from_video(video_url, audio_format="mp3"):
    app.logger.info(f"Audio extraction requested for URL: {video_url}")
    if not is_ffmpeg_available():
        msg = "FFmpeg not found. Required for audio conversion."
        app.logger.error(msg)
        return {"audio_server_path": None, "audio_relative_path": None, "error": msg}

    result = {"audio_server_path": None, "audio_relative_path": None, "title": None, "channel": None, "error": None}
    try:
        # Fetch metadata
        opts = {'quiet': True, 'skip_download': True, 'socket_timeout': SOCKET_TIMEOUT_SECONDS}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
        title = info.get('title')
        channel = info.get('uploader')

        # Prepare filename with timestamp
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        base = f"{ts}_{sanitize_filename(title or uuid.uuid4().hex[:6])}"
        out_dir = os.path.join(DOWNLOADS_BASE_DIR, base)
        os.makedirs(out_dir, exist_ok=True)
        tmpl = os.path.join(out_dir, f"{base}.%(ext)s")

        # Download audio
        download_opts = {
            'format': 'bestaudio/best',
            'outtmpl': tmpl,
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': audio_format}],
            'noplaylist': True,
            'quiet': True,
            'socket_timeout': SOCKET_TIMEOUT_SECONDS,
        }
        with yt_dlp.YoutubeDL(download_opts) as ydl:
            code = ydl.download([video_url])
            if code != 0:
                raise RuntimeError(f"yt-dlp exit code {code}")

        audio_file = f"{base}.{audio_format}"
        abs_path = os.path.join(out_dir, audio_file)
        if not os.path.exists(abs_path):
            raise FileNotFoundError(f"Audio file not found: {abs_path}")

        result.update({
            'audio_server_path': abs_path,
            'audio_relative_path': f"{base}/{audio_file}",
            'title': title,
            'channel': channel,
        })
    except Exception as e:
        app.logger.error(f"extract_audio error: {e}", exc_info=True)
        result['error'] = str(e)
    return result


def get_youtube_transcript_text(video_url):
    app.logger.info(f"Transcript requested for: {video_url}")
    # Only attempt subtitles for YouTube URLs
    if not ("youtube.com" in video_url or "youtu.be" in video_url):
        return {"transcript_text": None, "language_detected": None, "error": None, "title": None, "channel": None}

    result = {"transcript_text": None, "language_detected": None, "error": None}
    temp = f"vtt_{uuid.uuid4().hex}"
    try:
        # Download VTT subtitles (auto or manual)
        opts = {
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['ro','en'],
            'subtitlesformat': 'vtt',
            'skip_download': True,
            'outtmpl': os.path.join(TRANSCRIPTS_TEMP_DIR, temp),
            'noplaylist': True,
            'quiet': True,
            'socket_timeout': SOCKET_TIMEOUT_SECONDS,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(video_url, download=True)

        # Find VTT file
        found = None
        for lang in ('ro','en'):
            path = os.path.join(TRANSCRIPTS_TEMP_DIR, f"{temp}.{lang}.vtt")
            if os.path.exists(path):
                found = (path, lang)
                break
        if not found:
            app.logger.info("No subtitles found; returning empty transcript.")
            return result  # transcript_text=None, error=None

        vtt_path, lang = found
        with open(vtt_path, 'r', encoding='utf-8') as f:
            vtt = f.read()
        result['transcript_text'] = vtt_to_plaintext(vtt)
        result['language_detected'] = lang
    except Exception as e:
        app.logger.error(f"get_transcript error: {e}", exc_info=True)
        # swallow error to avoid failures
    finally:
        # cleanup
        for f in os.listdir(TRANSCRIPTS_TEMP_DIR):
            if f.startswith(temp):
                try: os.remove(os.path.join(TRANSCRIPTS_TEMP_DIR, f))
                except: pass
    return result

@app.route('/api/extract_audio', methods=['GET'])
def api_extract_audio():
    url = request.args.get('url')
    if not url:
        return jsonify({"error": "Missing 'url' parameter"}), 400
    data = extract_audio_from_video(url)
    resp = {
        'title': data.get('title'),
        'channel': data.get('channel'),
        'audio_download_url': None,
        'audio_server_path': data.get('audio_server_path'),
        'error': data.get('error'),
    }
    if data.get('audio_relative_path'):
        resp['audio_download_url'] = url_for('serve_downloaded_file', relative_file_path=data['audio_relative_path'], _external=True)
    return jsonify(resp), (500 if data.get('error') else 200)

@app.route('/api/get_youtube_transcript', methods=['GET'])
def api_get_youtube_transcript():
    url = request.args.get('url')
    if not url:
        return jsonify({"error": "Missing 'url' parameter"}), 400
    data = get_youtube_transcript_text(url)
    # add metadata if YouTube
    if data.get('transcript_text') is not None:
        try:
            info = yt_dlp.YoutubeDL({'quiet':True,'skip_download':True}).extract_info(url, download=False)
            data['title'] = info.get('title')
            data['channel'] = info.get('uploader')
        except: pass
    return jsonify(data), (500 if data.get('error') else 200)

@app.route('/files/<path:relative_file_path>')
def serve_downloaded_file(relative_file_path):
    try:
        return send_from_directory(DOWNLOADS_BASE_DIR, relative_file_path, as_attachment=True)
    except FileNotFoundError:
        return jsonify({"error": "File not found."}), 404

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy"}), 200

if __name__ == '__main__':
    if not is_ffmpeg_available():
        app.logger.critical("FFmpeg not installed.")
    app.run(host='0.0.0.0', port=5001, debug=True)
