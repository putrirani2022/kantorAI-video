"""
generate_ad_video.py  (v5 - multi slide)
Kantor AI - Video iklan otomatis untuk ebook 109 Jalan Rezeki Guru.

Alur: tiap kalimat = 1 slide. Tiap slide punya
  - background foto sendiri (Pixabay, bebas pakai komersial)
  - suara sendiri (Edge-TTS) sehingga durasi slide pas dengan ucapan
  - gerak zoom pelan (bergantian masuk / keluar) + efek fade antar slide
Lalu semua slide digabung dengan ffmpeg, diunggah ke Cloudinary,
dan Apps Script diberi tahu lewat callback.

Uji lokal tanpa internet:  DRY_RUN=1 python generate_ad_video.py
"""

import os
import re
import sys
import json
import asyncio
import subprocess
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

# ---------- 1. Baca payload ----------
payload = json.loads(os.environ.get("PAYLOAD_JSON", "{}") or "{}")
DRY_RUN = os.environ.get("DRY_RUN") == "1"

JOB_ID       = payload.get("job_id", "job_unknown")
SCRIPT_TEXT  = payload.get("script", "Ebook 109 Jalan Rezeki Guru. Usaha sampingan untuk guru tanpa modal besar.")
TITLE        = payload.get("title", "109 Jalan Rezeki Guru")
KEYWORDS     = payload.get("keywords", "teacher,education")
CALLBACK_URL = payload.get("callback_url", "")
CLOUD_NAME   = payload.get("cloudinary_cloud", "xw5wrdgm")
CLOUD_PRESET = payload.get("cloudinary_preset", "kantorAI_preset")
PIXABAY_KEY  = payload.get("pixabay_key", "")

VOICE = "id-ID-GadisNeural"
W, H = 1080, 1920
FPS = 30
PAD = 0.45            # jeda (detik) setelah tiap kalimat
FADE = 0.25           # lama fade antar slide
ZOOM = 0.20           # seberapa dekat zoom (20 persen)

OUT = Path("output")
TMP = OUT / "tmp"
TMP.mkdir(parents=True, exist_ok=True)
FINAL_VIDEO = OUT / "iklan_guru.mp4"

print("Job ID  : " + str(JOB_ID))
print("Title   : " + str(TITLE))
print("Callback: " + str(CALLBACK_URL))
print("Dry run : " + str(DRY_RUN))


# ---------- 2. Utilitas ----------
def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


FF = ffmpeg_exe()


def kirim_callback(video_url, status="success", error_msg=""):
    if not CALLBACK_URL or DRY_RUN:
        print("Callback dilewati")
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


def jalankan(cmd, label):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-2000:])
        gagal("ffmpeg gagal: " + label)
    return r


def durasi_audio(path):
    r = subprocess.run([FF, "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", r.stderr)
    if not m:
        return 4.0
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


# ---------- 3. Susun daftar slide ----------
def susun_slide():
    slides = []
    raw = payload.get("slides") or []
    for item in raw:
        if isinstance(item, dict):
            teks = str(item.get("text", "")).strip()
            kata = str(item.get("keyword", "")).strip()
        else:
            teks, kata = str(item).strip(), ""
        if teks:
            slides.append({"text": teks, "keyword": kata})

    if not slides:
        kalimat = [k.strip() for k in re.split(r"(?<=[.!?])\s+", SCRIPT_TEXT) if k.strip()]
        kws = [k.strip() for k in KEYWORDS.split(",") if k.strip()] or ["education"]
        for i, k in enumerate(kalimat[:6]):
            slides.append({"text": k, "keyword": kws[i % len(kws)]})

    if not slides:
        gagal("Tidak ada teks untuk dibuat slide")
    return slides


# ---------- 4. Background ----------
GRADIEN = [
    ((20, 80, 120), (30, 120, 180)),
    ((90, 40, 110), (150, 70, 160)),
    ((20, 100, 70), (50, 160, 110)),
    ((120, 60, 20), (200, 110, 40)),
    ((30, 60, 100), (60, 100, 170)),
    ((100, 30, 50), (170, 60, 80)),
]


def gambar_gradien(i, path):
    c1, c2 = GRADIEN[i % len(GRADIEN)]
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(c1[k] + (c2[k] - c1[k]) * t) for k in range(3)))
    img.save(path)


def cari_pixabay(keyword, dipakai):
    kandidat = [keyword, keyword.split(" ")[0] if keyword else "", "teacher", "education"]
    for q in kandidat:
        if not q:
            continue
        try:
            url = ("https://pixabay.com/api/?key=" + PIXABAY_KEY +
                   "&q=" + requests.utils.quote(q) +
                   "&image_type=photo&orientation=vertical&per_page=15&safesearch=true")
            hits = requests.get(url, timeout=20).json().get("hits", [])
            for h in hits:
                if h.get("id") in dipakai:
                    continue
                link = h.get("largeImageURL", "")
                if link:
                    data = requests.get(link, timeout=40).content
                    dipakai.add(h.get("id"))
                    return data
        except Exception as ex:
            print("Pixabay '" + q + "' gagal: " + str(ex))
    return None


def siapkan_background(i, keyword, dipakai):
    """Hasil: file JPG ukuran 2160x3840 (cukup besar agar zoom halus)."""
    raw_path = TMP / ("bg_raw_%d.jpg" % i)
    final_path = TMP / ("bg_%d.jpg" % i)
    ok = False
    if not DRY_RUN and PIXABAY_KEY:
        data = cari_pixabay(keyword, dipakai)
        if data:
            try:
                raw_path.write_bytes(data)
                Image.open(raw_path).verify()
                ok = True
            except Exception:
                ok = False
    if not ok:
        print("Slide " + str(i + 1) + ": pakai gradien")
        gambar_gradien(i, raw_path)

    img = Image.open(raw_path).convert("RGB")
    rasio = W / H
    w, h = img.size
    if w / h > rasio:
        nw = int(h * rasio)
        img = img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = int(w / rasio)
        img = img.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    img = img.resize((W * 2, H * 2), Image.LANCZOS)
    gelap = Image.new("RGB", img.size, (0, 0, 0))
    img = Image.blend(img, gelap, 0.5)
    img.save(final_path, quality=92)
    return final_path


# ---------- 5. Suara ----------
async def _tts_semua(slides):
    import edge_tts
    for i, s in enumerate(slides):
        path = TMP / ("voice_%d.mp3" % i)
        for percobaan in range(3):
            try:
                await edge_tts.Communicate(s["text"], VOICE).save(str(path))
                if path.exists() and path.stat().st_size > 800:
                    break
            except Exception as ex:
                print("TTS slide " + str(i + 1) + " percobaan " + str(percobaan + 1) + " gagal: " + str(ex))
                await asyncio.sleep(2 * (percobaan + 1))
        else:
            raise RuntimeError("TTS slide " + str(i + 1) + " gagal")
        s["audio"] = path


def buat_suara(slides):
    print("Membuat suara per slide...")
    if DRY_RUN:
        for i, s in enumerate(slides):
            path = TMP / ("voice_%d.mp3" % i)
            lama = 2.0 + 0.5 * i
            jalankan([FF, "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", str(lama),
                      "-acodec", "libmp3lame", "-q:a", "9", str(path)], "audio uji")
            s["audio"] = path
        return
    try:
        asyncio.run(_tts_semua(slides))
    except Exception as ex:
        gagal("Voiceover gagal: " + str(ex))


# ---------- 6. Teks di atas gambar ----------
def muat_font(tebal, ukuran):
    daftar = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if tebal else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if tebal else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for p in daftar:
        try:
            return ImageFont.truetype(p, ukuran)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=ukuran)
    except TypeError:
        return ImageFont.load_default()


def bungkus(draw, teks, font, lebar):
    baris, cur = [], ""
    for kata in teks.split():
        coba = (cur + " " + kata).strip()
        if not cur or draw.textlength(coba, font=font) <= lebar:
            cur = coba
        else:
            baris.append(cur)
            cur = kata
    if cur:
        baris.append(cur)
    return baris or [teks]


def buat_overlay(i, n, teks, terakhir):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # label ebook di atas
    f_brand = muat_font(True, 38)
    brand = "109 JALAN REZEKI GURU"
    bw = d.textlength(brand, font=f_brand)
    d.rounded_rectangle([((W - bw) / 2 - 36, 110), ((W + bw) / 2 + 36, 190)], radius=40, fill=(255, 200, 0, 255))
    d.text(((W - bw) / 2, 128), brand, font=f_brand, fill=(20, 20, 20, 255))

    # titik penanda slide
    jarak = 44
    x0 = W / 2 - (n - 1) * jarak / 2
    for k in range(n):
        r = 14 if k == i else 10
        warna = (255, 255, 255, 240) if k == i else (255, 255, 255, 110)
        d.ellipse([x0 + k * jarak - r, 250 - r, x0 + k * jarak + r, 250 + r], fill=warna)

    # teks utama: kecilkan font sampai muat
    ukuran = 74
    while True:
        f = muat_font(True, ukuran)
        baris = bungkus(d, teks, f, 860)
        if len(baris) <= 7 or ukuran <= 44:
            break
        ukuran -= 4
    lh = int(ukuran * 1.32)
    blok = len(baris) * lh
    atas = (H - blok) // 2 - 40

    panel = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(panel).rounded_rectangle([(60, atas - 50), (W - 60, atas + blok + 50)], radius=48, fill=(0, 0, 0, 125))
    img = Image.alpha_composite(img, panel)
    d = ImageDraw.Draw(img)

    y = atas
    for b in baris:
        w = d.textlength(b, font=f)
        d.text(((W - w) / 2 + 3, y + 3), b, font=f, fill=(0, 0, 0, 200))
        d.text(((W - w) / 2, y), b, font=f, fill=(255, 255, 255, 255))
        y += lh

    # tombol ajakan di slide terakhir
    if terakhir:
        f_cta = muat_font(True, 50)
        f_sub = muat_font(False, 36)
        cta = "KLIK LINK DI BAWAH"
        cw = d.textlength(cta, font=f_cta)
        d.rounded_rectangle([(W / 2 - 400, H - 420), (W / 2 + 400, H - 290)], radius=65, fill=(255, 90, 0, 255))
        d.text(((W - cw) / 2, H - 392), cta, font=f_cta, fill=(255, 255, 255, 255))
        sub = "Info lengkap via WhatsApp"
        sw = d.textlength(sub, font=f_sub)
        d.text(((W - sw) / 2, H - 250), sub, font=f_sub, fill=(255, 255, 255, 235))

    path = TMP / ("overlay_%d.png" % i)
    img.save(path)
    return path


# ---------- 7. Render tiap slide ----------
def render_slide(i, bg, overlay, audio, zoom_masuk):
    total = durasi_audio(audio) + PAD
    frames = max(int(round(total * FPS)), 2)
    if zoom_masuk:
        z = "1+%s*on/%d" % (ZOOM, frames)
    else:
        z = "%s-%s*on/%d" % (1 + ZOOM, ZOOM, frames)

    fc = ("[0:v]zoompan=z='" + z + "':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
          ":d=" + str(frames) + ":s=" + str(W) + "x" + str(H) + ":fps=" + str(FPS) + ",format=yuv420p[bg];"
          "[1:v]format=rgba[ov];"
          "[bg][ov]overlay=0:0:shortest=1,"
          "fade=t=in:st=0:d=" + str(FADE) + ",fade=t=out:st=" + ("%.2f" % (total - FADE)) + ":d=" + str(FADE) +
          ",format=yuv420p[v]")

    seg = TMP / ("seg_%d.mp4" % i)
    cmd = [FF, "-y",
           "-i", str(bg),
           "-loop", "1", "-framerate", str(FPS), "-i", str(overlay),
           "-i", str(audio),
           "-filter_complex", fc,
           "-map", "[v]", "-map", "2:a",
           "-af", "apad=pad_dur=" + str(PAD),
           "-t", "%.2f" % total,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-r", str(FPS),
           "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
           "-pix_fmt", "yuv420p", str(seg)]
    jalankan(cmd, "render slide " + str(i + 1))
    return seg


def gabung(segmen):
    daftar = TMP / "daftar.txt"
    daftar.write_text("".join("file '" + s.name + "'\n" for s in segmen), encoding="utf-8")
    cmd = [FF, "-y", "-f", "concat", "-safe", "0", "-i", str(daftar),
           "-c", "copy", "-movflags", "+faststart", str(FINAL_VIDEO.resolve())]
    jalankan(cmd, "gabung slide")


# ---------- 8. Cloudinary ----------
def upload_cloudinary():
    print("Upload ke Cloudinary...")
    url = "https://api.cloudinary.com/v1_1/" + CLOUD_NAME + "/video/upload"
    with open(FINAL_VIDEO, "rb") as f:
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
    print("KANTOR AI - generate video multi slide")
    print("=" * 40)

    slides = susun_slide()
    n = len(slides)
    print("Jumlah slide: " + str(n))

    buat_suara(slides)

    dipakai = set()
    segmen = []
    for i, s in enumerate(slides):
        print("Slide " + str(i + 1) + "/" + str(n) + ": " + s["text"][:60])
        bg = siapkan_background(i, s["keyword"], dipakai)
        ov = buat_overlay(i, n, s["text"], i == n - 1)
        segmen.append(render_slide(i, bg, ov, s["audio"], i % 2 == 0))

    gabung(segmen)
    print("Video OK, ukuran " + str(FINAL_VIDEO.stat().st_size) + " byte, durasi " + ("%.1f" % durasi_audio(FINAL_VIDEO)) + " detik")

    if DRY_RUN:
        print("DRY_RUN selesai, tidak upload.")
        return

    link = upload_cloudinary()
    kirim_callback(link, status="success")
    print("SELESAI: " + link)


if __name__ == "__main__":
    main()
