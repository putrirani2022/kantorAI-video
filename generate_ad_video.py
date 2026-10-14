"""
generate_ad_video.py  (v4)
Kantor AI - Video iklan otomatis untuk ebook 109 Jalan Rezeki Guru.
Dipanggil GitHub Actions (repository_dispatch) dari Google Apps Script.
Tanpa moviepy: video dirakit dengan ffmpeg dari paket imageio-ffmpeg.
"""

import os
import sys
import json
import asyncio
import subprocess
import textwrap
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

# ---------- 1. Baca payload ----------
payload = json.loads(os.environ.get("PAYLOAD_JSON", "{}") or "{}")

JOB_ID       = payload.get("job_id", "job_unknown")
SCRIPT_TEXT  = payload.get("script", "Ebook 109 Jalan Rezeki Guru. Usaha sampingan untuk guru tanpa modal besar.")
TITLE        = payload.get("title", "109 Jalan Rezeki Guru")
KEYWORDS     = payload.get("keywords", "guru,usaha sampingan")
CALLBACK_URL = payload.get("callback_url", "")
CLOUD_NAME   = payload.get("cloudinary_cloud", "xw5wrdgm")
CLOUD_PRESET = payload.get("cloudinary_preset", "kantorAI_preset")
PIXABAY_KEY  = payload.get("pixabay_key", "")

print("Job ID  : " + str(JOB_ID))
print("Title   : " + str(TITLE))
print("Callback: " + str(CALLBACK_URL))

WIDTH, HEIGHT = 1080, 1920
OUT = Path("output")
OUT.mkdir(exist_ok=True)
AUDIO_PATH = OUT / "voiceover.mp3"
VIDEO_PATH = OUT / "iklan_guru.mp4"
BG_PATH = OUT / "background.jpg"
FRAME_PATH = OUT / "frame.png"


# ---------- 2. Callback ke Apps Script ----------
def kirim_callback(video_url, status="success", error_msg=""):
    if not CALLBACK_URL:
        print("callback_url kosong, skip callback")
        return
    body = {
        "action": "video_ready",
        "job_id": JOB_ID,
        "video_url": video_url,
        "status": status,
        "error": error_msg,
        "title": TITLE,
    }
    try:
        r = requests.post(CALLBACK_URL, json=body, timeout=60)
        print("Callback status: " + str(r.status_code))
    except Exception as ex:
        print("Callback error: " + str(ex))


def gagal(pesan):
    print("GAGAL: " + pesan)
    kirim_callback("", status="error", error_msg=pesan)
    sys.exit(1)


# ---------- 3. Background ----------
def buat_gradien():
    img = Image.new("RGB", (WIDTH, HEIGHT))
    d = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        t = y / HEIGHT
        d.line([(0, y), (WIDTH, y)], fill=(int(20 + 10 * t), int(80 + 40 * t), int(120 + 60 * t)))
    img.save(BG_PATH)


def download_background():
    print("Mengunduh background dari Pixabay...")
    first = KEYWORDS.split(",")[0].strip() if KEYWORDS else "education"
    for q in [first, "teacher classroom", "education school", "learning"]:
        try:
            url = ("https://pixabay.com/api/?key=" + PIXABAY_KEY +
                   "&q=" + requests.utils.quote(q) +
                   "&image_type=photo&orientation=vertical&per_page=5&safesearch=true")
            hits = requests.get(url, timeout=20).json().get("hits", [])
            if hits:
                img_url = hits[0].get("largeImageURL", "")
                if img_url:
                    BG_PATH.write_bytes(requests.get(img_url, timeout=40).content)
                    Image.open(BG_PATH).verify()
                    print("Background OK: " + q)
                    return
        except Exception as ex:
            print("Pixabay '" + q + "' gagal: " + str(ex))
    print("Pakai background gradien")
    buat_gradien()


# ---------- 4. Voiceover ----------
async def _tts():
    import edge_tts
    com = edge_tts.Communicate(SCRIPT_TEXT, voice="id-ID-GadisNeural")
    await com.save(str(AUDIO_PATH))


def buat_voiceover():
    print("Membuat voiceover...")
    try:
        asyncio.run(_tts())
    except Exception as ex:
        gagal("Voiceover gagal: " + str(ex))
    if not AUDIO_PATH.exists() or AUDIO_PATH.stat().st_size < 1000:
        gagal("File voiceover kosong")
    print("Voiceover OK")


# ---------- 5. Frame ----------
def load_font(bold, size):
    kandidat = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for p in kandidat:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    return ImageFont.load_default()


def tengah(draw, teks, font, y, fill, shadow=True):
    w = draw.textlength(teks, font=font)
    x = (WIDTH - w) / 2
    if shadow:
        draw.text((x + 3, y + 3), teks, font=font, fill=(0, 0, 0))
    draw.text((x, y), teks, font=font, fill=fill)


def buat_frame():
    print("Membuat frame...")
    try:
        bg = Image.open(BG_PATH).convert("RGB")
        # crop tengah ke rasio 9:16 lalu resize
        rasio = WIDTH / HEIGHT
        w, h = bg.size
        if w / h > rasio:
            nw = int(h * rasio)
            bg = bg.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
        else:
            nh = int(w / rasio)
            bg = bg.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
        bg = bg.resize((WIDTH, HEIGHT))
    except Exception:
        buat_gradien()
        bg = Image.open(BG_PATH).convert("RGB")

    gelap = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    frame = Image.blend(bg, gelap, 0.55)
    d = ImageDraw.Draw(frame)

    f_judul = load_font(True, 64)
    f_isi = load_font(False, 46)
    f_cta = load_font(True, 40)

    y = 260
    for baris in textwrap.wrap(TITLE, width=22):
        tengah(d, baris, f_judul, y, (255, 220, 50))
        y += 80
    y += 50

    kalimat = [k.strip() for k in SCRIPT_TEXT.replace("...", ".").split(". ") if k.strip()][:4]
    for k in kalimat:
        for baris in textwrap.wrap(k, width=28):
            tengah(d, baris, f_isi, y, (255, 255, 255))
            y += 60
        y += 30

    cta = "Klik link di bawah untuk info lengkap"
    wc = d.textlength(cta, font=f_cta)
    yc = HEIGHT - 260
    d.rectangle([(WIDTH / 2 - wc / 2 - 30, yc - 20), (WIDTH / 2 + wc / 2 + 30, yc + 65)], fill=(255, 90, 0))
    d.text(((WIDTH - wc) / 2, yc), cta, font=f_cta, fill=(255, 255, 255))

    frame.save(FRAME_PATH)
    print("Frame OK")


# ---------- 6. Render video dengan ffmpeg ----------
def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def render_video():
    print("Render video...")
    cmd = [
        ffmpeg_exe(), "-y",
        "-loop", "1", "-i", str(FRAME_PATH),
        "-i", str(AUDIO_PATH),
        "-c:v", "libx264", "-tune", "stillimage", "-r", "30",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-shortest", str(VIDEO_PATH),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not VIDEO_PATH.exists():
        print(r.stderr[-1500:])
        gagal("ffmpeg gagal render video")
    print("Video OK, ukuran " + str(VIDEO_PATH.stat().st_size) + " byte")


# ---------- 7. Upload Cloudinary ----------
def upload_cloudinary():
    print("Upload ke Cloudinary...")
    url = "https://api.cloudinary.com/v1_1/" + CLOUD_NAME + "/video/upload"
    with open(VIDEO_PATH, "rb") as f:
        resp = requests.post(
            url,
            data={"upload_preset": CLOUD_PRESET, "public_id": JOB_ID, "folder": "kantor_ai"},
            files={"file": f},
            timeout=300,
        )
    hasil = resp.json()
    link = hasil.get("secure_url", "")
    if not link:
        gagal("Upload Cloudinary gagal: " + json.dumps(hasil)[:300])
    print("Upload OK: " + link)
    return link


# ---------- MAIN ----------
def main():
    print("=" * 40)
    print("KANTOR AI - generate video")
    print("=" * 40)
    download_background()
    buat_voiceover()
    buat_frame()
    render_video()
    link = upload_cloudinary()
    kirim_callback(link, status="success")
    print("SELESAI: " + link)


if __name__ == "__main__":
    main()
