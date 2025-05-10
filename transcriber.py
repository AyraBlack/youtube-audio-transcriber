import yt_dlp
import os
import shutil
import re # Pentru parsarea VTT
from flask import Flask, request, jsonify, send_from_directory, url_for, Response # Am adăugat Response
import logging
import uuid # Pentru nume de fișiere temporare unice
from datetime import datetime

# --- Configurare Aplicație Flask ---
app = Flask(__name__)
app.logger.setLevel(logging.INFO) # Setăm nivelul de logging pentru logger-ul aplicației

# --- Configurare Directoare ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_BASE_DIR = os.path.join(BASE_DIR, "api_downloads") # Pentru MP3-uri
TRANSCRIPTS_TEMP_DIR = os.path.join(BASE_DIR, "api_transcripts_temp") # Pentru fișiere VTT temporare

if not os.path.exists(DOWNLOADS_BASE_DIR):
    os.makedirs(DOWNLOADS_BASE_DIR)
    app.logger.info(f"Directorul de bază pentru descărcări MP3 creat: {DOWNLOADS_BASE_DIR}")
if not os.path.exists(TRANSCRIPTS_TEMP_DIR):
    os.makedirs(TRANSCRIPTS_TEMP_DIR)
    app.logger.info(f"Directorul temporar pentru transcrieri creat: {TRANSCRIPTS_TEMP_DIR}")

# --- Constante ---
SOCKET_TIMEOUT_SECONDS = 180
COMMON_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

# --- Citire Proxy din Variabila de Mediu ---
PROXY_URL_FROM_ENV = os.environ.get('PROXY_URL')
if PROXY_URL_FROM_ENV:
    app.logger.info(f"Se utilizează proxy din variabila de mediu: {PROXY_URL_FROM_ENV.split('@')[1] if '@' in PROXY_URL_FROM_ENV else 'Proxy configurat (detalii ascunse)'}")
else:
    app.logger.info("Variabila de mediu PROXY_URL nu este setată. Se operează fără proxy.")

def is_ffmpeg_available():
    """Verifică dacă FFmpeg este instalat și accesibil."""
    return shutil.which("ffmpeg") is not None

def sanitize_filename(name_str, max_length=60):
    """Curăță un string pentru a fi un nume de fișier sigur."""
    s = name_str.replace(' ', '_')
    s = "".join(c if c.isalnum() or c in ('-', '_') else '_' for c in s)
    s = re.sub(r'_+', '_', s) # Elimină underscore-urile consecutive
    s = s.strip('_') # Elimină underscore-urile de la început/sfârșit
    return s[:max_length]

def vtt_to_plaintext(vtt_content):
    """Convertește conținutul VTT în text simplu."""
    lines = vtt_content.splitlines()
    text_lines = []
    in_dialogue_block = False
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            in_dialogue_block = False
            continue
        if "-->" in line_stripped: # Linie de timestamp
            in_dialogue_block = True
            continue
        if line_stripped.isdigit() and not in_dialogue_block: # Numerele replicilor
            continue
        if in_dialogue_block:
            cleaned_line = re.sub(r'<[^>]+>', '', line_stripped) # Elimină tag-urile VTT
            cleaned_line = cleaned_line.replace(' ', ' ').replace('&', '&').replace('<', '<').replace('>', '>')
            if cleaned_line:
                text_lines.append(cleaned_line)
    return "\n".join(text_lines)

def _get_common_ydl_opts():
    """Funcție ajutătoare pentru opțiunile comune yt-dlp, inclusiv proxy."""
    opts = {
        'socket_timeout': SOCKET_TIMEOUT_SECONDS,
        'http_headers': {'User-Agent': COMMON_USER_AGENT},
        'logger': app.logger, # Direcționează log-urile yt-dlp către logger-ul Flask/Gunicorn
        'noplaylist': True,
        'noprogress': True, # Bun pentru log-urile API
        'verbose': False,
    }
    if PROXY_URL_FROM_ENV:
        opts['proxy'] = PROXY_URL_FROM_ENV
    return opts

def extract_audio_from_video(video_url, audio_format="mp3"):
    """Descarcă audio dintr-un URL video."""
    app.logger.info(f"Cerere de extragere audio pentru URL: {video_url}")
    if not is_ffmpeg_available():
        error_msg = "FFmpeg nu este instalat sau nu a fost găsit. Este necesar pentru conversia audio."
        app.logger.error(error_msg)
        return {"error": error_msg, "audio_server_path": None, "audio_relative_path": None}

    result_paths = {"audio_server_path": None, "audio_relative_path": None, "error": None}
    try:
        common_opts = _get_common_ydl_opts()
        info_opts = {**common_opts, 'quiet': True, 'extract_flat': 'in_playlist'}
        with yt_dlp.YoutubeDL(info_opts) as ydl_info:
            app.logger.info(f"Se preiau metadatele video pentru audio: {video_url}...")
            info_dict = ydl_info.extract_info(video_url, download=False)
            video_title = info_dict.get('title', f'unknown_video_{uuid.uuid4().hex[:6]}')
            app.logger.info(f"Titlul video pentru audio: '{video_title}'")

        current_time_str = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        sanitized_title_part = sanitize_filename(video_title)
        base_output_filename_safe = f"{current_time_str}_{sanitized_title_part}"
        app.logger.info(f"Nume de fișier de bază curățat și cu timestamp: '{base_output_filename_safe}'")
        
        request_folder_name = base_output_filename_safe
        request_download_dir_abs = os.path.join(DOWNLOADS_BASE_DIR, request_folder_name)
        if not os.path.exists(request_download_dir_abs):
            os.makedirs(request_download_dir_abs)
        
        actual_disk_filename_template = f'{base_output_filename_safe}.%(ext)s'
        output_template_audio_abs = os.path.join(request_download_dir_abs, actual_disk_filename_template)

        ydl_opts_download = {
            **common_opts,
            'format': 'bestaudio/best',
            'outtmpl': output_template_audio_abs,
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': audio_format}],
            'quiet': False, # Permite yt-dlp să afișeze progresul descărcării
            'noprogress': False, # Asigură afișarea progresului
        }
        with yt_dlp.YoutubeDL(ydl_opts_download) as ydl:
            app.logger.info(f"Se începe descărcarea/extragerea audio pentru {video_url}...")
            error_code = ydl.download([video_url])
            if error_code != 0:
                result_paths["error"] = f"Procesul audio yt-dlp a eșuat (cod {error_code})."
                return result_paths
        
        final_audio_filename_on_disk = f"{base_output_filename_safe}.{audio_format}"
        result_paths["audio_server_path"] = os.path.join(request_download_dir_abs, final_audio_filename_on_disk)
        result_paths["audio_relative_path"] = os.path.join(request_folder_name, final_audio_filename_on_disk)
        
        if not os.path.exists(result_paths["audio_server_path"]):
            result_paths["error"] = "Fișierul audio nu a fost găsit după procesare."
            result_paths["audio_server_path"] = None
            result_paths["audio_relative_path"] = None
        else:
            app.logger.info(f"Audio extras: {result_paths['audio_server_path']}")
        return result_paths
    except Exception as e:
        app.logger.error(f"Eroare în extract_audio_from_video: {e}", exc_info=True)
        result_paths["error"] = f"Eroare neașteptată în timpul extragerii audio: {str(e)}"
        return result_paths

def get_youtube_transcript_text(video_url):
    """Descarcă transcrierea YouTube (VTT), o parsează în text simplu și o returnează."""
    app.logger.info(f"Cerere de transcriere pentru URL YouTube: {video_url}")
    result_data = {"transcript_text": None, "language_detected": None, "error": None}
    
    temp_vtt_basename = f"transcript_{uuid.uuid4().hex}"
    temp_vtt_dir = TRANSCRIPTS_TEMP_DIR
    output_template_transcript_abs = os.path.join(temp_vtt_dir, temp_vtt_basename) 

    common_opts = _get_common_ydl_opts()
    ydl_opts = {
        **common_opts,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['ro', 'en'], # Încearcă Română întâi, apoi Engleză
        'subtitlesformat': 'vtt',
        'skip_download': True,      
        'outtmpl': output_template_transcript_abs, 
        'quiet': False, 
        'noprogress': False,
    }

    downloaded_vtt_path = None
    actual_lang_code = None

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            app.logger.info(f"Se începe descărcarea directă a transcrierii pentru {video_url} (limbi: ro, en) cu timeout {SOCKET_TIMEOUT_SECONDS}s...")
            info_dict = ydl.extract_info(video_url, download=True) 
            
            requested_subs = info_dict.get('requested_subtitles')
            if requested_subs:
                for lang_code in ['ro', 'en']: 
                    if lang_code in requested_subs:
                        sub_info = requested_subs[lang_code]
                        if sub_info.get('filepath') and os.path.exists(sub_info['filepath']):
                            downloaded_vtt_path = sub_info['filepath']
                            actual_lang_code = lang_code
                            app.logger.info(f"Transcriere descărcată: {downloaded_vtt_path} (Limba: {actual_lang_code})")
                            break 
            
            if not downloaded_vtt_path:
                app.logger.info("Calea transcrierii nu este în 'requested_subtitles', se scanează directorul...")
                for lang in ['ro', 'en']:
                    potential_path = os.path.join(temp_vtt_dir, f"{temp_vtt_basename}.{lang}.vtt")
                    if os.path.exists(potential_path):
                        downloaded_vtt_path = potential_path
                        actual_lang_code = lang
                        app.logger.info(f"Transcriere găsită prin scanare: {downloaded_vtt_path} (Limba: {actual_lang_code})")
                        break
            
            if not downloaded_vtt_path:
                result_data["error"] = "Fișierul VTT al transcrierii nu a fost găsit după încercarea de descărcare sau nu este disponibil în RO/EN."
                app.logger.warning(result_data["error"])
                return result_data

        with open(downloaded_vtt_path, 'r', encoding='utf-8') as f:
            vtt_content = f.read()
        
        result_data["transcript_text"] = vtt_to_plaintext(vtt_content)
        result_data["language_detected"] = actual_lang_code
        app.logger.info(f"Transcriere parsată cu succes pentru limba: {actual_lang_code}")

    except yt_dlp.utils.DownloadError as de_yt:
        app.logger.error(f"yt-dlp DownloadError în timpul procesării transcrierii pentru {video_url}: {de_yt}")
        result_data["error"] = f"yt-dlp DownloadError: {str(de_yt)}"
    except Exception as e:
        app.logger.error(f"Eroare în get_youtube_transcript_text pentru {video_url}: {e}", exc_info=True)
        result_data["error"] = f"Eroare neașteptată în timpul procesării transcrierii: {str(e)}"
    finally:
        if downloaded_vtt_path and os.path.exists(downloaded_vtt_path):
            try:
                os.remove(downloaded_vtt_path)
                app.logger.info(f"Fișierul temporar de transcriere șters: {downloaded_vtt_path}")
            except Exception as e_del:
                app.logger.error(f"Eroare la ștergerea fișierului temporar de transcriere {downloaded_vtt_path}: {e_del}")
    return result_data

# --- Endpoint-uri API ---
@app.route('/api/extract_audio', methods=['GET'])
def api_extract_audio():
    app.logger.info("Cerere primită pentru /api/extract_audio")
    video_url_param = request.args.get('url')
    if not video_url_param:
        app.logger.warning("Parametrul 'url' lipsește din cererea /api/extract_audio.")
        return jsonify({"error": "Parametrul 'url' lipsește"}), 400
    
    result = extract_audio_from_video(video_url_param)
    response_data = {
        "audio_download_url": None,
        "audio_server_path": result.get("audio_server_path"), # Pentru debugging intern
        "error": result.get("error")
    }
    if result.get("audio_relative_path"):
        response_data["audio_download_url"] = url_for('serve_downloaded_file', relative_file_path=result["audio_relative_path"], _external=True)
    
    status_code = 500 if response_data.get("error") else 200
    return jsonify(response_data), status_code

@app.route('/api/get_youtube_transcript', methods=['GET'])
def api_get_youtube_transcript():
    app.logger.info("Cerere primită pentru /api/get_youtube_transcript")
    video_url_param = request.args.get('url')
    if not video_url_param:
        app.logger.warning("Parametrul 'url' lipsește din cererea /api/get_youtube_transcript.")
        return jsonify({"error": "Parametrul 'url' lipsește"}), 400
    
    # Verificare simplă dacă este un URL YouTube (poate fi îmbunătățită)
    # Am eliminat verificarea strictă pentru a permite yt-dlp să încerce orice URL valid pe care îl suportă pentru subtitrări
    # if not ("youtube.com/" in video_url_param or "youtu.be/" in video_url_param):
    #     app.logger.warning(f"URL non-YouTube furnizat pentru transcriere: {video_url_param}")
    #     return jsonify({"error": "Acest endpoint suportă în principal URL-uri YouTube pentru transcrieri."}), 400

    result = get_youtube_transcript_text(video_url_param)

    # --- MODIFICARE PENTRU A RETURNA TEXT SIMPLU ---
    if result.get("error"):
        # Erorile sunt încă returnate ca JSON pentru a putea fi parsat mesajul de eroare
        return jsonify({"error": result["error"], "language_detected": None, "transcript_text": None}), 500
    elif result.get("transcript_text") is not None:
        # Returnează textul simplu direct, cu Content-Type text/plain
        return Response(result["transcript_text"], mimetype='text/plain; charset=utf-8')
    else:
        # Caz neașteptat, ar trebui să existe fie text, fie eroare
        app.logger.error("Rezultat neașteptat de la get_youtube_transcript_text: nici text, nici eroare.")
        return jsonify({"error": "Eroare internă neașteptată la procesarea transcrierii."}), 500
    # --- SFÂRȘITUL MODIFICĂRII ---

@app.route('/files/<path:relative_file_path>')
def serve_downloaded_file(relative_file_path):
    app.logger.info(f"Cerere de servire fișier. Director de bază: '{DOWNLOADS_BASE_DIR}', Cale relativă din URL: '{relative_file_path}'")
    try:
        return send_from_directory(DOWNLOADS_BASE_DIR, relative_file_path, as_attachment=True)
    except FileNotFoundError:
        app.logger.error(f"FileNotFoundError: Fișier negăsit pentru servire. Cale verificată: '{os.path.join(DOWNLOADS_BASE_DIR, relative_file_path)}'")
        return jsonify({"error": "Fișier negăsit. Este posibil să fi fost mutat, șters sau calea este incorectă după procesare."}), 404
    except Exception as e:
        app.logger.error(f"Eroare la servirea fișierului '{relative_file_path}': {type(e).__name__} - {str(e)}", exc_info=True)
        return jsonify({"error": "Nu s-a putut servi fișierul din cauza unei probleme interne."}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Endpoint simplu pentru verificarea stării de sănătate a API-ului."""
    return jsonify({"status": "healthy"}), 200

# --- Execuție principală (pentru testare locală) ---
if __name__ == '__main__':
    app.logger.info("--- Pornire aplicație Flask local (pentru dezvoltare) ---")
    if PROXY_URL_FROM_ENV:
        app.logger.info(f"Rularea locală ar utiliza proxy: {PROXY_URL_FROM_ENV.split('@')[1] if '@' in PROXY_URL_FROM_ENV else 'Proxy configurat'}")
    if not is_ffmpeg_available():
        app.logger.critical("CRITIC: FFmpeg nu este instalat sau nu a fost găsit. Acest API necesită FFmpeg.")
    else:
        app.logger.info("FFmpeg găsit (verificare locală).")
    app.logger.info(f"MP3-urile vor fi salvate sub: {DOWNLOADS_BASE_DIR}")
    app.logger.info(f"Transcrierile temporare sub: {TRANSCRIPTS_TEMP_DIR}")
    app.run(host='0.0.0.0', port=5001, debug=True) # debug=True oferă mai mult output Flask
