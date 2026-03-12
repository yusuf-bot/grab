#!/usr/bin/env python3
"""
grab — VPS server
- Serves the web UI
- Proxies OMDB searches
- Extracts M3U8 via Playwright
- Tells the phone's local agent to download

Setup:
    pip install fastapi uvicorn playwright
    playwright install chromium && playwright install-deps chromium
    export API_KEY=your_secret
    export OMDB_KEY=your_omdb_key
    python server.py
"""

import asyncio
import os
import re
import subprocess
import time
import urllib.request
import urllib.parse
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

# ── Load .env file if present ─────────────────────────────────────────────────
def _load_env(path=".env"):
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), val)

_load_env()

app = FastAPI()

API_KEY   = os.environ.get("API_KEY",  "")
OMDB_KEY  = os.environ.get("OMDB_KEY", "")
TOR_PROXY = os.environ.get("TOR_PROXY", None)

if not API_KEY:
    raise RuntimeError("API_KEY is not set. Add it to your .env file.")
if not OMDB_KEY:
    raise RuntimeError("OMDB_KEY is not set. Add it to your .env file.")


# ── Auth ──────────────────────────────────────────────────────────────────────

def verify(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(401, "Invalid API key")


# ── Logging ───────────────────────────────────────────────────────────────────

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ── OMDB ──────────────────────────────────────────────────────────────────────

@app.get("/api/search")
async def search(q: str, x_api_key: str = Header(...)):
    verify(x_api_key)
    if not OMDB_KEY:
        raise HTTPException(500, "OMDB_KEY not set on server")
    url = "http://www.omdbapi.com/?" + urllib.parse.urlencode({"s": q, "apikey": OMDB_KEY})
    with urllib.request.urlopen(url, timeout=10) as r:
        data = json.loads(r.read())
    return data.get("Search", [])[:10] if data.get("Response") == "True" else []


@app.get("/api/detail")
async def detail(imdb: str, x_api_key: str = Header(...)):
    verify(x_api_key)
    url = "http://www.omdbapi.com/?" + urllib.parse.urlencode({"i": imdb, "apikey": OMDB_KEY})
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


@app.get("/api/season")
async def season(imdb: str, s: int, x_api_key: str = Header(...)):
    verify(x_api_key)
    url = "http://www.omdbapi.com/?" + urllib.parse.urlencode({"i": imdb, "Season": s, "apikey": OMDB_KEY})
    with urllib.request.urlopen(url, timeout=10) as r:
        data = json.loads(r.read())
    return data.get("Episodes", [])


# ── M3U8 extraction ───────────────────────────────────────────────────────────

async def extract_m3u8(imdb_id: str, season: int, episode: int, media_type: str = "tv") -> Optional[str]:
    if media_type == "tv":
        vidsrc_url = f"https://vidsrc.io/embed/tv?imdb={imdb_id}&season={season}&episode={episode}"
    else:
        vidsrc_url = f"https://vidsrc.io/embed/movie?imdb={imdb_id}"

    log(f"Fetching: {vidsrc_url}")

    iframe_url = None
    for attempt in range(3):
        curl_cmd = ["curl", "-s", vidsrc_url, "-H", "User-Agent: Mozilla/5.0", "-L"]
        if TOR_PROXY:
            curl_cmd.extend(["-x", TOR_PROXY])
        try:
            result = subprocess.run(curl_cmd, capture_output=True, text=True, timeout=30)
            matches = re.findall(r'cloudnestra\.com/rcp/([^"]+)"', result.stdout)
            if matches:
                iframe_url = "https://cloudnestra.com/rcp/" + matches[0]
                log(f"Got iframe: {iframe_url}")
                break
            if attempt < 2:
                await asyncio.sleep(2)
        except Exception as e:
            log(f"Curl error: {e}")
            if attempt < 2:
                await asyncio.sleep(2)

    if not iframe_url:
        return None

    from playwright.async_api import async_playwright
    m3u8_url = None

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        ctx_opts = {
            "viewport": {"width": 1920, "height": 1080},
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        if TOR_PROXY:
            ctx_opts["proxy"] = {"server": TOR_PROXY}

        context = await browser.new_context(**ctx_opts)
        page    = await context.new_page()

        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = {runtime: {}};
        """)

        def on_request(request):
            nonlocal m3u8_url
            if ".m3u8" in request.url and not m3u8_url:
                m3u8_url = request.url
                log(f"✅ M3U8: {request.url}")

        page.on("request", on_request)

        try:
            await page.goto(iframe_url, wait_until="networkidle", timeout=30000)
        except Exception as e:
            log(f"Navigation: {e}")

        await page.wait_for_timeout(3000)

        if not m3u8_url:
            try:
                if await page.locator("video").count() > 0:
                    await page.click("video", timeout=2000)
                else:
                    await page.mouse.click(960, 540)
            except Exception:
                pass
            for _ in range(15):
                if m3u8_url:
                    break
                await page.wait_for_timeout(1000)

        await browser.close()

    return m3u8_url


@app.get("/api/m3u8/tv", dependencies=[Depends(verify)])
async def get_tv_m3u8(imdb: str, season: int, episode: int):
    log(f"M3U8 request: {imdb} S{season:02d}E{episode:02d}")
    m3u8 = await extract_m3u8(imdb, season, episode, "tv")
    if not m3u8:
        raise HTTPException(404, "Could not extract M3U8")
    return {
        "m3u8": m3u8,
        "headers": {
            "Referer":    "https://cloudnestra.com/",
            "Origin":     "https://cloudnestra.com",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
    }


@app.get("/api/m3u8/movie", dependencies=[Depends(verify)])
async def get_movie_m3u8(imdb: str):
    log(f"M3U8 request: movie {imdb}")
    m3u8 = await extract_m3u8(imdb, 0, 0, "movie")
    if not m3u8:
        raise HTTPException(404, "Could not extract M3U8")
    return {
        "m3u8": m3u8,
        "headers": {
            "Referer":    "https://cloudnestra.com/",
            "Origin":     "https://cloudnestra.com",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


# ── Web UI ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def ui():
    return open("ui.html").read()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)