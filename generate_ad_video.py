"""
generate_ad_video.py
Kantor AI — Video Iklan Otomatis untuk Ebook 109 Jalan Rezeki Guru
Dipanggil oleh GitHub Actions via repository_dispatch dari Google Apps Script
"""

import os
import sys
import json
import requests
import textwrap
import asyncio
import subprocess
from pathlib import Path

# ── 1. Baca payload dari environment variable ──────────────────────────────
payload_raw = os.environ.get("PAYLOAD_JSON", "{}")
payload     = json.loads(payload_raw)

JOB_ID        = payload.get("job_id",            "job_unknown")
SCRIPT_TEXT   = payload.get("script",            "Ebook 109 Jalan Rezeki Guru. Usaha sampingan untuk guru tanpa modal besar.")
TITLE         = payload.get("title",             "109 Jalan Rezeki Guru")
KEYWORDS      = payload.get("keywords",          "guru,usaha sampingan")
CALLBACK_URL  = payload.get("callback_url",      "")
CLOUD_NAME    = payload.get("cloudinary_cloud",  "xw5wrdgm")
CLOUD_PRESET  = payload.get("cloudinary_preset", "kantorAI_preset")
PIXABAY_KEY   = payload.get("pixabay_key",       "")
CHAT_ID       = payload.get("chat_id",           "")
TELEGRAM_TOKEN = payload.get("telegram_token",   "")

print(f"Job ID  : {JOB_ID}")
print(f"Title   : {TITLE}")
print(f"Callback: {CALLBACK_URL}")

# ── 2. Import library video ────────────────────────────────────────────────
try:
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np
    from moviepy.editor import (
        VideoFileClip, ImageClip, AudioFileClip,
        CompositeVideoClip, concatenate_videoclips
    )
    print("Library video berhasil diimport")
except ImportError as e:
    print(f"ERROR import library: {e}")
    sys.exit(1)

# ── 3. Konstanta video ─────────────────────────────────────────────────────
WIDTH, HEIGHT = 1080, 1920
FPS           = 30

OUTPUT_DIR  = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)
AUDIO_PATH  = OUTPUT_DIR / "voiceover.mp3"
VIDEO_PATH  = OUTPUT_DIR / "iklan_guru.mp4"
BG_PATH     = OUTPUT_DIR / "background.jpg"

# ── 4. Download gambar latar dari Pixabay ─────────────────────────────────
def download_background():
    print("Mengunduh gambar latar dari Pixabay...")
    keyword = KEYWORDS.split(",")[0] if KEYWORDS else "education"
    queries = [keyword, "teacher classroom", "education school", "learning"]
    for q in queries:
        try:
            url = (
                "https://pixabay.com/api/"
                "?key=" + PIXABAY_KEY +
                "&q=" + requests.utils.quote(q) +
                "&image_type=photo&orientation=vertical"
                "&min_width=1080&min_height=1920&per_page=5&safesearch=true"
            )
            r = json.loads(requests.get(url, timeout=15).text)
            hits = r.get("hits", [])
            if hits:
                img_url = hits[0].get("largeImageURL", "")
                if img_url:
                    img_data = requests.get(img_url, timeout=30).content
                    with open(BG_PATH, "wb") as f:
                        f.write(img_data)
                    print(f"Gambar latar diunduh: {q}")
                    return True
        except Exception as ex:
            print(f"Pixabay query '{q}' gagal: {ex}")
    print("Membuat background gradien sebagai fallback...")
    return create_gradient_background()

def create_gradient_background():
    img = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        r = int(20  + (y / HEIGHT) * 10)
        g = int(80  + (y / HEIGHT) * 40)
        b = int(120 + (y / HEIGHT) * 60)
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))
    img.save(BG_PATH)
    return True

# ── 5. Buat voiceover dengan edge-tts ─────────────────────────────────────
async def buat_voiceover():
    print("Membuat voiceover dengan edge-tts...")
    try:
        import edge_tts
        communicate = edge_tts.Communicate(SCRIPT_TEXT, voice="id-ID-GadisNeural")
        await communicate.save(str(AUDIO_PATH))
        print(f"Voiceover selesai: {AUDIO_PATH}")
        return True
    except Exception as e:
        print(f"edge-tts error: {e}")
        return False

# ── 6. Render frame teks di atas background ────────────────────────────────
def buat_frame_teks(bg_image_path, teks_list):
    try:
        bg = Image.open(bg_image_path).convert("RGBA").resize((WIDTH, HEIGHT))
    except Exception:
        bg = Image.new("RGBA", (WIDTH, HEIGHT), (20, 80, 120, 255))
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 140))
    frame   = Image.alpha_composite(bg, overlay)
    draw    = ImageDraw.Draw(frame)
    try:
        font_judul = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 60)
        font_isi   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 44)
        font_kecil = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
    except Exception:
        font_judul = ImageFont.load_default()
        font_isi   = font_judul
        font_kecil = font_judul
    y_pos = 200
    for baris in textwrap.wrap(TITLE, width=22):
        w = draw.textlength(baris, font=font_judul) if hasattr(draw, "textlength") else 500
        x = (WIDTH - w) // 2
        draw.text((x+3, y_pos+3), baris, font=font_judul, fill=(0,0,0,200))
        draw.text((x, y_pos),     baris, font=font_judul, fill=(255,220,50,255))
        y_pos += 75
    y_pos += 40
    for teks in teks_list:
        for baris in textwrap.wrap(teks, width=28):
            w = draw.textlength(baris, font=font_isi) if hasattr(draw, "textlength") else 400
            x = (WIDTH - w) // 2
            draw.text((x+2, y_pos+2), baris, font=font_isi, fill=(0,0,0,180))
            draw.text((x, y_pos),     baris, font=font_isi, fill=(255,255,255,255))
            y_pos += 55
        y_pos += 20
    cta   = "Klik link di bawah untuk info lengkap"
    y_cta = HEIGHT - 200
    w_cta = draw.textlength(cta, font=font_kecil) if hasattr(draw, "textlength") else 400
    draw.rectangle(
        [(WIDTH//2 - w_cta//2 - 20, y_cta - 15),
         (WIDTH//2 + w_cta//2 + 20, y_cta + 50)],
        fill=(255, 80, 0, 220)
    )
    draw.text(((WIDTH - w_cta)//2, y_cta), cta, font=font_kecil, fill=(255,255,255,255))
    frame_path = OUTPUT_DIR / "frame.png"
    frame.convert("RGB").save(frame_path)
    return frame_path

# ── 7. Render video akhir ──────────────────────────────────────────────────
def render_video(frame_path):
    print("Merender video final...")
    audio_clip = AudioFileClip(str(AUDIO_PATH))
    audio_dur  = audio_clip.duration
    print(f"Durasi audio: {audio_dur:.1f} detik")
    image_clip = ImageClip(str(frame_path)).set_duration(audio_dur).set_fps(FPS)
    video = image_clip.set_audio(audio_clip)
    video.write_videofile(
        str(VIDEO_PATH), fps=FPS,
        codec="libx264", audio_codec="aac",
        temp_audiofile="temp_audio.m4a", remove_temp=True, logger=None
    )
    print(f"Video selesai: {VIDEO_PATH}")
    return VIDEO_PATH

# ── 8. Upload ke Cloudinary ────────────────────────────────────────────────
def upload_cloudinary(video_path):
    print("Mengupload video ke Cloudinary...")
    upload_url = "https://api.cloudinary.com/v1_1/" + CLOUD_NAME + "/video/upload"
    with open(video_path, "rb") as f:
        resp = requests.post(
            upload_url,
            data={"upload_preset": CLOUD_PRESET, "public_id": JOB_ID, "folder": "kantor_ai"},
            files={"file": f}, timeout=120
        )
    result  = resp.json()
    sec_url = result.get("secure_url", "")
    if sec_url:
        print(f"Upload berhasil: {sec_url}")
        return sec_url
    else:
        print(f"Upload gagal: {result}")
        return ""

# ── 9. Kirim callback ke GAS ───────────────────────────────────────────────
def kirim_callback(video_url, status="success", error_msg=""):
    if not CALLBACK_URL:
        print("Tidak ada callback_url, skip.")
        return
    print(f"Mengirim callback ke GAS: {CALLBACK_URL}")
    try:
        r = requests.post(CALLBACK_URL, json={
            "action": "video_ready", "job_id": JOB_ID,
            "video_url": video_url,  "status": status,
            "error": error_msg,      "title": TITLE
        }, timeout=30)
        print(f"Callback response: {r.status_code}")
    except Exception as ex:
        print(f"Callback error: {ex}")

# ── 10. MAIN ───────────────────────────────────────────────────────────────
def main():
    print("=" * 50)
    print("KANTOR AI — Generate Video Dimulai")
    print("=" * 50)

    download_background()

    loop  = asyncio.get_event_loop()
    vo_ok = loop.run_until_complete(buat_voiceover())
    if not vo_ok:
        kirim_callback("", status="error", error_msg="Voiceover gagal")
        sys.exit(1)

    teks_list  = SCRIPT_TEXT.split(". ")[:4]
    frame_path = buat_frame_teks(BG_PATH, teks_list)

    try:
        video_path = render_video(frame_path)
    except Exception as e:
        print(f"Render error: {e}")
        kirim_callback("", status="error", error_msg=str(e))
        sys.exit(1)

    video_url = upload_cloudinary(video_path)
    if not video_url:
        kirim_callback("", status="error", error_msg="Upload Cloudinary gagal")
        sys.exit(1)

    kirim_callback(video_url, status="success")

    print("=" * 50)
    print("SELESAI! Video URL:", video_url)
    print("=" * 50)

if __name__ == "__main__":
    main()
