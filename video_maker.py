"""Automatic short-video maker (script -> voice -> slide cards -> video).

Env variables (Railway > Variables):
  GEMINI_API_KEY   Gemini key
  GEMINI_MODEL     default gemini-3.5-flash
  TG_TOKEN         Telegram bot token used to deliver the test video
  TG_CHAT_ID       your Telegram user id (get it from @userinfobot)
  TOPIC            video topic, e.g. "Botda 1 daqiqada prezentatsiya"
  TTS_VOICE        default uz-UZ-MadinaNeural (men: uz-UZ-SardorNeural,
                   Russian: ru-RU-SvetlanaNeural)

Usage:
  python video_maker.py            # make one video for TOPIC, send to Telegram
  python video_maker.py "mavzu"    # same, topic from the command line
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time

import edge_tts
import imageio_ffmpeg
import requests
from mutagen.mp3 import MP3
from PIL import Image, ImageDraw, ImageFont

GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
TG_TOKEN = os.getenv("TG_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")
TTS_VOICE = os.getenv("TTS_VOICE", "uz-UZ-MadinaNeural")
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

W, H = 1080, 1920
CTA = {
    "title": "Botni sinab ko'ring",
    "voice": "Birinchi prezentatsiya bepul. Havola profilda.",
}


# ---------- 1. script ----------
def make_script(topic: str) -> list:
    prompt = (
        "Instagram Reels uchun o'zbek tilida 20-30 soniyalik ssenariy yoz. "
        f"Mavzu: {topic}. "
        "Reklama qilinadigan narsa: Telegram'da 1 daqiqada prezentatsiya "
        "yasab beradigan bot. 4 ta sahna yoz. Birinchi sahna kuchli ilgak "
        "bo'lsin. Har sahna uchun: 'title' (eng ko'pi 6 so'z, ekranga chiqadi) "
        "va 'voice' (1-2 qisqa gap, ovoz o'qiydi). Faqat JSON qaytar: "
        '[{"title": "...", "voice": "..."}, ...]'
    )
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent"
    )
    r = requests.post(
        url,
        headers={"x-goog-api-key": GEMINI_KEY},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        },
        timeout=90,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Gemini xato {r.status_code}: {r.text[:200]}")
    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    text = text.strip().removeprefix("```json").removesuffix("```").strip()
    scenes = json.loads(text)
    return scenes[:5] + [CTA]


# ---------- 2. voice ----------
async def _tts(text: str, path: str) -> None:
    await edge_tts.Communicate(text, TTS_VOICE).save(path)


def make_voice(text: str, path: str) -> float:
    asyncio.run(_tts(text, path))
    return MP3(path).info.length


# ---------- 3. slide card ----------
def _font(size: int):
    return ImageFont.load_default(size=size)


def _wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def make_card(scene: dict, idx: int, total: int, path: str) -> None:
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):  # vertical gradient
        t = y / H
        c = (int(15 + 25 * t), int(20 + 35 * t), int(60 + 90 * t))
        for x in range(W):
            px[x, y] = c
    d = ImageDraw.Draw(img)

    f_title, f_text, f_small = _font(96), _font(58), _font(44)
    y = 380
    for line in _wrap(d, scene["title"], f_title, W - 160):
        d.text((80, y), line, font=f_title, fill=(255, 255, 255))
        y += 120
    y += 60
    d.rectangle([80, y, 280, y + 10], fill=(255, 200, 60))
    y += 70
    for line in _wrap(d, scene["voice"], f_text, W - 160):
        d.text((80, y), line, font=f_text, fill=(215, 225, 245))
        y += 80
    d.text((80, H - 160), f"{idx}/{total}", font=f_small, fill=(150, 165, 200))
    img.save(path)


# ---------- 4. assemble ----------
def _run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-400:])


def build_video(cards: list, audios: list, out: str, tmp: str) -> None:
    parts = []
    for i, (card, audio) in enumerate(zip(cards, audios)):
        seg = os.path.join(tmp, f"seg{i}.mp4")
        _run([
            FFMPEG, "-y", "-loop", "1", "-i", card, "-i", audio,
            "-c:v", "libx264", "-tune", "stillimage", "-r", "30",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            "-pix_fmt", "yuv420p", "-shortest", seg,
        ])
        parts.append(seg)
    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w") as f:
        for p in parts:
            f.write(f"file '{p}'\n")
    _run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", lst,
          "-c", "copy", out])


def make_video(topic: str, out: str) -> str:
    scenes = make_script(topic)
    with tempfile.TemporaryDirectory() as tmp:
        cards, audios = [], []
        for i, sc in enumerate(scenes, 1):
            card = os.path.join(tmp, f"card{i}.png")
            audio = os.path.join(tmp, f"voice{i}.mp3")
            make_card(sc, i, len(scenes), card)
            make_voice(sc["voice"], audio)
            cards.append(card)
            audios.append(audio)
        build_video(cards, audios, out, tmp)
    return out


# ---------- 5. deliver (test stage) ----------
def send_to_telegram(path: str, caption: str) -> None:
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendVideo"
    with open(path, "rb") as f:
        r = requests.post(
            url,
            data={"chat_id": TG_CHAT_ID, "caption": caption[:1000]},
            files={"video": f},
            timeout=300,
        )
    if r.status_code != 200:  # never print the URL (it contains the token)
        raise RuntimeError(f"Telegram xato {r.status_code}: {r.text[:200]}")


def main() -> None:
    topic = " ".join(sys.argv[1:]) or os.getenv(
        "TOPIC", "Botda 1 daqiqada prezentatsiya tayyorlash"
    )
    out = os.path.join(tempfile.gettempdir(), "test_video.mp4")
    try:
        make_video(topic, out)
        send_to_telegram(out, f"Test video: {topic}")
        print("Video Telegram'ga yuborildi.")
    except Exception as e:
        print("Xato:", e)
    # keep the worker alive so Railway does not restart it in a loop
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
