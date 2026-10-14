"""
generate_ad_video.py  (v6 - footage video + subtitle bergerak + musik + transisi)
Kantor AI - Video iklan otomatis untuk ebook 109 Jalan Rezeki Guru.

Teknik yang dipelajari dari proyek open source MoneyPrinterTurbo (lisensi MIT),
ditulis ulang ringan agar jalan di GitHub Actions:
  - footage video asli dari Pixabay (cadangan: foto zoom, lalu gradien)
  - tata letak vertikal: footage di tengah, latar blur dari footage yang sama
  - subtitle muncul beberapa kata sekali, sinkron dengan suara, angka disorot kuning
  - suara per kalimat (Edge-TTS) sehingga durasi tiap adegan pas
  - transisi antar adegan (xfade) + musik latar buatan sendiri (bebas hak cipta)
  - bar progres di bawah, tombol ajakan di adegan terakhir
  - efek suara halus di tiap transisi (Kenney CC0, lewat repo latent-spaces/brag, MIT)
  - aturan kreatif dari /brag: hook 2 detik pertama, teks cukup lama terbaca, tiap frame layak posting

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
PAD = 0.55            # jeda setelah tiap kalimat (harus lebih besar dari XF)
XF = 0.35             # lama transisi antar adegan
ZOOM = 0.20           # zoom foto (cadangan)
FOOT_Y = 330          # posisi atas footage di tengah
FOOT_MAX_H = 700      # tinggi maksimal footage
SUB_Y = 1010          # posisi atas pita subtitle
SUB_H = 420           # tinggi pita subtitle
MUSIK_VOL = 0.20
TRANSISI = ["fade", "slideleft", "wipeleft", "circleopen", "fadeblack"]

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


def jalankan_lunak(cmd):
    """Jalankan ffmpeg, kembalikan (berhasil, stderr)."""
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


def ukuran_video(path):
    r = subprocess.run([FF, "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Video:.*?\b(\d{3,5})x(\d{3,5})\b", r.stderr)
    if not m:
        return 1920, 1080
    return int(m.group(1)), int(m.group(2))


# ---------- 3. Susun daftar slide ----------
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

    if not slides:
        kalimat = [k.strip() for k in re.split(r"(?<=[.!?])\s+", SCRIPT_TEXT) if k.strip()]
        kws = [k.strip() for k in KEYWORDS.split(",") if k.strip()] or ["education"]
        for i, k in enumerate(kalimat[:6]):
            slides.append({"text": k, "keyword": kws[i % len(kws)]})

    if not slides:
        gagal("Tidak ada teks untuk dibuat slide")
    return slides


# ---------- 4. Media latar: footage -> foto -> gradien ----------
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
    awal = keyword.split(" ")[0] if keyword else ""
    hasil = []
    for q in [keyword, awal, "teacher", "education"]:
        if q and q not in hasil:
            hasil.append(q)
    return hasil


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
    """Foto -> JPG 2160x3840 digelapkan, untuk zoom halus."""
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
    img = img.resize((W * 2, H * 2), Image.LANCZOS)
    img = Image.blend(img, Image.new("RGB", img.size, (0, 0, 0)), 0.45)
    img.save(final_path, quality=92)
    return final_path


def footage_uji(i):
    path = TMP / ("footage_%d.mp4" % i)
    jalankan([FF, "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=6",
              "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], "footage uji")
    return path


def siapkan_media(i, keyword, dipakai):
    """Kembalikan (jenis, path) dengan jenis 'footage' atau 'foto'."""
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
            jalankan([FF, "-y", "-f", "lavfi", "-i", "sine=f=220:d=%s" % (3.0 + 0.4 * i),
                      "-af", "volume=0.2", "-acodec", "libmp3lame", "-q:a", "6", str(path)], "audio uji")
            s["audio"] = path
        return
    try:
        asyncio.run(_tts_semua(slides))
    except Exception as ex:
        gagal("Voiceover gagal: " + str(ex))


# ---------- 6. Gambar teks ----------
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
    """Label ebook, titik penanda, dan tombol ajakan (adegan terakhir)."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

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
        f_cta = muat_font(True, 52)
        f_sub = muat_font(False, 36)
        cta = "KLIK LINK DI BAWAH"
        cw = d.textlength(cta, font=f_cta)
        d.rounded_rectangle([(W / 2 - 400, 1500), (W / 2 + 400, 1630)], radius=65, fill=(255, 90, 0, 255))
        d.text(((W - cw) / 2, 1526), cta, font=f_cta, fill=(255, 255, 255, 255))
        sub = "Info lengkap via WhatsApp"
        sw = d.textlength(sub, font=f_sub)
        d.text(((W - sw) / 2, 1650), sub, font=f_sub, fill=(255, 255, 255, 235))

    path = TMP / ("statis_%d.png" % i)
    img.save(path)
    return path


def pecah_subtitle(teks):
    """Pecah kalimat jadi potongan 2-3 kata, berhenti di tanda baca."""
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
    """Satu potongan subtitle di pita ukuran W x SUB_H."""
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

    # jika masih terlalu lebar, pecah jadi dua baris
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
    """Daftar (png, mulai, selesai) sinkron dengan lama ucapan."""
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
    # potongan terakhir tetap tampil sampai adegan selesai
    if hasil:
        png, a, b = hasil[-1]
        hasil[-1] = (png, a, b + PAD)
    return hasil


# ---------- 7. Render tiap adegan ----------
def filter_dasar(jenis, media, frames):
    if jenis == "foto":
        z = "1+%s*on/%d" % (ZOOM, frames)
        return ("[0:v]zoompan=z='" + z + "':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                ":d=" + str(frames) + ":s=" + str(W) + "x" + str(H) + ":fps=" + str(FPS) + ",format=yuv420p[base];")

    vw, vh = ukuran_video(media)
    if vh > vw:
        return ("[0:v]fps=" + str(FPS) + ",scale=" + str(W) + ":" + str(H) +
                ":force_original_aspect_ratio=increase,crop=" + str(W) + ":" + str(H) +
                ",setsar=1,eq=brightness=-0.08,format=yuv420p[base];")
    fg_h = int(round(W * vh / float(vw) / 2.0) * 2)
    if fg_h <= FOOT_MAX_H:
        skala_fg = "scale=" + str(W) + ":-2"
    else:
        skala_fg = "scale=-2:" + str(FOOT_MAX_H)
    return ("[0:v]fps=" + str(FPS) + ",split=2[a][b];"
            "[a]scale=" + str(W) + ":" + str(H) + ":force_original_aspect_ratio=increase,crop=" + str(W) + ":" + str(H) +
            ",boxblur=28:4,eq=brightness=-0.18,setsar=1[bgv];"
            "[b]" + skala_fg + ",setsar=1[fg];"
            "[bgv][fg]overlay=(W-w)/2:" + str(FOOT_Y) + ",format=yuv420p[base];")


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


# ---------- 8. Musik latar buatan sendiri ----------
def buat_musik(total):
    """Pad akor lembut dari gelombang sinus (original, bebas hak cipta)."""
    path = TMP / "musik.wav"
    akor = [
        [130.81, 164.81, 196.00, 246.94],   # Cmaj7
        [110.00, 130.81, 164.81, 196.00],   # Am7
        [87.31, 130.81, 174.61, 220.00],    # Fmaj7
        [98.00, 123.47, 146.83, 196.00],    # G6
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


# ---------- 8b. Efek suara (Kenney CC0, dari repo latent-spaces/brag) ----------
SFX_BASE = "https://raw.githubusercontent.com/latent-spaces/brag/069432cac935ed1895859df201c31436c07fb74e/skills/brag/assets/sfx/"
SFX_LOKAL = os.environ.get("SFX_LOKAL", "")


def ambil_sfx(nama_file, simpan_sebagai):
    """Unduh satu efek suara. Gagal = lanjut tanpa efek (None)."""
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


# ---------- 9. Gabung semua adegan ----------
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

    efek = []   # (index input, waktu detik, volume)
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


# ---------- 10. Cloudinary ----------
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
    print("KANTOR AI - generate video v6")
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
