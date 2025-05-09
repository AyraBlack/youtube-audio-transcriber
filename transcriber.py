from pytube import YouTube
import os # For joining paths and checking if file exists

def download_youtube_audio(video_url, output_path=".", file_name=None):
    """
    Downloads the audio from a YouTube video.

    Args:
        video_url (str): The URL of the YouTube video.
        output_path (str): The directory where the audio will be saved. Defaults to current directory.
        file_name (str, optional): The desired file name (without extension). 
                                   If None, uses video title.

    Returns:
        str: The full path to the downloaded audio file, or None if download failed.
    """
    try:
        print(f"Connecting to YouTube with URL: {video_url}...")
        yt = YouTube(video_url)
        print(f"Successfully connected. Video title: {yt.title}")

        # Filter for audio-only streams and get the first one
        # Pytube often provides these as .mp4 files (which are M4A audio) or .webm (Opus audio)
        stream = yt.streams.filter(only_audio=True).first()

        if not stream:
            print("No suitable audio stream found.")
            return None

        print(f"Found audio stream: {stream}")

        # Determine filename to pass to pytube's download method
        # Pytube will add its own extension, usually .mp4 for these audio streams.
        filename_for_pytube = None
        if file_name:
            # If a filename is provided, use its base (without extension)
            # as pytube's download() function expects the name part only.
            filename_for_pytube, _ = os.path.splitext(file_name)
        
        print(f"Starting download of '{yt.title}'...")
        # The `filename` parameter in download() specifies the name *before* the extension.
        downloaded_file_path = stream.download(output_path=output_path, filename=filename_for_pytube)
        
        print(f"Download complete! Audio saved to: {downloaded_file_path}")
        return downloaded_file_path

    except Exception as e:
        print(f"An error occurred: {e}")
        return None

if __name__ == "__main__":
    # Example usage:
    # --- THIS IS THE CORRECTED LINE ---
    sample_video_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ" # Rick Astley - Never Gonna Give You Up
    # --- END OF CORRECTION ---
    
    print("--- YouTube Audio Downloader ---")
    # You can uncomment the next line to ask for user input
    # video_to_download = input("Enter the YouTube video URL: ") 
    video_to_download = sample_video_url # For testing, using a fixed URL

    save_path = "downloaded_audio" # Audio will be saved in a folder named 'downloaded_audio'
    
    # Create the output directory if it doesn't exist
    if not os.path.exists(save_path):
        os.makedirs(save_path)
        print(f"Created directory: {save_path}")

    # Call the download function
    # You can also pass a custom_filename like this:
    # custom_filename_example = "my_rick_roll_audio" 
    # downloaded_file = download_youtube_audio(video_to_download, output_path=save_path, file_name=custom_filename_example)
    downloaded_file = download_youtube_audio(video_to_download, output_path=save_path)

    if downloaded_file:
        print(f"\nSuccessfully downloaded audio to: {downloaded_file}")
    else:
        print("\nFailed to download audio.")
