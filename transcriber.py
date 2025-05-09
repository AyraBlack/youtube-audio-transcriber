from pytube import YouTube, exceptions as pytube_exceptions # Import specific exceptions
import os
import pytube # To print version

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
        print(f"--- Using pytube version: {pytube.__version__} ---") # Print version
        print(f"Attempting to connect to YouTube with URL: {video_url}...")
        
        # Initialize YouTube object
        yt = YouTube(video_url)
        
        print(f"Successfully connected. Video title: '{yt.title}'")
        print(f"Video author: {yt.author}")
        print(f"Video length: {yt.length} seconds")
        # print(f"Available streams: {yt.streams}") # Potentially very long output

        # Filter for audio-only streams and get the first one
        stream = yt.streams.filter(only_audio=True).first()

        if not stream:
            print("No suitable audio-only stream found.")
            return None

        print(f"Found audio stream: {stream}")

        filename_for_pytube = None
        if file_name:
            filename_for_pytube, _ = os.path.splitext(file_name)
        
        print(f"Starting download of audio for '{yt.title}'...")
        downloaded_file_path = stream.download(output_path=output_path, filename=filename_for_pytube)
        
        print(f"Download complete! Audio saved to: {downloaded_file_path}")
        return downloaded_file_path

    except pytube_exceptions.VideoUnavailable:
        print(f"Pytube Error: The video {video_url} is unavailable.")
        return None
    except pytube_exceptions.RegexMatchError:
        print(f"Pytube Error: Could not find a match for video ID in URL: {video_url}. Is the URL correct?")
        return None
    except pytube_exceptions.PytubeError as pe: # Catch other Pytube specific errors
        print(f"A Pytube specific error occurred: {pe}")
        return None
    except Exception as e: # Catch any other general errors
        print(f"An unexpected general error occurred: {e}")
        print(f"Error type: {type(e)}")
        return None

if __name__ == "__main__":
    # Let's try a different standard test video URL: "Big Buck Bunny"
    # This is a very common, openly licensed video used for testing.
    sample_video_url = "https://www.youtube.com/watch?v=AdUZArA-kZw&t=0s" 
    
    print("--- YouTube Audio Downloader (Debug Mode) ---")
    
    video_to_download = sample_video_url
    # You can also uncomment the line below to ask for user input for the URL
    # video_to_download = input("Enter the YouTube video URL: ")

    save_path = "downloaded_audio"
    
    if not os.path.exists(save_path):
        os.makedirs(save_path)
        print(f"Created directory: {save_path}")

    downloaded_file = download_youtube_audio(video_to_download, output_path=save_path)

    if downloaded_file:
        print(f"\nSuccessfully downloaded audio to: {downloaded_file}")
    else:
        print(f"\nFailed to download audio for URL: {video_to_download}")

