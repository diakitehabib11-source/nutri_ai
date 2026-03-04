

from openai import OpenAI
import sys
import time
import os
from dotenv import load_dotenv

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai = OpenAI()

def main():
    openai = OpenAI()

    video = openai.videos.create(
        model="sora-2",
        prompt="A video of a cool cat on a motorcycle in the night",
    )

    print("Video generation started:", video)

    progress = getattr(video, "progress", 0)
    bar_length = 30

    while video.status in ("in_progress", "queued"):
        # Refresh status
        video = openai.videos.retrieve(video.id)
        progress = getattr(video, "progress", 0)

        filled_length = int((progress / 100) * bar_length)
        bar = "=" * filled_length + "-" * (bar_length - filled_length)
        status_text = "Queued" if video.status == "queued" else "Processing"

        sys.stdout.write(f"\r{status_text} : [{bar}] {progress:.1f}%")
        sys.stdout.flush()
        time.sleep(2)

    # Move to next line after progress loop
    sys.stdout.write("\n")

    if video.status == "failed":
        message = getattr(
            getattr(video, "error", None), "message", "Video generation failed"
        )
        print(message)
        return

    print("Video generation completed:", video)
    print("Downloading video content...")

    content = openai.videos.download_content(video.id, variant="video")
    content.write_to_file("video.mp4")

    print("Wrote video.mp4")


if __name__ == "__main__":
    main()


'''import os
import requests
from utils.env_utils import load_env_vars, download_final_video

from utils.env_utils import require_env
env_vars = load_env_vars()
HEYGEN_API_KEY = env_vars["HEYGEN_API_KEY"]

import dotenv
dotenv.load_dotenv()

# Génération de la vidéo avec Gey Gen tout en se servant du prompte directrice 
def generate_video_with_heygen(video_prompt, output_file="final_video.mp4"):
    require_env("HEYGEN_API_KEY", HEYGEN_API_KEY)
    url = "https://api.heygen.com/v1/videos"
    headers = {
        "Authorization": f"Bearer {HEYGEN_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "script": video_prompt,
        "output_format": "mp4",
        "resolution": "1080p"
    }
    r = requests.post(url, headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()
    video_url = data.get("video_url")
    if not video_url:
        raise RuntimeError(f"Aucune URL vidéo renvoyée par HeyGen : {data}")
    print(f"[OK] Vidéo générée par HeyGen : {video_url}")   
    # Télécharger la vidéo
    download_final_video(video_url, output_file)
    return output_file'''