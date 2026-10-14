"""
generate_ad_video.py
====================
KantorAI Video Generator — dijalankan oleh GitHub Actions
"""

import asyncio
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from urllib.parse import urlencode

import requests

OUTPUT_DIR = Path("output_video")
OUTPUT_DIR.mkdir(exist_ok=True)
TEMP_DIR   = OUTPUT_DIR / "temp"
TEMP_DIR.mkdir(exist_ok=True)

VIDEO_W = 1080
VIDEO_H = 1920

payload_raw = os.environ.get("PAYLOAD_JSON", "")
if not payload_raw:
    print("❌ PAYLOAD_JSON env var kosong", file=sys.stderr)
    sys.exit(1)

payload = json.loads(payload_raw)

JOB_ID           = payload["job_id"]
CHAT_ID          = payload["chat_id"]
SENTENCES        = payload["sentences"]
KEYWORDS         = payload["keywords"]
HEADLINE         = payload.get("headline", "")
VOICE            = payload.get("voice", "id-ID-GadisNeural")
GAS_CALLBACK_URL = payload["gas_callback_url"]
PIXABAY_KEY      = payload.get("pixabay_key", "")
CLOUDINARY_CLOUD = payload.get("cloudinary_cloud_name", "")
CLOUDINARY_PRESET= payload.get("cloudinary_upload_preset", "")

print(f"🚀 KantorAI Video Generator — Job: {JOB_ID}")
print(f"📝 Sentences: {len(SENTENCES)}")

# ═══════════════════════════════
# STEP 1: Download gambar Pixabay
# ═══════════════════════════════
def download_pixabay_images(keywords, count_per_scene=1):
    images = []
    for i, kw in enumerate(keywords):
        scene_imgs = []
        try:
            r = requests.get(
                "https://pixabay.com/api/",
                params={
                    "key":         PIXABAY_KEY,
                    "q":           kw,
                    "image_type":  "photo",
                    "orientation": "vertical",
                    "per_page":    5,
                    "safesearch":  "true",
                    "min_width":   500,
                },
                timeout=10
            )
            r.raise_for_status()
            hits = r.json().get("hits", [])
            for hit in hits[:count_per_scene]:
                img_url  = hit.get("largeImageURL") or hit.get("webformatURL", "")
                img_id   = hit.get("id", i)
                img_path = TEMP_DIR / f"img_{i}_{img_id}.jpg"
                img_r = requests.get(img_url, timeout=15)
                img_r.raise_for_status()
                with open(img_path, "wb") as f:
                    f.write(img_r.content)
                scene_imgs.append(str(img_path))
                print(f"  ✅ Scene {i+1} gambar: {img_path.name}")
                break
        except Exception as e:
            print(f"  ⚠️ Pixabay scene {i+1} error: {e} — pakai warna solid")
        images.append(scene_imgs)
    return images

print("📸 Downloading gambar dari Pixabay...")
scene_images = download_pixabay_images(KEYWORDS)

# ═══════════════════════════════
# STEP 2: Generate TTS voiceover
# ═══════════════════════════════
async def generate_all_tts(sentences, voice):
    from edge_tts import Communicate
    audio_files = []
    for i, text in enumerate(sentences):
        path = TEMP_DIR / f"speech_{i}.mp3"
        for attempt in range(3):
            try:
                comm = Communicate(text, voice)
                await comm.save(str(path))
                audio_files.append(str(path))
                print(f"  ✅ TTS scene {i+1}: {path.name}")
                break
            except Exception as e:
                print(f"  ⚠️ TTS attempt {attempt+1} scene {i+1}: {e}")
                if attempt == 2:
                    audio_files.append(None)
                await asyncio.sleep(2 ** attempt)
    return audio_files

print("🎤 Generating voiceover dengan Edge-TTS...")
audio_files = asyncio.run(generate_all_tts(SENTENCES, VOICE))

# ═══════════════════════════════
# STEP 3: Assemble video MoviePy
# ═══════════════════════════════
def assemble_video(sentences, scene_images, audio_files):
    from moviepy import (
        ImageClip, AudioFileClip, CompositeVideoClip,
        concatenate_videoclips, TextClip
    )
    from PIL import Image
    import numpy as np

    clips = []

    for i, (sentence, imgs, audio_path) in enumerate(zip(sentences, scene_images, audio_files)):
        if not audio_path or not os.path.exists(audio_path):
            print(f"  ⚠️ Skip scene {i+1} — audio tidak ada")
            continue

        audio_clip = AudioFileClip(audio_path)
        duration = audio_clip.duration + 0.3

        if imgs and os.path.exists(imgs[0]):
            img = Image.open(imgs[0]).convert("RGB")
            target_ratio = VIDEO_W / VIDEO_H
            img_ratio = img.width / img.height
            if img_ratio > target_ratio:
                new_w = int(img.height * target_ratio)
                left = (img.width - new_w) // 2
                img = img.crop((left, 0, left + new_w, img.height))
            else:
                new_h = int(img.width / target_ratio)
                top = (img.height - new_h) // 2
                img = img.crop((0, top, img.width, top + new_h))
            img = img.resize((VIDEO_W, VIDEO_H), Image.LANCZOS)
            frame_path = TEMP_DIR / f"frame_{i}.jpg"
            img.save(str(frame_path), quality=90)
            bg_clip = ImageClip(str(frame_path)).with_duration(duration)
            def make_zoom(t):
                scale = 1.0 + 0.05 * (t / duration)
                return scale
            bg_clip = bg_clip.resized(make_zoom).with_position("center")
        else:
            frame_arr = np.zeros((VIDEO_H, VIDEO_W, 3), dtype=np.uint8)
            for y in range(VIDEO_H):
                r = int(15 + (y / VIDEO_H) * 30)
                g = int(20 + (y / VIDEO_H) * 40)
                b = int(80 + (y / VIDEO_H) * 60)
                frame_arr[y, :] = [r, g, b]
            frame_path = TEMP_DIR / f"frame_{i}.jpg"
            Image.fromarray(frame_arr).save(str(frame_path))
            bg_clip = ImageClip(str(frame_path)).with_duration(duration)

        overlay_arr = __import__('numpy').zeros((300, VIDEO_W, 4), dtype=__import__('numpy').uint8)
        overlay_arr[:, :, 3] = 160
        overlay_path = TEMP_DIR / f"overlay_{i}.png"
        Image.fromarray(overlay_arr, 'RGBA').save(str(overlay_path))
        overlay_clip = (ImageClip(str(overlay_path))
                       .with_duration(duration)
                       .with_position(("center", VIDEO_H - 320)))

        try:
            words = sentence.split()
            lines = []
            current_line = []
            for word in words:
                current_line.append(word)
                if len(" ".join(current_line)) > 28:
                    lines.append(" ".join(current_line[:-1]))
                    current_line = [word]
            if current_line:
                lines.append(" ".join(current_line))
            wrapped = "\n".join(lines[:3])
            txt_clip = (TextClip(
                text=wrapped, font_size=52, color="white",
                stroke_color="black", stroke_width=2,
                size=(VIDEO_W - 80, None), method="caption", text_align="center"
            ).with_duration(duration).with_position(("center", VIDEO_H - 310)))
        except Exception as e:
            print(f"  ⚠️ TextClip error: {e}")
            txt_clip = None

        headline_clip = None
        if i == 0 and HEADLINE:
            try:
                headline_clip = (TextClip(
                    text=HEADLINE, font_size=44, color="white",
                    stroke_color="#000000", stroke_width=2,
                    size=(VIDEO_W - 100, None), method="caption", text_align="center"
                ).with_duration(duration).with_position(("center", 120)))
            except Exception as e:
                print(f"  ⚠️ Headline clip error: {e}")

        layers = [bg_clip, overlay_clip]
        if txt_clip:
            layers.append(txt_clip)
        if headline_clip:
            layers.append(headline_clip)

        scene = CompositeVideoClip(layers, size=(VIDEO_W, VIDEO_H))
        scene = scene.with_audio(audio_clip)
        clips.append(scene)
        print(f"  ✅ Scene {i+1} selesai ({duration:.1f}s)")

    if not clips:
        raise RuntimeError("Tidak ada scene yang berhasil!")

    print(f"🔗 Menggabungkan {len(clips)} scene...")
    final = concatenate_videoclips(clips, method="compose")
    output_path = OUTPUT_DIR / "iklan_video.mp4"
    print(f"💾 Menyimpan video ke {output_path}...")
    final.write_videofile(str(output_path), fps=24, codec="libx264",
                          audio_codec="aac", preset="fast", threads=2, logger=None)
    return str(output_path)

print("🎬 Assembling video...")
try:
    video_path = assemble_video(SENTENCES, scene_images, audio_files)
    print(f"✅ Video selesai: {video_path}")
    print(f"📦 Ukuran: {os.path.getsize(video_path)/1024/1024:.1f} MB")
except Exception as e:
    print(f"❌ Error assembling video: {e}")
    traceback.print_exc()
    sys.exit(1)

# ═══════════════════════════════
# STEP 4: Upload ke Cloudinary
# ═══════════════════════════════
def upload_cloudinary(video_path):
    print("☁️ Uploading ke Cloudinary...")
    url = f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD}/video/upload"
    with open(video_path, "rb") as f:
        response = requests.post(url, data={
            "upload_preset": CLOUDINARY_PRESET,
            "public_id": f"kantorAI_{JOB_ID}",
            "folder": "kantorAI_videos"
        }, files={"file": f}, timeout=120)
    response.raise_for_status()
    result = response.json()
    video_url = result.get("secure_url", "")
    if not video_url:
        raise RuntimeError(f"Cloudinary tidak mengembalikan URL: {result}")
    print(f"✅ Video URL: {video_url}")
    return video_url

if not CLOUDINARY_CLOUD or not CLOUDINARY_PRESET:
    print("⚠️ Cloudinary tidak dikonfigurasi")
    VIDEO_URL = f"ARTIFACT:{video_path}"
else:
    try:
        VIDEO_URL = upload_cloudinary(video_path)
    except Exception as e:
        print(f"❌ Cloudinary error: {e}")
        traceback.print_exc()
        VIDEO_URL = ""

# ═══════════════════════════════
# STEP 5: Callback ke GAS
# ═══════════════════════════════
def callback_gas(video_url):
    print(f"📡 Callback ke GAS...")
    payload_cb = {
        "action": "video_selesai", "job_id": JOB_ID,
        "chat_id": CHAT_ID, "video_url": video_url, "headline": HEADLINE
    }
    try:
        r = requests.post(GAS_CALLBACK_URL, json=payload_cb,
                          headers={"Content-Type": "application/json"}, timeout=30)
        r.raise_for_status()
        print(f"✅ Callback sukses: {r.status_code}")
    except Exception as e:
        print(f"⚠️ Callback gagal: {e} — fallback GET")
        try:
            qs = urlencode(payload_cb)
            r2 = requests.get(f"{GAS_CALLBACK_URL}?{qs}", timeout=30)
            print(f"   Fallback GET: {r2.status_code}")
        except Exception as e2:
            print(f"❌ Callback gagal total: {e2}")

callback_gas(VIDEO_URL or "ERROR:upload_gagal")

print("\n✅ KantorAI Video Generator selesai!")
print(f"   Job ID:    {JOB_ID}")
print(f"   Video URL: {VIDEO_URL}")
