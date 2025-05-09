from pytube import YouTube
from pytube.exceptions import PytubeError # For more specific pytube errors
import pytube # To print the version

def test_youtube_connection(video_url):
    """
    Tests if pytube can fetch basic metadata (like the title) for a given YouTube URL.
    """
    print(f"--- Using pytube version: {pytube.__version__} ---")
    print(f"Attempting to connect and fetch metadata for URL: {video_url}")
    
    try:
        # Initialize YouTube object.
        # use_oauth=False and allow_oauth_cache=False are good defaults for public videos.
        yt = YouTube(video_url, use_oauth=False, allow_oauth_cache=False)
        
        # If the above line didn't raise an error, pytube made a successful initial connection.
        # Now, try to access the title. This forces pytube to fetch video metadata.
        video_title = yt.title
        
        print(f"SUCCESS: Successfully fetched metadata!")
        print(f"Video Title: '{video_title}'")
        print(f"Video Author: {yt.author}")
        print("Pytube seems to be able to connect and get info for this URL.")
        return True
        
    except PytubeError as pe:
        print(f"PYTUBE ERROR: A Pytube-specific error occurred: {pe}")
    except Exception as e:
        # This will catch errors like the HTTPError 400 if it happens during metadata fetch
        print(f"UNEXPECTED ERROR: An unexpected error occurred: {e}")
        print(f"Error Type: {type(e)}")
    
    print(f"FAILURE: Could not get metadata for URL: {video_url}")
    return False

if __name__ == "__main__":
    print("--- YouTube Connection Test Script ---")
    
    # Let's use the "Big Buck Bunny" URL that was problematic before.
    # This is a standard, openly licensed video often used for testing.
    test_url_1 = "https://www.youtube.com/watch?v=AdUZArA-kZw&t=0s" 
    
    # You can also try a different, very popular, and non-region-restricted video URL.
    # For example, find a popular music video or a major news channel clip on YouTube
    # and paste its full URL here:
    # test_url_2 = "PASTE_A_DIFFERENT_YOUTUBE_URL_HERE"

    print(f"\nTesting URL 1: {test_url_1}")
    test_youtube_connection(test_url_1)
    
    # if 'test_url_2' in locals() and test_url_2 != "PASTE_A_DIFFERENT_YOUTUBE_URL_HERE":
    # print(f"\nTesting URL 2: {test_url_2}")
    # test_youtube_connection(test_url_2)

    print("\n--- Test Complete ---")
    print("If all tests show 'UNEXPECTED ERROR: ... HTTP Error 400 ...',")
    print("it's likely that pytube is currently unable to communicate effectively with YouTube,")
    print("possibly due to recent changes on YouTube's side or network/IP related issues.")
