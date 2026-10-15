"""
generate_ad_video.py  (v8 - fokus 1 ide, suara natural, tanpa wajah Eropa)
Kantor AI - Video iklan ebook 109 Jalan Rezeki Guru.

Perubahan mendalam v8:
  SUARA (poin 3):
    - Edge-TTS dibuat BERSIH: tanpa geser pitch, rate natural -5%.
      (Geser pitch dan EQ/kompresi berat di versi lama membuat suara
       terdengar metalik/robot. Keduanya dihapus.)
    - Hanya loudnorm (normalisasi volume) sebagai proses akhir.
    - ElevenLabs tetap jadi prioritas jika ELEVENLABS_KEY valid.
    - Mesin suara yang dipakai dilaporkan ke Telegram (ElevenLabs / Edge-TTS)
      supaya mudah diagnosa.

  GAMBAR (poin 2):
    - Video fokus 1 ide (lele dalam ember), kata kunci HANYA objek/hewan
      (ikan, air, ember, uang, buku) sehingga TIDAK ada wajah orang Eropa.

  DURASI (poin 1):
    - 1 ide saja, 5 slide, sekitar 35-45 detik.

Uji lokal:  DRY_RUN=1 python generate_ad_video.py
"""

import os
import re
import sys
import json
import math
import asyncio
import subprocess
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

# ---------- 1. Baca payload ----------
payload = json.loads(os.environ.get("PAYLOAD_JSON", "{}") or "{}")
DRY_RUN = os.environ.get("DRY_RUN") == "1"

JOB_ID       = payload.get("job_id", "job_unknown")
SCRIPT_TEXT  = payload.get("script", "")
TITLE        = payload.get("title", "109 Jalan Rezeki Guru")
KEYWORDS     = payload.get("keywords", "fish pond,money")
CALLBACK_URL = payload.get("callback_url", "")
CLOUD_NAME   = payload.get("cloudinary_cloud", "xw5wrdgm")
CLOUD_PRESET = payload.get("cloudinary_preset", "kantorAI_preset")
PIXABAY_KEY  = payload.get("pixabay_key", "")

# --- Suara (Edge-TTS): BERSIH, tanpa geser pitch ---
EDGE_VOICE = "id-ID-GadisNeural"   # suara perempuan neural (Azure)
EDGE_RATE  = "-5%"                 # sedikit lebih tenang, tetap natural
# Catatan: pitch TIDAK digeser. Geser pitch membuat suara neural jadi robot.

# --- ElevenLabs (opsional, lebih natural) ---
ELEVENLABS_KEY      = os.environ.get("ELEVENLABS_KEY", "")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
ELEVENLABS_MODEL    = "eleven_multilingual_v2"

# Mesin suara yang benar-benar dipakai (untuk laporan ke Telegram)
TTS_DIPAKAI = "Edge-TTS"

W, H = 1080, 1920
FPS = 30
PAD = 0.5
XF = 0.35
ZOOM = 0.18
SUB_Y = 1050
SUB_H = 420
MUSIK_VOL = 0.16
TRANSISI = ["fade", "slideleft", "circleopen", "fade"]

OUT = Path("output")
TMP = OUT / "tmp"
TMP.mkdir(parents=True, exist_ok=True)
FINAL_VIDEO = OUT / "iklan_guru.mp4"

print("Job ID    : " + str(JOB_ID))
print("Title     : " + str(TITLE))
print("TTS primary: " + ("ElevenLabs" if ELEVENLABS_KEY else "Edge-TTS"))
print("Dry run   : " + str(DRY_RUN))

# ---------- 2. Konten default: 1 ide (lele dalam ember) ----------
# Kata kunci HANYA objek/hewan -> tidak ada wajah orang.
SLIDES_DEFAULT = [
    {
        "text": "Pak Guru, Bu Guru... mau punya penghasilan tambahan tanpa mengganggu jam mengajar?",
        "keyword": "rupiah money coins savings"
    },
    {
        "text": "Rahasianya satu: budidaya lele dalam ember, cukup di belakang rumah.",
        "keyword": "fish pond aquaculture water"
    },
    {
        "text": "Bu Siti, seorang guru honorer, memulai hanya dari 5 ember. Modalnya cuma Rp 100.000.",
        "keyword": "buckets water containers"
    },
    {
        "text": "Dalam 1,5 tahun embernya menjadi 50, dan kini ia pemasok lele untuk warung sekecamatan.",
        "keyword": "fresh fish market harvest"
    },
    {
        "text": "Panduan lengkapnya ada di ebook 109 Jalan Rezeki Guru. Klik link di bawah dan mulai hari ini juga!",
        "keyword": "open book reading notebook"
    },
]


# ---------- 3. Utilitas ----------
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
        "tts_engine": TTS_DIPAKAI,
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


def jalankan_lunak(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0, r.stderr


def jalankan(cmd, label):
    ok, err = jalankan_lunak(cmd)
    if not ok:
        print(err[-2500:])
        gagal("ffmpeg gagal: " + label)


def durasi(path):
    r = subprocess.run([FF, "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", r.stderr)
    if not m:
        return 4.0
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


# ---------- 4. Susun daftar slide ----------
def susun_slide():
    slides = []
    for item in (payload.get("slides") or []):
        if isinstance(item, dict):
            teks = str(item.get("text", "")).strip()
            kata = str(item.get("keyword", "")).strip()
        else:
            teks, kata = str(item).strip(), ""
        if teks:
            slides.append({"text": teks, "keyword": kata})

    if not slides and SCRIPT_TEXT.strip():
        kalimat = [k.strip() for k in re.split(r"(?<=[.!?])\s+", SCRIPT_TEXT) if k.strip()]
        kws = [k.strip() for k in KEYWORDS.split(",") if k.strip()] or ["fish pond"]
        for i, k in enumerate(kalimat[:8]):
            slides.append({"text": k, "keyword": kws[i % len(kws)]})

    if not slides:
        slides = [dict(s) for s in SLIDES_DEFAULT]

    if not slides:
        gagal("Tidak ada teks untuk dibuat slide")
    return slides


# ---------- 5. Media latar ----------
GRADIEN = [
    ((20, 80, 120), (30, 120, 180)),
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


def daftar_kueri(keyword):
    """
    Kueri Pixabay. Fallback HANYA objek (bukan orang) agar tidak muncul
    wajah Barat: ikan, air, uang, pasar.
    """
    kw = (keyword or "").strip()
    hasil = []
    if kw:
        hasil.append(kw)
    bagian = kw.split(" ")
    if len(bagian) >= 2:
        dua = bagian[0] + " " + bagian[1]
        if dua not in hasil:
            hasil.append(dua)
    for fallback in ["fish pond water", "fresh fish market", "rupiah money"]:
        if fallback not in hasil:
            hasil.append(fallback)
    return hasil[:5]


def unduh(url, path, batas_mb=45):
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = 0
        with open(path, "wb") as f:
            for blok in r.iter_content(chunk_size=1024 * 256):
                if not blok:
                    continue
                total += len(blok)
                if total > batas_mb * 1024 * 1024:
                    raise RuntimeError("file terlalu besar")
                f.write(blok)


def cari_footage(i, keyword, dipakai):
    for q in daftar_kueri(keyword):
        try:
            r = requests.get("https://pixabay.com/api/videos/",
                             params={"key": PIXABAY_KEY, "q": q, "per_page": 15, "safesearch": "true"},
                             timeout=20)
            for h in r.json().get("hits", []):
                if h.get("id") in dipakai or h.get("duration", 0) < 3:
                    continue
                vids = h.get("videos", {})
                pilih = None
                for nama in ("medium", "small", "large"):
                    v = vids.get(nama) or {}
                    if v.get("url"):
                        pilih = v
                        break
                if not pilih:
                    continue
                path = TMP / ("footage_%d.mp4" % i)
                try:
                    unduh(pilih["url"], path)
                except Exception as ex:
                    print("Unduh footage gagal: " + str(ex))
                    continue
                dipakai.add(h.get("id"))
                print("Slide " + str(i + 1) + ": footage '" + q + "' id " + str(h.get("id")))
                return path
        except Exception as ex:
            print("Pixabay video '" + q + "' gagal: " + str(ex))
    return None


def cari_foto(i, keyword, dipakai):
    for q in daftar_kueri(keyword):
        try:
            r = requests.get("https://pixabay.com/api/",
                             params={"key": PIXABAY_KEY, "q": q, "image_type": "photo",
                                     "orientation": "vertical", "per_page": 15, "safesearch": "true"},
                             timeout=20)
            for h in r.json().get("hits", []):
                key = "f" + str(h.get("id"))
                if key in dipakai or not h.get("largeImageURL"):
                    continue
                path = TMP / ("foto_raw_%d.jpg" % i)
                path.write_bytes(requests.get(h["largeImageURL"], timeout=40).content)
                Image.open(path).verify()
                dipakai.add(key)
                print("Slide " + str(i + 1) + ": foto '" + q + "'")
                return path
        except Exception as ex:
            print("Pixabay foto '" + q + "' gagal: " + str(ex))
    return None


def foto_siap(i, raw_path):
    final_path = TMP / ("foto_%d.jpg" % i)
    img = Image.open(raw_path).convert("RGB")
    rasio = W / H
    w, h = img.size
    if w / h > rasio:
        nw = int(h * rasio)
        img = img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = int(w / rasio)
        img = img.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    img = img.resize((W, H), Image.LANCZOS)
    img = Image.blend(img, Image.new("RGB", img.size, (0, 0, 0)), 0.35)
    img.save(final_path, quality=92)
    return final_path


def footage_uji(i):
    path = TMP / ("footage_%d.mp4" % i)
    jalankan([FF, "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=6",
              "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], "footage uji")
    return path


def siapkan_media(i, keyword, dipakai):
    if DRY_RUN:
        if i % 2 == 0:
            return "footage", footage_uji(i)
        raw = TMP / ("foto_raw_%d.jpg" % i)
        gambar_gradien(i, raw)
        return "foto", foto_siap(i, raw)

    if PIXABAY_KEY:
        f = cari_footage(i, keyword, dipakai)
        if f:
            return "footage", f
        p = cari_foto(i, keyword, dipakai)
        if p:
            return "foto", foto_siap(i, p)
    print("Slide " + str(i + 1) + ": pakai gradien")
    raw = TMP / ("foto_raw_%d.jpg" % i)
    gambar_gradien(i, raw)
    return "foto", foto_siap(i, raw)


def foto_cadangan(i):
    raw = TMP / ("foto_raw_%d.jpg" % i)
    gambar_gradien(i, raw)
    return foto_siap(i, raw)


# ---------- 6. Suara (TTS bertingkat, BERSIH) ----------
def perkaya_audio(raw_path, out_path):
    """
    Proses akhir MINIMAL: hanya normalisasi volume (loudnorm).
    Tidak ada EQ/kompresi berat -> suara tetap natural, tidak metalik.
    Jika gagal, salin apa adanya.
    """
    cmd = [FF, "-y", "-i", str(raw_path),
           "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
           "-acodec", "libmp3lame", "-q:a", "2",
           "-ar", "44100", "-ac", "2", str(out_path)]
    ok, err = jalankan_lunak(cmd)
    if not ok:
        print("Loudnorm gagal, pakai asli: " + err[-200:])
        import shutil
        shutil.copy(str(raw_path), str(out_path))


def tts_elevenlabs(teks, path_out):
    url = "https://api.elevenlabs.io/v1/text-to-speech/" + ELEVENLABS_VOICE_ID
    headers = {
        "xi-api-key": ELEVENLABS_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    body = {
        "text": teks,
        "model_id": ELEVENLABS_MODEL,
        "voice_settings": {
            "stability": 0.50,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
        },
    }
    for percobaan in range(3):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=60)
            if r.status_code == 200 and len(r.content) > 800:
                path_out.write_bytes(r.content)
                return True
            elif r.status_code == 401:
                print("ElevenLabs: API key DITOLAK (401). Beralih ke Edge-TTS.")
                return False
            elif r.status_code == 429:
                print("ElevenLabs: kuota habis (429). Beralih ke Edge-TTS.")
                return False
            else:
                print("ElevenLabs status " + str(r.status_code) + " percobaan " + str(percobaan + 1)
                      + " -> " + r.text[:200])
        except Exception as ex:
            print("ElevenLabs error percobaan " + str(percobaan + 1) + ": " + str(ex))
        import time
        time.sleep(2 * (percobaan + 1))
    return False


async def _edge_tts_satu(teks, path_out, percobaan_maks=3):
    import edge_tts
    for percobaan in range(percobaan_maks):
        try:
            # BERSIH: hanya rate, tanpa pitch
            comm = edge_tts.Communicate(teks, EDGE_VOICE, rate=EDGE_RATE)
            await comm.save(str(path_out))
            if path_out.exists() and path_out.stat().st_size > 800:
                return True
        except Exception as ex:
            print("Edge-TTS percobaan " + str(percobaan + 1) + " gagal: " + str(ex))
            await asyncio.sleep(2 * (percobaan + 1))
    return False


def buat_suara(slides):
    global TTS_DIPAKAI
    print("Membuat suara per slide...")

    if DRY_RUN:
        TTS_DIPAKAI = "DRY_RUN"
        for i, s in enumerate(slides):
            path = TMP / ("voice_%d.mp3" % i)
            jalankan([FF, "-y", "-f", "lavfi", "-i", "sine=f=220:d=%s" % (3.0 + 0.4 * i),
                      "-af", "volume=0.2", "-acodec", "libmp3lame", "-q:a", "6", str(path)], "audio uji")
            s["audio"] = path
        return

    pakai_elevenlabs = bool(ELEVENLABS_KEY)
    elevenlabs_gagal = False
    jumlah_eleven = 0
    jumlah_edge = 0

    for i, s in enumerate(slides):
        path_raw = TMP / ("voice_raw_%d.mp3" % i)
        path_final = TMP / ("voice_%d.mp3" % i)
        berhasil = False

        if pakai_elevenlabs and not elevenlabs_gagal:
            if tts_elevenlabs(s["text"], path_raw):
                print("Slide " + str(i + 1) + ": ElevenLabs OK")
                jumlah_eleven += 1
                berhasil = True
            else:
                elevenlabs_gagal = True

        if not berhasil:
            async def _jalankan():
                return await _edge_tts_satu(s["text"], path_raw)
            if not asyncio.run(_jalankan()):
                gagal("TTS slide " + str(i + 1) + " gagal (ElevenLabs & Edge-TTS)")
            print("Slide " + str(i + 1) + ": Edge-TTS OK")
            jumlah_edge += 1

        perkaya_audio(path_raw, path_final)
        s["audio"] = path_final

    # Tentukan mesin dominan untuk laporan
    if jumlah_eleven > 0 and jumlah_edge == 0:
        TTS_DIPAKAI = "ElevenLabs"
    elif jumlah_eleven > 0 and jumlah_edge > 0:
        TTS_DIPAKAI = "Campuran (ElevenLabs+Edge)"
    else:
        TTS_DIPAKAI = "Edge-TTS"
    print("MESIN SUARA DIPAKAI: " + TTS_DIPAKAI)


# ---------- 7. Overlay statis ----------
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


def overlay_statis(i, n, terakhir):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    for y in range(320):
        alpha = int(180 * (1.0 - y / 320.0))
        d.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
    for y in range(950, H):
        alpha = int(min(200, (y - 950) / float(H - 950) * 200))
        d.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))

    f_brand = muat_font(True, 38)
    brand = "109 JALAN REZEKI GURU"
    bw = d.textlength(brand, font=f_brand)
    d.rounded_rectangle([((W - bw) / 2 - 36, 120), ((W + bw) / 2 + 36, 200)], radius=40, fill=(255, 200, 0, 255))
    d.text(((W - bw) / 2, 138), brand, font=f_brand, fill=(20, 20, 20, 255))

    jarak = 44
    x0 = W / 2 - (n - 1) * jarak / 2
    for k in range(n):
        r = 14 if k == i else 10
        warna = (255, 255, 255, 240) if k == i else (255, 255, 255, 110)
        d.ellipse([x0 + k * jarak - r, 250 - r, x0 + k * jarak + r, 250 + r], fill=warna)

    if terakhir:
        f_cta = muat_font(True, 54)
        f_sub = muat_font(False, 36)
        cta = "KLIK LINK DI BAWAH"
        cw = d.textlength(cta, font=f_cta)
        d.rounded_rectangle([(W / 2 - 410, 1530), (W / 2 + 410, 1662)], radius=66, fill=(255, 90, 0, 255))
        d.text(((W - cw) / 2, 1556), cta, font=f_cta, fill=(255, 255, 255, 255))
        sub = "Dapatkan ebooknya sekarang"
        sw = d.textlength(sub, font=f_sub)
        d.text(((W - sw) / 2, 1680), sub, font=f_sub, fill=(255, 255, 255, 235))

    path = TMP / ("statis_%d.png" % i)
    img.save(path)
    return path


def pecah_subtitle(teks):
    kata = teks.split()
    potongan, cur = [], []
    for k in kata:
        cur.append(k)
        if len(cur) >= 3 or k.endswith((",", ".", "?", "!")):
            potongan.append(cur)
            cur = []
    if cur:
        if potongan and len(cur) == 1 and len(potongan[-1]) <= 2:
            potongan[-1].extend(cur)
        else:
            potongan.append(cur)
    return potongan


def sorot(kata):
    return bool(re.search(r"\d|Rp|%", kata))


def gambar_potongan(i, k, kata_list):
    img = Image.new("RGBA", (W, SUB_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    ukuran = 100
    while True:
        f = muat_font(True, ukuran)
        spasi = d.textlength(" ", font=f)
        lebar = sum(d.textlength(x, font=f) for x in kata_list) + spasi * (len(kata_list) - 1)
        if lebar <= 960 or ukuran <= 48:
            break
        ukuran -= 4

    baris = [kata_list]
    if lebar > 960 and len(kata_list) > 1:
        tengah = (len(kata_list) + 1) // 2
        baris = [kata_list[:tengah], kata_list[tengah:]]

    lh = int(ukuran * 1.25)
    tinggi = lh * len(baris)
    y = (SUB_H - tinggi) // 2
    for b in baris:
        spasi = d.textlength(" ", font=f)
        lb = sum(d.textlength(x, font=f) for x in b) + spasi * (len(b) - 1)
        x = (W - lb) / 2
        for kata in b:
            warna = (255, 214, 10, 255) if sorot(kata) else (255, 255, 255, 255)
            d.text((x + 4, y + 5), kata, font=f, fill=(0, 0, 0, 160), stroke_width=6, stroke_fill=(0, 0, 0, 160))
            d.text((x, y), kata, font=f, fill=warna, stroke_width=6, stroke_fill=(0, 0, 0, 255))
            x += d.textlength(kata, font=f) + spasi
        y += lh

    path = TMP / ("sub_%d_%d.png" % (i, k))
    img.save(path)
    return path


def jadwal_subtitle(i, teks, lama_bicara):
    pot = pecah_subtitle(teks)
    bobot = []
    for p in pot:
        s = " ".join(p)
        bobot.append(len(s) + (5 if s.endswith((",", ".", "?", "!")) else 0))
    total_bobot = float(sum(bobot)) or 1.0
    mulai = 0.08
    sisa = max(lama_bicara - 0.16, 1.0)
    hasil = []
    t = mulai
    for k, p in enumerate(pot):
        dur = sisa * bobot[k] / total_bobot
        hasil.append((gambar_potongan(i, k, p), t, t + dur))
        t += dur
    if hasil:
        png, a, b = hasil[-1]
        hasil[-1] = (png, a, b + PAD)
    return hasil


# ---------- 8. Render adegan ----------
def filter_dasar(jenis, media, frames):
    if jenis == "foto":
        z = "1+%s*on/%d" % (ZOOM, frames)
        return ("[0:v]zoompan=z='" + z + "':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                ":d=" + str(frames) + ":s=" + str(W) + "x" + str(H) + ":fps=" + str(FPS)
                + ",format=yuv420p[base];")
    return (
        "[0:v]fps=" + str(FPS) +
        ",scale=" + str(W) + ":" + str(H) +
        ":force_original_aspect_ratio=increase" +
        ",crop=" + str(W) + ":" + str(H) +
        ",eq=brightness=-0.22" +
        ",setsar=1,format=yuv420p[base];"
    )


def render_adegan(i, n, jenis, media, teks, audio, terakhir):
    bicara = durasi(audio)
    total = bicara + PAD
    frames = max(int(round(total * FPS)), 2)
    tot = "%.3f" % total

    statis = overlay_statis(i, n, terakhir)
    potongan = jadwal_subtitle(i, teks, bicara)

    if jenis == "footage":
        masuk = ["-stream_loop", "-1", "-t", tot, "-i", str(media)]
    else:
        masuk = ["-i", str(media)]
    masuk += ["-loop", "1", "-framerate", str(FPS), "-t", tot, "-i", str(statis)]
    for png, a, b in potongan:
        masuk += ["-loop", "1", "-framerate", str(FPS), "-t", tot, "-i", str(png)]
    idx_audio = 2 + len(potongan)
    masuk += ["-i", str(audio)]

    fc = filter_dasar(jenis, media, frames)
    fc += "[1:v]format=rgba[st];[base][st]overlay=0:0[o0];"
    prev = "o0"
    for k, (png, a, b) in enumerate(potongan):
        fc += "[" + str(2 + k) + ":v]format=rgba[c" + str(k) + "];"
        fc += ("[" + prev + "][c" + str(k) + "]overlay=0:" + str(SUB_Y) +
               ":enable='between(t," + ("%.3f" % a) + "," + ("%.3f" % b) + ")'[o" + str(k + 1) + "];")
        prev = "o" + str(k + 1)
    fc += "[" + prev + "]format=yuv420p[v]"

    seg = TMP / ("seg_%d.mp4" % i)
    cmd = [FF, "-y"] + masuk + [
        "-filter_complex", fc,
        "-map", "[v]", "-map", str(idx_audio) + ":a",
        "-af", "apad=pad_dur=" + str(PAD),
        "-t", tot,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
        "-pix_fmt", "yuv420p", str(seg)]
    ok, err = jalankan_lunak(cmd)
    return ok, err, seg


def buat_adegan(i, n, jenis, media, teks, audio):
    terakhir = (i == n - 1)
    ok, err, seg = render_adegan(i, n, jenis, media, teks, audio, terakhir)
    if ok:
        return seg
    print("Render adegan " + str(i + 1) + " (" + jenis + ") gagal, pakai cadangan foto.")
    print(err[-1500:])
    ok, err, seg = render_adegan(i, n, "foto", foto_cadangan(i), teks, audio, terakhir)
    if not ok:
        print(err[-2500:])
        gagal("ffmpeg gagal render adegan " + str(i + 1))
    return seg


# ---------- 9. Musik latar ----------
def buat_musik(total):
    path = TMP / "musik.wav"
    akor = [
        [130.81, 164.81, 196.00, 246.94],
        [110.00, 130.81, 164.81, 196.00],
        [87.31, 130.81, 174.61, 220.00],
        [98.00, 123.47, 146.83, 196.00],
    ]
    dur = 6.0
    banyak = int(math.ceil((total + 2) / dur)) + 1
    cmd = [FF, "-y"]
    fc = ""
    nomor = 0
    for c in range(banyak):
        daftar = akor[c % len(akor)]
        label = ""
        for fr in daftar:
            cmd += ["-f", "lavfi", "-i", "sine=f=" + str(fr) + ":d=" + str(dur) + ":r=44100"]
            label += "[" + str(nomor) + ":a]"
            nomor += 1
        fc += (label + "amix=inputs=" + str(len(daftar)) + ":normalize=0,afade=t=in:d=1.5,afade=t=out:st=" +
               str(dur - 1.5) + ":d=1.5[ch" + str(c) + "];")
    gabung_label = "".join("[ch" + str(c) + "]" for c in range(banyak))
    fc += (gabung_label + "concat=n=" + str(banyak) + ":v=0:a=1,lowpass=f=1400,tremolo=f=0.25:d=0.3,"
           "aformat=sample_fmts=fltp:channel_layouts=stereo[m]")
    cmd += ["-filter_complex", fc, "-map", "[m]", "-t", "%.2f" % (total + 2), str(path)]
    ok, err = jalankan_lunak(cmd)
    if not ok:
        print("Musik gagal dibuat, lanjut tanpa musik: " + err[-400:])
        return None
    return path


# ---------- 9b. Efek suara (Kenney CC0) ----------
SFX_BASE = "https://raw.githubusercontent.com/latent-spaces/brag/069432cac935ed1895859df201c31436c07fb74e/skills/brag/assets/sfx/"
SFX_LOKAL = os.environ.get("SFX_LOKAL", "")


def ambil_sfx(nama_file, simpan_sebagai):
    path = TMP / simpan_sebagai
    try:
        if SFX_LOKAL:
            path.write_bytes((Path(SFX_LOKAL) / nama_file).read_bytes())
        elif DRY_RUN:
            return None
        else:
            r = requests.get(SFX_BASE + nama_file, timeout=30)
            r.raise_for_status()
            path.write_bytes(r.content)
        if path.stat().st_size < 500:
            return None
        return path
    except Exception as ex:
        print("SFX " + nama_file + " dilewati: " + str(ex))
        return None


# ---------- 10. Gabung semua adegan ----------
def gabung(segmen, musik, sfx=True):
    n = len(segmen)
    d = [durasi(s) for s in segmen]
    total = sum(d) - (n - 1) * XF

    cmd = [FF, "-y"]
    for s in segmen:
        cmd += ["-i", str(s)]
    idx_musik = None
    if musik:
        idx_musik = n
        cmd += ["-i", str(musik)]
    idx_bar = n + (1 if musik else 0)
    cmd += ["-f", "lavfi", "-i", "color=c=0xFFD60A:s=" + str(W) + "x12:d=" + ("%.2f" % total) + ":r=" + str(FPS)]

    efek = []
    if sfx:
        nxt = idx_bar + 1
        impact = ambil_sfx("impact/impactSoft_medium_001.ogg", "sfx_impact.ogg")
        bong = ambil_sfx("interface/bong_001.ogg", "sfx_bong.ogg")
        if impact:
            for k in range(1, n):
                cmd += ["-i", str(impact)]
                efek.append((nxt, sum(d[:k]) - k * XF, 0.4))
                nxt += 1
        if bong:
            cmd += ["-i", str(bong)]
            efek.append((nxt, max(total - 3.0, 0.5), 0.3))
            nxt += 1

    fc = ""
    vprev = "0:v"
    aprev = "0:a"
    for k in range(1, n):
        offset = sum(d[:k]) - k * XF
        tr = TRANSISI[(k - 1) % len(TRANSISI)]
        fc += ("[" + vprev + "][" + str(k) + ":v]xfade=transition=" + tr + ":duration=" + str(XF) +
               ":offset=" + ("%.3f" % offset) + "[v" + str(k) + "];")
        fc += "[" + aprev + "][" + str(k) + ":a]acrossfade=d=" + str(XF) + "[a" + str(k) + "];"
        vprev = "v" + str(k)
        aprev = "a" + str(k)

    fc += ("[" + vprev + "][" + str(idx_bar) + ":v]overlay=x='-w+w*t/" + ("%.2f" % total) +
           "':y=1900:eval=frame,fade=t=out:st=" + ("%.2f" % (total - 0.4)) + ":d=0.4,format=yuv420p[vout];")

    mix_in = "[" + aprev + "]"
    n_mix = 1
    for e, (idx, waktu, vol) in enumerate(efek):
        ms = int(max(waktu, 0) * 1000)
        fc += ("[" + str(idx) + ":a]aformat=sample_rates=44100:channel_layouts=stereo,volume=" + str(vol) +
               ",adelay=" + str(ms) + "|" + str(ms) + "[fx" + str(e) + "];")
        mix_in += "[fx" + str(e) + "]"
        n_mix += 1
    if n_mix > 1:
        fc += mix_in + "amix=inputs=" + str(n_mix) + ":duration=first:normalize=0[asuara];"
        aprev = "asuara"

    if idx_musik is not None:
        fc += ("[" + str(idx_musik) + ":a]atrim=0:" + ("%.2f" % total) + ",afade=t=in:d=1,afade=t=out:st=" +
               ("%.2f" % (total - 1.5)) + ":d=1.5,volume=" + str(MUSIK_VOL) + "[mus];"
               "[" + aprev + "][mus]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[aout]")
    else:
        fc += "[" + aprev + "]anull[aout]"

    cmd += ["-filter_complex", fc, "-map", "[vout]", "-map", "[aout]",
            "-t", "%.2f" % total,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(FINAL_VIDEO.resolve())]
    ok, err = jalankan_lunak(cmd)
    if not ok and musik:
        print("Gabung dengan musik gagal, coba tanpa musik.")
        print(err[-1500:])
        return gabung(segmen, None, sfx)
    if not ok and sfx:
        print("Gabung dengan efek suara gagal, coba tanpa efek.")
        print(err[-1500:])
        return gabung(segmen, musik, False)
    if not ok:
        print(err[-2500:])
        gagal("ffmpeg gagal menggabung adegan")
    return total


# ---------- 11. Cloudinary ----------
def upload_cloudinary():
    print("Upload ke Cloudinary...")
    url = "https://api.cloudinary.com/v1_1/" + CLOUD_NAME + "/video/upload"
    with open(FINAL_VIDEO, "rb") as f:
        resp = requests.post(
            url,
            data={"upload_preset": CLOUD_PRESET, "public_id": JOB_ID, "folder": "kantor_ai"},
            files={"file": f},
            timeout=600,
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
    print("KANTOR AI - generate video v8")
    print("=" * 40)

    slides = susun_slide()
    n = len(slides)
    print("Jumlah adegan: " + str(n))

    buat_suara(slides)

    dipakai = set()
    segmen = []
    for i, s in enumerate(slides):
        print("Adegan " + str(i + 1) + "/" + str(n) + ": " + s["text"][:60])
        jenis, media = siapkan_media(i, s["keyword"], dipakai)
        segmen.append(buat_adegan(i, n, jenis, media, s["text"], s["audio"]))

    total_est = sum(durasi(x) for x in segmen) - (n - 1) * XF
    musik = buat_musik(total_est)
    total = gabung(segmen, musik)
    print("Video OK, durasi " + ("%.1f" % total) + " detik, suara " + TTS_DIPAKAI)

    if DRY_RUN:
        print("DRY_RUN selesai, tidak upload.")
        return

    link = upload_cloudinary()
    kirim_callback(link, status="success")
    print("SELESAI: " + link)


if __name__ == "__main__":
    main()
