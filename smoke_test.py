import os
from dotenv import load_dotenv
from googleapiclient.discovery import build


def main():
    # Step 1: load YT_API_KEY from .env with python-dotenv
    load_dotenv()
    api_key = os.getenv("YT_API_KEY")

    if not api_key:
        raise ValueError("YT_API_KEY not found in environment variables or .env file.")

    # Step 2: build the client
    youtube = build("youtube", "v3", developerKey=api_key)

    # Step 3: call channels().list for a sample handle (e.g., '@YouTubeCreators')
    handle = ("@mkbhd")
    request = youtube.channels().list(
        part="snippet,statistics,contentDetails",
        forHandle=handle
    )
    response = request.execute()

    # Step 4: print the channel ID and contentDetails.relatedPlaylists.uploads
    if "items" in response and len(response["items"]) > 0:
        channel = response["items"][0]
        channel_id = channel.get("id")
        uploads_playlist = channel.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")

        print(f"Successfully connected to YouTube Data API!")
        print(f"Handle: {handle}")
        print(f"Channel ID: {channel_id}")
        print(f"Uploads Playlist ID: {uploads_playlist}")
    else:
        print(f"Could not find channel for handle: {handle}")


if __name__ == "__main__":
    main()
