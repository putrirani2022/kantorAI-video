"""
generate_ad_video.py  (v7.1 - potret penuh, gambar Indonesia, 5 ide bisnis)
Kantor AI - Video iklan otomatis untuk ebook 109 Jalan Rezeki Guru.

Teknik yang dipelajari dari:
  - MoneyPrinterTurbo (MIT): footage Pixabay, subtitle sinkron, musik, transisi
  - latent-spaces/brag (MIT): efek suara Kenney CC0, aturan kreatif hook 2 detik
  - jamiepine/voicebox (MIT): TTS bertingkat, Chatterbox/Kokoro engine analysis
  - ElevenLabs API: suara Bahasa Indonesia paling natural (gratis 10.000 karakter/bulan)

Strategi TTS (diurutkan dari terbaik):
  1. ElevenLabs  -> jika env ELEVENLABS_KEY diisi (gratis 10k karakter/bulan)
  2. Edge-TTS    -> cadangan selalu tersedia, dipercantik dengan EQ ffmpeg

Koreksi v7.1:
  - Footage mengisi penuh frame potret 1080x1920 (tidak letterbox landscape)
  - Pixabay mencari gambar/video bernuansa Indonesia (bukan orang Eropa)
  - Default video menampilkan 5 contoh ide bisnis yang mudah dicoba semua guru
  - Teks dan warna teks dipertahankan

Uji lokal tanpa internet:  DRY_RUN=1 python generate_ad_video.py
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
KEYWORDS     = payload.get("keywords", "guru indonesia,pendidikan")
CALLBACK_URL = payload.get("callback_url", "")
CLOUD_NAME   = payload.get("cloudinary_cloud", "xw5wrdgm")
CLOUD_PRESET = payload.get("cloudinary_preset", "kantorAI_preset")
PIXABAY_KEY  = payload.get("pixabay_key", "")

# TTS settings
EDGE_VOICE   = "id-ID-GadisNeural"
EDGE_RATE    = "-10%"     # sedikit lebih lambat = lebih natural
EDGE_PITCH   = "+8Hz"     # sedikit lebih hangat untuk suara perempuan

# ElevenLabs settings (diisi lewat GitHub Actions secret)
# Daftar suara ElevenLabs yang bagus untuk Bahasa Indonesia (eleven_multilingual_v2):
#   Sarah  : 21m00Tcm4TlvDq8ikWAM   (perempuan, lembut)
#   Rachel : EXAVITQu4vr4xnSDxMaL   (perempuan, jernih)
#   Aria   : 9BWtsMINqrJLrRacOk9x    (perempuan, profesional)
# Ganti ELEVENLABS_VOICE_ID di GitHub Actions Secrets jika ingin suara lain.
ELEVENLABS_KEY      = os.environ.get("ELEVENLABS_KEY", "")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
ELEVENLABS_MODEL    = "eleven_multilingual_v2"

W, H = 1080, 1920
FPS = 30
PAD = 0.55
XF = 0.35
ZOOM = 0.20
SUB_Y = 1050
SUB_H = 420
MUSIK_VOL = 0.20
TRANSISI = ["fade", "slideleft", "wipeleft", "circleopen", "fadeblack"]

OUT = Path("output")
TMP = OUT / "tmp"
TMP.mkdir(parents=True, exist_ok=True)
FINAL_VIDEO = OUT / "iklan_guru.mp4"

print("Job ID    : " + str(JOB_ID))
print("Title     : " + str(TITLE))
print("Callback  : " + str(CALLBACK_URL))
print("Dry run   : " + str(DRY_RUN))
print("TTS engine: " + ("ElevenLabs" if ELEVENLABS_KEY else "Edge-TTS"))

# ---------- 2. Konten default: 5 ide bisnis ----------
# Digunakan saat tidak ada payload slides/script dari GAS
# Masing-masing slide sesuai ide bisnis dari ebook "109 Jalan Rezeki Guru"
SLIDES_IDE_BISNIS = [
    {
        "text": "Pak Guru, Bu Guru — gaji belum cukup? Ada 109 usaha yang bisa jalan tanpa ganggu jam mengajar!",
        "keyword": "teacher indonesia school classroom"
    },
    {
        "text": "Ide 1 — Les Privat: 2 anak per hari dikali Rp 50.000 per sesi sama dengan Rp 3 juta per bulan. Bisa dilakukan sore sepulang sekolah!",
        "keyword": "private tutoring student indonesia learning"
    },
    {
        "text": "Ide 2 — Gorengan Sore: Modal Rp 50.000, untung Rp 150.000 per hari. Mulai jam 3 sore, tutup sebelum Maghrib!",
        "keyword": "indonesian food snack street food"
    },
    {
        "text": "Ide 3 — Agen PPOB: Bayar listrik dan BPJS warga dari HP sendiri. Tanpa stok barang. Penghasilan Rp 500 ribu hingga 1,5 juta per bulan!",
        "keyword": "smartphone digital payment indonesia"
    },
    {
        "text": "Ide 4 — Budidaya Lele Ember: Modal Rp 100.000. Bu Siti mulai dari 5 ember, kini 50 ember — jadi pemasok utama warung kecamatan!",
        "keyword": "fish farming indonesia pond village"
    },
    {
        "text": "Ide 5 — Kue Basah Jajanan Pasar: Buat malam, jual subuh sebelum berangkat mengajar. Rp 150.000 hingga 500.000 per hari dari dapur sendiri!",
        "keyword": "indonesian traditional cake market food"
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
        kws = [k.strip() for k in KEYWORDS.split(",") if k.strip()] or ["guru indonesia"]
        for i, k in enumerate(kalimat[:8]):
            slides.append({"text": k, "keyword": kws[i % len(kws)]})

    # Jika tidak ada konten dari payload, pakai 5 ide bisnis default
    if not slides:
        slides = [dict(s) for s in SLIDES_IDE_BISNIS]

    if not slides:
        gagal("Tidak ada teks untuk dibuat slide")
    return slides


# ---------- 5. Media latar ----------
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


def daftar_kueri(keyword):
    """
    Buat daftar kueri Pixabay dengan prioritas konten Indonesia.
    Urutan: keyword asli + indonesia, variasi, fallback guru indonesia.
    """
    kw_bersih = (keyword or "").strip()
    hasil = []

    # Prioritas: keyword spesifik dari slide
    if kw_bersih:
        hasil.append(kw_bersih)

    # Variasi pertama: kata pertama + indonesia
    awal = kw_bersih.split(" ")[0] if kw_bersih else ""
    if awal and awal.lower() not in ["indonesia"]:
        kandidat = awal + " indonesia"
        if kandidat not in hasil:
            hasil.append(kandidat)

    # Fallback generik bernuansa Asia/Indonesia
    for fallback in ["asian teacher school", "indonesia education", "guru indonesia mengajar"]:
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
    """
    Siapkan foto portrait 1080x1920.
    Foto digelapi sedikit agar teks di atasnya terbaca.
    """
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
    # Gelapi sedikit agar teks terbaca
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


# ---------- 6. Suara (TTS bertingkat) ----------
def perkaya_audio(raw_path, out_path):
    """
    Tambahkan kehangatan suara lewat ffmpeg:
      - equalizer  : naikkan frekuensi suara manusia (200-3000 Hz)
      - compand    : kompresi dinamis lembut agar suara lebih penuh
      - volume     : normalisasi keluaran
    Jika gagal, salin raw ke out tanpa perubahan.
    """
    filter_audio = (
        "equalizer=f=200:width_type=o:width=2:g=2,"
        "equalizer=f=800:width_type=o:width=1.5:g=1.5,"
        "equalizer=f=5000:width_type=o:width=1:g=-2,"
        "equalizer=f=9000:width_type=o:width=1:g=-3,"
        "compand=attacks=0.05:decays=0.3:points=-70/-70|-24/-16|-6/-4|0/-3:soft-knee=6:gain=2,"
        "volume=1.15"
    )
    cmd = [FF, "-y", "-i", str(raw_path),
           "-af", filter_audio,
           "-acodec", "libmp3lame", "-q:a", "3",
           "-ar", "44100", "-ac", "2", str(out_path)]
    ok, err = jalankan_lunak(cmd)
    if not ok:
        print("Perkaya audio gagal, pakai asli: " + err[-200:])
        import shutil
        shutil.copy(str(raw_path), str(out_path))


def tts_elevenlabs(teks, path_out):
    """Minta audio dari ElevenLabs API. Kembalikan True jika berhasil."""
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
            "stability": 0.45,
            "similarity_boost": 0.80,
            "style": 0.35,
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
                print("ElevenLabs: API key salah, beralih ke Edge-TTS")
                return False
            elif r.status_code == 429:
                print("ElevenLabs: batas karakter bulan ini habis, beralih ke Edge-TTS")
                return False
            else:
                print("ElevenLabs status " + str(r.status_code) + " percobaan " + str(percobaan + 1))
        except Exception as ex:
            print("ElevenLabs error percobaan " + str(percobaan + 1) + ": " + str(ex))
        import time
        time.sleep(2 * (percobaan + 1))
    return False


async def _edge_tts_satu(teks, path_out, percobaan_maks=3):
    import edge_tts
    for percobaan in range(percobaan_maks):
        try:
            comm = edge_tts.Communicate(teks, EDGE_VOICE, rate=EDGE_RATE, pitch=EDGE_PITCH)
            await comm.save(str(path_out))
            if path_out.exists() and path_out.stat().st_size > 800:
                return True
        except Exception as ex:
            print("Edge-TTS percobaan " + str(percobaan + 1) + " gagal: " + str(ex))
            await asyncio.sleep(2 * (percobaan + 1))
    return False


def buat_suara(slides):
    print("Membuat suara per slide...")

    if DRY_RUN:
        for i, s in enumerate(slides):
            path = TMP / ("voice_%d.mp3" % i)
            jalankan([FF, "-y", "-f", "lavfi", "-i", "sine=f=220:d=%s" % (3.0 + 0.4 * i),
                      "-af", "volume=0.2", "-acodec", "libmp3lame", "-q:a", "6", str(path)], "audio uji")
            s["audio"] = path
        return

    pakai_elevenlabs = bool(ELEVENLABS_KEY)
    elevenlabs_gagal = False

    for i, s in enumerate(slides):
        path_raw = TMP / ("voice_raw_%d.mp3" % i)
        path_final = TMP / ("voice_%d.mp3" % i)
        berhasil = False

        if pakai_elevenlabs and not elevenlabs_gagal:
            ok = tts_elevenlabs(s["text"], path_raw)
            if ok:
                print("Slide " + str(i + 1) + ": ElevenLabs OK")
                berhasil = True
            else:
                elevenlabs_gagal = True

        if not berhasil:
            async def _jalankan():
                return await _edge_tts_satu(s["text"], path_raw)
            ok = asyncio.run(_jalankan())
            if not ok:
                gagal("TTS slide " + str(i + 1) + " gagal baik ElevenLabs maupun Edge-TTS")
            print("Slide " + str(i + 1) + ": Edge-TTS OK")

        perkaya_audio(path_raw, path_final)
        s["audio"] = path_final

    print("Semua suara selesai.")


# ---------- 7. Gambar overlay statis ----------
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
    """
    Panel overlay transparan berisi:
    - Gradien gelap di atas (untuk area brand bar)
    - Gradien gelap di bawah (untuk area subtitle dan CTA)
    - Brand bar kuning
    - Dot indikator slide
    - (Jika terakhir) tombol CTA
    """
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Gradien gelap atas (y=0 s/d y=320): agar brand bar terbaca
    for y in range(320):
        alpha = int(180 * (1.0 - y / 320.0))
        d.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))

    # Gradien gelap bawah (y=950 s/d y=H): agar subtitle terbaca di atas footage
    for y in range(950, H):
        alpha = int(min(200, (y - 950) / float(H - 950) * 200))
        d.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))

    # Brand bar kuning
    f_brand = muat_font(True, 38)
    brand = "109 JALAN REZEKI GURU"
    bw = d.textlength(brand, font=f_brand)
    d.rounded_rectangle([((W - bw) / 2 - 36, 120), ((W + bw) / 2 + 36, 200)], radius=40, fill=(255, 200, 0, 255))
    d.text(((W - bw) / 2, 138), brand, font=f_brand, fill=(20, 20, 20, 255))

    # Dot indikator slide
    jarak = 44
    x0 = W / 2 - (n - 1) * jarak / 2
    for k in range(n):
        r = 14 if k == i else 10
        warna = (255, 255, 255, 240) if k == i else (255, 255, 255, 110)
        d.ellipse([x0 + k * jarak - r, 250 - r, x0 + k * jarak + r, 250 + r], fill=warna)

    # Tombol CTA pada slide terakhir
    if terakhir:
        f_cta = muat_font(True, 52)
        f_sub = muat_font(False, 36)
        cta = "KLIK LINK DI BAWAH"
        cw = d.textlength(cta, font=f_cta)
        d.rounded_rectangle([(W / 2 - 400, 1530), (W / 2 + 400, 1660)], radius=65, fill=(255, 90, 0, 255))
        d.text(((W - cw) / 2, 1556), cta, font=f_cta, fill=(255, 255, 255, 255))
        sub = "Info lengkap via WhatsApp"
        sw = d.textlength(sub, font=f_sub)
        d.text(((W - sw) / 2, 1678), sub, font=f_sub, fill=(255, 255, 255, 235))

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

    ukuran = 96
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


# ---------- 8. Render tiap adegan ----------
def filter_dasar(jenis, media, frames):
    """
    Hasilkan filter ffmpeg untuk latar.

    Perubahan v7.1:
    - Footage (video): MENGISI PENUH frame potret 1080x1920
      Cara: scale dengan force_original_aspect_ratio=increase lalu crop tengah.
      Ini menghilangkan tampilan landscape (letterbox) yang terlihat aneh di portrait.
    - Foto: zoompan seperti sebelumnya (sudah portrait 1:1).
    """
    if jenis == "foto":
        z = "1+%s*on/%d" % (ZOOM, frames)
        return ("[0:v]zoompan=z='" + z + "':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                ":d=" + str(frames) + ":s=" + str(W) + "x" + str(H) + ":fps=" + str(FPS)
                + ",format=yuv420p[base];")

    # Footage: isi penuh frame potret — scale cover lalu crop tengah
    # Ini membuat video apapun (landscape maupun portrait) mengisi 1080x1920 penuh
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
                efek.append((nxt, sum(d[:k]) - k * XF, 0.45))
                nxt += 1
        if bong:
            cmd += ["-i", str(bong)]
            efek.append((nxt, max(total - 3.2, 0.5), 0.35))
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
    print("KANTOR AI - generate video v7.1")
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
    print("Video OK, ukuran " + str(FINAL_VIDEO.stat().st_size) + " byte, durasi " + ("%.1f" % total) + " detik")

    if DRY_RUN:
        print("DRY_RUN selesai, tidak upload.")
        return

    link = upload_cloudinary()
    kirim_callback(link, status="success")
    print("SELESAI: " + link)


if __name__ == "__main__":
    main()
