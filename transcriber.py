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

if not os.path.exists(DOWNLOADS_BASE_DIR):
    os.makedirs(DOWNLOADS_BASE_DIR)
    app.logger.info(f"Created base MP3 downloads directory: {DOWNLOADS_BASE_DIR}")
if not os.path.exists(TRANSCRIPTS_TEMP_DIR):
    os.makedirs(TRANSCRIPTS_TEMP_DIR)
    app.logger.info(f"Created temporary transcripts directory: {TRANSCRIPTS_TEMP_DIR}")

# --- Constants ---
SOCKET_TIMEOUT_SECONDS = 180


def is_ffmpeg_available():
    return shutil.which("ffmpeg") is not None


def sanitize_filename(name_str, max_length=60):
    """Sanitizes a string to be a safe filename component."""
    s = name_str.replace(' ', '_')
    s = "".join(c if c.isalnum() or c in ('-', '_') else '_' for c in s)
    s = re.sub(r'_+', '_', s)
    s = s.strip('_')
    return s[:max_length]


def vtt_to_plaintext(vtt_content):
    lines = vtt_content.splitlines()
    text_lines = []
    in_dialogue = False
    for line in lines:
        l = line.strip()
        if not l:
            in_dialogue = False
            continue
        if "-->" in l:
            in_dialogue = True
            continue
        if l.isdigit() and not in_dialogue:
            continue
        if in_dialogue:
            # Remove HTML tags
            clean = re.sub(r'<[^>]+>', '', l)
            if clean:
                text_lines.append(clean)
    return "\n".join(text_lines)


def extract_audio_from_video(video_url, audio_format="mp3"):
    app.logger.info(f"Audio extraction requested for URL: {video_url}")
    if not is_ffmpeg_available():
        err = "FFmpeg not found. Required for audio conversion."
        app.logger.error(err)
        return {"audio_server_path": None, "audio_relative_path": None, "error": err}

    result = {"audio_server_path": None, "audio_relative_path": None, "error": None}
    try:
        # Fetch metadata (title & channel)
        meta_opts = {'quiet': True, 'skip_download': True, 'socket_timeout': SOCKET_TIMEOUT_SECONDS}
        with yt_dlp.YoutubeDL(meta_opts) as ydl_meta:
            info = ydl_meta.extract_info(video_url, download=False)
        video_title = info.get('title', f'video_{uuid.uuid4().hex[:6]}')
        channel_name = info.get('uploader', '')
        app.logger.info(f"Metadata - title: '{video_title}', channel: '{channel_name}'")

        # Timestamped filename
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        base_name = f"{timestamp}_{sanitize_filename(video_title)}"

        out_dir = os.path.join(DOWNLOADS_BASE_DIR, base_name)
        os.makedirs(out_dir, exist_ok=True)

        tmpl = os.path.join(out_dir, f"{base_name}.%(ext)s")
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': tmpl,
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': audio_format}],
            'noplaylist': True,
            'noprogress': True,
            'quiet': True,
            'socket_timeout': SOCKET_TIMEOUT_SECONDS,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            app.logger.info(f"Downloading audio to template: {tmpl}")
            code = ydl.download([video_url])
            if code != 0:
                raise RuntimeError(f"yt-dlp returned non-zero exit code {code}")

        filename = f"{base_name}.{audio_format}"
        abs_path = os.path.join(out_dir, filename)
        if not os.path.exists(abs_path):
            raise FileNotFoundError(f"Expected output not found: {abs_path}")

        result.update({
            "audio_server_path": abs_path,
            "audio_relative_path": f"{base_name}/{filename}",
            "title": video_title,
            "channel": channel_name,
        })
        app.logger.info(f"Audio ready at: {abs_path}")
    except Exception as e:
        app.logger.error(f"extract_audio error: {e}", exc_info=True)
        result['error'] = str(e)
    return result


def get_youtube_transcript_text(video_url):
    app.logger.info(f"Transcript requested for: {video_url}")
    result = {"transcript_text": None, "language_detected": None, "error": None}
    temp_base = f"transcript_{uuid.uuid4().hex}"
    try:
        # Download subtitles
        opts = {
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['ro','en'],
            'subtitlesformat': 'vtt',
            'skip_download': True,
            'outtmpl': os.path.join(TRANSCRIPTS_TEMP_DIR, temp_base),
            'noplaylist': True,
            'quiet': True,
            'socket_timeout': SOCKET_TIMEOUT_SECONDS,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            subs = info.get('requested_subtitles') or {}
            # Locate downloaded file
            found = None
            for lang in ('ro','en'):
                path = os.path.join(TRANSCRIPTS_TEMP_DIR, f"{temp_base}.{lang}.vtt")
                if os.path.exists(path):
                    found = (path, lang)
                    break
            if not found:
                raise FileNotFoundError("Subtitle VTT not found.")
            vtt_path, lang = found
        # Parse VTT -> plain text
        with open(vtt_path, 'r', encoding='utf-8') as f:
            vtt = f.read()
        result['transcript_text'] = vtt_to_plaintext(vtt)
        result['language_detected'] = lang
    except Exception as e:
        app.logger.error(f"get_transcript error: {e}", exc_info=True)
        result['error'] = str(e)
    finally:
        # Clean up
        for file in os.listdir(TRANSCRIPTS_TEMP_DIR):
            if file.startswith(temp_base):
                try:
                    os.remove(os.path.join(TRANSCRIPTS_TEMP_DIR, file))
                except:
                    pass
    return result

@app.route('/api/extract_audio', methods=['GET'])
def api_extract_audio():
    video_url = request.args.get('url')
    if not video_url:
        return jsonify({"error": "Missing 'url' parameter"}), 400
    data = extract_audio_from_video(video_url)
    response = {
        "title": data.get('title'),
        "channel": data.get('channel'),
        "audio_download_url": None,
        "audio_server_path": data.get('audio_server_path'),
        "error": data.get('error'),
    }
    if data.get('audio_relative_path'):
        response['audio_download_url'] = url_for('serve_downloaded_file', relative_file_path=data['audio_relative_path'], _external=True)
    status = 500 if data.get('error') else 200
    return jsonify(response), status

@app.route('/api/get_youtube_transcript', methods=['GET'])
def api_get_youtube_transcript():
    video_url = request.args.get('url')
    if not video_url:
        return jsonify({"error": "Missing 'url' parameter"}), 400
    transcript_data = get_youtube_transcript_text(video_url)
    # Also extract metadata
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'skip_download': True, 'socket_timeout': SOCKET_TIMEOUT_SECONDS}) as ydl:
            info = ydl.extract_info(video_url, download=False)
        transcript_data['title'] = info.get('title')
        transcript_data['channel'] = info.get('uploader')
    except Exception:
        transcript_data['title'] = None
        transcript_data['channel'] = None
    status = 500 if transcript_data.get('error') else 200
    return jsonify(transcript_data), status

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
