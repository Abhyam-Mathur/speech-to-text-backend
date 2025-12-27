import os
import subprocess
import uuid
from typing import List
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
# import whisper

# ---------------- CONFIG ----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

VIDEO_DIR = os.path.join(BASE_DIR, "videos")
TEMP_DIR = os.path.join(BASE_DIR, "temp")
CLIPS_DIR = os.path.join(BASE_DIR, "clips")
AUDIO_DIR = os.path.join(BASE_DIR, "audio")

os.makedirs(VIDEO_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(CLIPS_DIR, exist_ok=True)
os.makedirs(AUDIO_DIR, exist_ok=True)

# ---------------- APP ----------------
app = FastAPI(title="Video Search & Transcription API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- LOAD MODEL ----------------
# model = whisper.load_model("small")

# ---------------- HELPERS ----------------
def extract_audio(video_path: str, audio_path: str):
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i", video_path,
            "-ac", "1",
            "-ar", "16000",
            audio_path
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

# def transcribe_audio(audio_path: str):
 #   result = model.transcribe(audio_path, word_timestamps=True)
  #  return result["segments"]

def transcribe_audio(audio_path: str):
    # Disabled on Render due to memory limits
    return []


def create_video_clip(video_path: str, start_time: float, end_time: float, output_path: str):
    """Extract a video clip from start_time to end_time"""
    duration = end_time - start_time
    try:
        # Try copy mode first (faster, but may not work for all videos)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i", video_path,
                "-ss", str(start_time),
                "-t", str(duration),
                "-c", "copy",
                output_path
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError:
        # Fallback to re-encoding if copy mode fails
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i", video_path,
                "-ss", str(start_time),
                "-t", str(duration),
                "-c:v", "libx264",
                "-c:a", "aac",
                output_path
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

def create_audio_clip(audio_path: str, start_time: float, end_time: float, output_path: str):
    """Extract an audio clip from start_time to end_time"""
    duration = end_time - start_time
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i", audio_path,
            "-ss", str(start_time),
            "-t", str(duration),
            "-ac", "1",
            "-ar", "16000",
            output_path
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

# ---------------- REQUEST MODELS ----------------
class SearchRequest(BaseModel):
    segments: List[dict]
    keyword: str
    window: int = 7

# ---------------- ROUTES ----------------

@app.get("/")
async def root():
    return {"message": "Video Transcription & Search API", "status": "running"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.post("/upload")
async def upload_video(file: UploadFile = File(...)):
    video_id = str(uuid.uuid4())
    # Preserve original file extension
    file_ext = os.path.splitext(file.filename)[1] or ".mp4"
    video_path = os.path.join(VIDEO_DIR, f"{video_id}{file_ext}")
    audio_path = os.path.join(TEMP_DIR, f"{video_id}.wav")

    try:
        with open(video_path, "wb") as f:
            f.write(await file.read())

        extract_audio(video_path, audio_path)
        segments = transcribe_audio(audio_path)

        return {
            "video_id": video_id,
            "segments": segments
        }
    except Exception as e:
        # Clean up on error
        if os.path.exists(video_path):
            os.remove(video_path)
        raise

@app.post("/youtube")
async def youtube(url: str = Form(...)):
    video_id = str(uuid.uuid4())
    # yt-dlp will add the extension, so we use a template
    video_template = os.path.join(VIDEO_DIR, f"{video_id}.%(ext)s")
    audio_path = os.path.join(TEMP_DIR, f"{video_id}.wav")

    try:
        # Windows-safe yt-dlp execution
        try:
            subprocess.run(
                ["python", "-m", "yt_dlp", "-f", "best[ext=mp4]/best", "-o", video_template, url],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except subprocess.CalledProcessError:
            # Try alternative format if mp4 fails
            subprocess.run(
                ["python", "-m", "yt_dlp", "-o", video_template, url],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

        # Find the downloaded video file (yt-dlp adds extension)
        video_files = [f for f in os.listdir(VIDEO_DIR) if f.startswith(video_id)]
        if not video_files:
            raise FileNotFoundError("Video download failed")
        
        video_path = os.path.join(VIDEO_DIR, video_files[0])

        extract_audio(video_path, audio_path)
        segments = transcribe_audio(audio_path)

        return {
            "video_id": video_id,
            "segments": segments
        }
    except Exception as e:
        # Clean up on error
        video_files = [f for f in os.listdir(VIDEO_DIR) if f.startswith(video_id)]
        for vf in video_files:
            try:
                os.remove(os.path.join(VIDEO_DIR, vf))
            except:
                pass
        error_msg = str(e)
        if "Video download failed" in error_msg:
            raise HTTPException(status_code=400, detail="Failed to download video. Please check the URL and try again.")
        elif "yt_dlp" in error_msg or "yt-dlp" in error_msg:
            raise HTTPException(status_code=500, detail="YouTube downloader error. Please ensure yt-dlp is installed: pip install yt-dlp")
        else:
            raise HTTPException(status_code=500, detail=f"Error processing YouTube video: {error_msg}")

@app.post("/search")
async def search_keyword(data: SearchRequest):
    keyword = data.keyword.lower()
    results = []

    for seg in data.segments:
        if keyword in seg["text"].lower():
            start = max(seg["start"] - data.window, 0)
            end = seg["end"] + data.window

            results.append({
                "found_at": round(seg["start"], 2),
                "clip_start": round(start, 2),
                "clip_end": round(end, 2),
                "text": seg["text"].strip()
            })

    return {
        "keyword": data.keyword,
        "matches": results
    }

class ClipRequest(BaseModel):
    video_id: str
    start_time: float
    end_time: float

@app.post("/generate-clip")
async def generate_clip(data: ClipRequest):
    """Generate video clip, audio clip, and return paths"""
    # Find the video file (might have different extension)
    video_files = [f for f in os.listdir(VIDEO_DIR) if f.startswith(data.video_id)]
    if not video_files:
        return {"error": "Video file not found"}
    
    video_path = os.path.join(VIDEO_DIR, video_files[0])
    audio_path = os.path.join(TEMP_DIR, f"{data.video_id}.wav")
    clip_id = str(uuid.uuid4())
    
    clip_video_path = os.path.join(CLIPS_DIR, f"{clip_id}.mp4")
    clip_audio_path = os.path.join(AUDIO_DIR, f"{clip_id}.wav")
    
    try:
        # Generate clips
        create_video_clip(video_path, data.start_time, data.end_time, clip_video_path)
        
        if os.path.exists(audio_path):
            create_audio_clip(audio_path, data.start_time, data.end_time, clip_audio_path)
        
        return {
            "clip_id": clip_id,
            "video_clip": f"/clips/{clip_id}.mp4",
            "audio_clip": f"/audio/{clip_id}.wav" if os.path.exists(clip_audio_path) else None
        }
    except Exception as e:
        # Clean up on error
        if os.path.exists(clip_video_path):
            try:
                os.remove(clip_video_path)
            except:
                pass
        raise

@app.get("/clips/{clip_id}.mp4")
async def get_video_clip(clip_id: str):
    """Serve video clip"""
    clip_path = os.path.join(CLIPS_DIR, f"{clip_id}.mp4")
    if os.path.exists(clip_path):
        return FileResponse(clip_path, media_type="video/mp4")
    return {"error": "Clip not found"}

@app.get("/audio/{clip_id}.wav")
async def get_audio_clip(clip_id: str):
    """Serve audio clip"""
    audio_path = os.path.join(AUDIO_DIR, f"{clip_id}.wav")
    if os.path.exists(audio_path):
        return FileResponse(audio_path, media_type="audio/wav")
    return {"error": "Audio clip not found"}
