import yt_dlp # Import the yt-dlp library
import os
import shutil # For checking if ffmpeg is available

def is_ffmpeg_available():
    """Checks if ffmpeg is installed and accessible."""
    return shutil.which("ffmpeg") is not None

def download_youtube_audio_ytdlp(video_url, output_path=".", file_name=None, audio_format="mp3"):
    """
    Downloads the audio from a YouTube video using yt-dlp.

    Args:
        video_url (str): The URL of the YouTube video.
        output_path (str): The directory where the audio will be saved.
        file_name (str, optional): The desired file name (without extension). 
                                   If None, uses a sanitized video title.
        audio_format (str): The desired audio format (e.g., "mp3", "wav", "m4a").

    Returns:
        str: The full path to the downloaded audio file, or None if download failed.
    """
    if not is_ffmpeg_available():
        print("--------------------------------------------------------------------")
        print("ERROR: FFmpeg is not installed or not found in your system's PATH.")
        print("yt-dlp requires FFmpeg to extract and convert audio.")
        print("Please install FFmpeg. On macOS, you can use Homebrew: 'brew install ffmpeg'")
        print("--------------------------------------------------------------------")
        return None

    try:
        # --- 1. Get video info (like title) first to determine filename if not provided ---
        info_opts = {
            'quiet': True,
            'noplaylist': True,
        }
        with yt_dlp.YoutubeDL(info_opts) as ydl_info:
            print(f"Fetching video metadata for URL: {video_url}...")
            info_dict = ydl_info.extract_info(video_url, download=False)
            video_title = info_dict.get('title', 'unknown_video')
            print(f"Video title: '{video_title}'")

        # --- 2. Determine the base output filename (without extension) ---
        if file_name:
            base_output_filename = os.path.splitext(file_name)[0] # Use user-provided name, remove extension if any
        else:
            # Use a sanitized version of the video title
            base_output_filename = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in video_title).rstrip()
            base_output_filename = base_output_filename[:100] # Limit length for sanity

        # --- 3. Configure yt-dlp options for audio download and conversion ---
        # The output template will now use our determined base_output_filename
        output_template = os.path.join(output_path, f'{base_output_filename}.%(ext)s')
        
        ydl_opts = {
            'format': 'bestaudio/best',  # Choose best audio quality
            'outtmpl': output_template,   # Output template using our base name
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': audio_format, # e.g., 'mp3', 'wav'
                # 'preferredquality': '192', # For mp3, quality in kbit/s. Optional.
            }],
            'noplaylist': True,         # Only download single video if playlist URL is given
            'quiet': False,             # Show yt-dlp's own progress messages
            'progress_hooks': [lambda d: print(d.get('_percent_str', ''), d.get('_speed_str', ''), d.get('_eta_str', ''), end='\r') if d['status'] == 'downloading' else None],
        }

        print(f"Preparing to download audio as '{base_output_filename}.{audio_format}' to '{output_path}'...")

        # --- 4. Perform the download ---
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            error_code = ydl.download([video_url]) # Pass URL as a list

            if error_code != 0:
                print(f"\nAn error occurred during download (yt-dlp error code: {error_code}).")
                return None

        # --- 5. Determine the final downloaded file path ---
        # yt-dlp will have created the file with the correct extension (e.g., .mp3)
        final_downloaded_path = os.path.join(output_path, f"{base_output_filename}.{audio_format}")

        if os.path.exists(final_downloaded_path):
            print(f"\nDownload and conversion complete! Audio saved to: {final_downloaded_path}")
            return final_downloaded_path
        else:
            print(f"\nDownload seemed to complete, but the expected file was not found: {final_downloaded_path}")
            print("This might be due to an issue with FFmpeg or yt-dlp's post-processing.")
            return None

    except yt_dlp.utils.DownloadError as de:
        print(f"\nyt-dlp DownloadError: {de}")
        return None
    except Exception as e:
        print(f"\nAn unexpected general error occurred: {e}")
        print(f"Error type: {type(e)}")
        return None

if __name__ == "__main__":
    print("--- YouTube Audio Downloader (using yt-dlp) ---")

    # Ensure FFmpeg is installed before proceeding
    if not is_ffmpeg_available():
        print("\nPlease install FFmpeg and try again.")
    else:
        print("FFmpeg found. Proceeding with download attempt...")
        # A known working, non-region-restricted video URL
        sample_video_url = "https://www.youtube.com/watch?v=AdUZArA-kZw&t=0s" # Big Buck Bunny
        # You can also use the Rick Astley video if you prefer:
        # sample_video_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

        # video_to_download = input("Enter the YouTube video URL: ")
        video_to_download = sample_video_url 

        save_path = "downloaded_audio_ytdlp" # New folder to avoid confusion
        desired_audio_format = "mp3" # Can be "wav", "m4a", etc.
        # custom_filename_base = "my_custom_audio_name" # Optional: if you want a specific name

        if not os.path.exists(save_path):
            os.makedirs(save_path)
            print(f"Created directory: {save_path}")

        print(f"\nAttempting to download: {video_to_download}")
        # downloaded_file = download_youtube_audio_ytdlp(video_to_download, output_path=save_path, file_name=custom_filename_base, audio_format=desired_audio_format)
        downloaded_file = download_youtube_audio_ytdlp(video_to_download, output_path=save_path, audio_format=desired_audio_format)


        if downloaded_file:
            print(f"\nSUCCESS: Audio downloaded to: {downloaded_file}")
        else:
            print(f"\nFAILURE: Could not download audio for URL: {video_to_download}")
