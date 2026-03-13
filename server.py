#!/usr/bin/env python3
"""
grab — server.py
TMDB metadata · vidsrc streams · ffmpeg pipe to browser

Setup:
    pip install fastapi uvicorn playwright httpx
    playwright install chromium && playwright install-deps chromium
    apt install ffmpeg
    .env:
        API_KEY=yourkey
        TMDB_KEY=your_tmdb_key
        TOR_PROXY=socks5://127.0.0.1:9050  (optional)
    python server.py
"""

import asyncio
import gzip
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
import urllib.parse
import xmlrpc.client
from pathlib import Path
from typing import Optional, AsyncGenerator

import httpx
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import HTMLResponse, Response, StreamingResponse

# ── Load .env ─────────────────────────────────────────────────────────────────
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
TMDB_KEY  = os.environ.get("TMDB_KEY", "")
TOR_PROXY = os.environ.get("TOR_PROXY", None)

if not API_KEY:
    raise RuntimeError("API_KEY not set")
if not TMDB_KEY:
    raise RuntimeError("TMDB_KEY not set")

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMG  = "https://image.tmdb.org/t/p/w92"

CDN_HEADERS = {
    "Referer":    "https://cloudnestra.com/",
    "Origin":     "https://cloudnestra.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}
SEG_HEADERS = {**CDN_HEADERS, "Accept": "*/*", "Accept-Encoding": "identity"}


# ── Auth ──────────────────────────────────────────────────────────────────────

def verify(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(401, "Invalid API key")

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ── TMDB helpers ──────────────────────────────────────────────────────────────
async def tmdb_get(path: str, **params) -> dict:
    params["api_key"] = TMDB_KEY
    url = TMDB_BASE + path + "?" + urllib.parse.urlencode(params)
    async with httpx.AsyncClient(timeout=10, proxy=TOR_PROXY if TOR_PROXY else None) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.json()

# ── Search ────────────────────────────────────────────────────────────────────

@app.get("/api/search", dependencies=[Depends(verify)])
async def search(q: str):
    data = await tmdb_get("/search/multi", query=q, include_adult=False)
    out  = []
    for r in data.get("results", [])[:10]:
        mt = r.get("media_type")
        if mt not in ("movie", "tv"):
            continue
        imdb_id = None
        try:
            ext     = await tmdb_get(f"/{mt}/{r['id']}/external_ids")
            imdb_id = ext.get("imdb_id")
        except Exception as e:
            log(f"external_ids error for {r['id']}: {e}")
        out.append({
            "tmdb_id": r.get("id"),
            "imdb_id": imdb_id,
            "title":   r.get("title") or r.get("name") or "",
            "year":    (r.get("release_date") or r.get("first_air_date") or "")[:4],
            "type":    mt,
            "rating":  round(r.get("vote_average", 0), 1),
            "poster":  TMDB_IMG + r["poster_path"] if r.get("poster_path") else "",
        })
    return out


# ── Detail ────────────────────────────────────────────────────────────────────

@app.get("/api/detail", dependencies=[Depends(verify)])
async def detail(tmdb_id: int, type: str):
    if type not in ("movie", "tv"):
        raise HTTPException(400, "type must be 'movie' or 'tv'")
    d   = await tmdb_get(f"/{type}/{tmdb_id}")
    ext = await tmdb_get(f"/{type}/{tmdb_id}/external_ids")
    base = {
        "tmdb_id":  d["id"],
        "imdb_id":  ext.get("imdb_id", ""),
        "type":     type,
        "overview": d.get("overview", ""),
        "rating":   round(d.get("vote_average", 0), 1),
        "genres":   ", ".join(g["name"] for g in d.get("genres", [])),
        "poster":   TMDB_IMG + d["poster_path"] if d.get("poster_path") else "",
    }
    if type == "tv":
        base.update({
            "title":         d.get("name", ""),
            "year":          (d.get("first_air_date") or "")[:4],
            "total_seasons": d.get("number_of_seasons", 1),
        })
    else:
        base.update({
            "title":   d.get("title", ""),
            "year":    (d.get("release_date") or "")[:4],
            "runtime": d.get("runtime", 0),
        })
    return base


# ── Season episodes ───────────────────────────────────────────────────────────

@app.get("/api/season", dependencies=[Depends(verify)])
async def season(tmdb_id: int, s: int):
    d = await tmdb_get(f"/tv/{tmdb_id}/season/{s}")
    return [
        {
            "episode":  ep["episode_number"],
            "title":    ep.get("name", ""),
            "overview": ep.get("overview", ""),
            "rating":   round(ep.get("vote_average", 0), 1),
            "air_date": ep.get("air_date", ""),
        }
        for ep in d.get("episodes", [])
    ]

@app.get("/api/still", dependencies=[Depends(verify)])
async def get_still(tmdb_id: int, season: int, episode: int):
    """Fetch episode still path from TMDB, return image proxied through server."""
    data = await tmdb_get(f"/tv/{tmdb_id}/season/{season}/episode/{episode}")
    still_path = data.get("still_path")
    if not still_path:
        raise HTTPException(404, "No still available")
    img_url = f"https://image.tmdb.org/t/p/w300{still_path}"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(img_url)
        r.raise_for_status()
    return Response(content=r.content, media_type=r.headers.get("content-type", "image/jpeg"),
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/poster", dependencies=[Depends(verify)])
async def get_poster(tmdb_id: int, type: str, size: str = "w342"):
    """Proxy a poster image through the server."""
    if size not in ("w92", "w342", "w780"):
        raise HTTPException(400, "Invalid size")
    d = await tmdb_get(f"/{type}/{tmdb_id}")
    poster_path = d.get("poster_path")
    if not poster_path:
        raise HTTPException(404, "No poster available")
    img_url = f"https://image.tmdb.org/t/p/{size}{poster_path}"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(img_url)
        r.raise_for_status()
    return Response(content=r.content, media_type=r.headers.get("content-type", "image/jpeg"),
                    headers={"Cache-Control": "public, max-age=86400"})

# ── M3U8 extraction via Playwright ────────────────────────────────────────────

async def extract_m3u8(imdb_id: str, season: int, episode: int, media_type: str = "tv") -> Optional[str]:
    if not imdb_id:
        raise HTTPException(422, "No IMDB ID available for this title")

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
        log("Could not find iframe URL")
        return None

    from playwright.async_api import async_playwright
    m3u8_url = None

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            **({"proxy": {"server": TOR_PROXY}} if TOR_PROXY else {})
        )
        page = await context.new_page()
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
            log(f"Navigation warning: {e}")

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


# ── Segment downloader ────────────────────────────────────────────────────────

async def _fetch_segment(client: httpx.AsyncClient, url: str, dest: Path, retries: int = 6) -> None:
    for attempt in range(retries):
        try:
            async with client.stream("GET", url, headers=SEG_HEADERS, timeout=60) as r:
                if r.status_code == 429:
                    wait = int(r.headers.get("retry-after", 2 ** attempt))
                    log(f"429 rate limit, waiting {wait}s")
                    await asyncio.sleep(wait)
                    continue
                r.raise_for_status()
                with open(dest, "wb") as f:
                    async for chunk in r.aiter_bytes(65536):
                        f.write(chunk)
            return
        except Exception as e:
            if attempt == retries - 1:
                raise RuntimeError(f"Segment failed after {retries} attempts: {url} — {e}")
            wait = 2 ** attempt
            log(f"Segment retry {attempt+1}/{retries} in {wait}s: {e}")
            await asyncio.sleep(wait)


# ── ffmpeg: segments → mux → stream to browser ───────────────────────────────

async def _ffmpeg_stream(m3u8_url: str, fname: str) -> StreamingResponse:

    # 1. Fetch master playlist
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get(m3u8_url, headers=CDN_HEADERS)
        r.raise_for_status()
        master_text = r.text

    # 2. Resolve best-quality media playlist
    media_url = m3u8_url
    if "#EXT-X-STREAM-INF" in master_text:
        best_bw, best_url = -1, None
        lines = master_text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF"):
                m = re.search(r"BANDWIDTH=(\d+)", line)
                bw  = int(m.group(1)) if m else 0
                nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
                if bw > best_bw and nxt and not nxt.startswith("#"):
                    best_bw, best_url = bw, nxt
        if best_url:
            media_url = best_url if best_url.startswith("http") else \
                        urllib.parse.urljoin(m3u8_url, best_url)

    # 3. Fetch media playlist
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get(media_url, headers=CDN_HEADERS)
        r.raise_for_status()
        media_text = r.text

    seg_urls = [
        (line.strip() if line.strip().startswith("http") else
         urllib.parse.urljoin(media_url, line.strip()))
        for line in media_text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not seg_urls:
        raise RuntimeError("No segments found in playlist")

    log(f"Downloading {len(seg_urls)} segments → {fname}")

    # 4. Download all segments into tempdir
    tmpdir = Path(tempfile.mkdtemp(prefix="grab_"))
    try:
        async with httpx.AsyncClient(
            timeout=60,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=3, max_keepalive_connections=3),
        ) as client:
            seg_paths = []
            for i, url in enumerate(seg_urls):
                dest = tmpdir / f"seg{i:05d}.ts"
                await _fetch_segment(client, url, dest)
                seg_paths.append(dest)
                if (i + 1) % 10 == 0:
                    log(f"  {i+1}/{len(seg_urls)} segments")
                await asyncio.sleep(0.05)

        # 5. Write local playlist pointing at temp files
        playlist = tmpdir / "playlist.m3u8"
        with open(playlist, "w") as f:
            f.write("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:10\n")
            for p in seg_paths:
                f.write(f"#EXTINF:10.0,\n{p}\n")
            f.write("#EXT-X-ENDLIST\n")

        # 6. ffmpeg mux → pipe:1 → browser
        cmd = [
            "ffmpeg", "-y",
            "-allowed_extensions", "ALL",
            "-protocol_whitelist", "file,crypto,data,https,http,tcp,tls",
            "-i",        str(playlist),
            "-c",        "copy",
            "-bsf:a",    "aac_adtstoasc",
            "-movflags", "frag_keyframe+empty_moov+faststart",
            "-f",        "mp4",
            "pipe:1",
        ]

        log(f"ffmpeg mux → {fname}")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def _chunks() -> AsyncGenerator[bytes, None]:
            try:
                while True:
                    chunk = await proc.stdout.read(65536)
                    if not chunk:
                        break
                    yield chunk
            finally:
                try:
                    proc.kill()
                except Exception:
                    pass
                await proc.wait()
                try:
                    err = (await proc.stderr.read()).decode(errors="replace")[-800:]
                    if proc.returncode not in (0, -9):
                        log(f"ffmpeg stderr: {err}")
                except Exception:
                    pass
                log(f"ffmpeg done → {fname} rc={proc.returncode}")
                shutil.rmtree(tmpdir, ignore_errors=True)

        return StreamingResponse(
            _chunks(),
            media_type="video/mp4",
            headers={
                "Content-Disposition": f'attachment; filename="{fname}"',
                "Cache-Control": "no-store",
            },
        )

    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise


# ── Download endpoints ────────────────────────────────────────────────────────

@app.get("/api/download/tv", dependencies=[Depends(verify)])
async def download_tv(imdb: str, season: int, episode: int):
    log(f"Download TV: {imdb} S{season:02d}E{episode:02d}")
    m3u8 = await extract_m3u8(imdb, season, episode, "tv")
    if not m3u8:
        raise HTTPException(404, "Could not extract M3U8")
    return await _ffmpeg_stream(m3u8, f"{imdb}_S{season:02d}E{episode:02d}.mp4")


@app.get("/api/download/movie", dependencies=[Depends(verify)])
async def download_movie(imdb: str):
    log(f"Download movie: {imdb}")
    m3u8 = await extract_m3u8(imdb, 0, 0, "movie")
    if not m3u8:
        raise HTTPException(404, "Could not extract M3U8")
    return await _ffmpeg_stream(m3u8, f"{imdb}.mp4")


# ── Subtitles ─────────────────────────────────────────────────────────────────

class _OSTransport(xmlrpc.client.SafeTransport):
    def send_headers(self, connection, headers):
        connection.putheader("User-Agent", "cine2.0 v1")


@app.get("/api/subtitle", dependencies=[Depends(verify)])
async def subtitle(imdb: str, season: int = 0, episode: int = 0):
    try:
        os_proxy = xmlrpc.client.ServerProxy(
            "https://api.opensubtitles.org/xml-rpc",
            transport=_OSTransport()
        )
        r = os_proxy.LogIn("", "", "en", "cine2.0 v1")
        if not r.get("status", "").startswith("200"):
            raise HTTPException(404, "OpenSubtitles login failed")
        token = r["token"]
        params = {"imdbid": imdb.lstrip("t"), "sublanguageid": "eng"}
        if season:
            params["season"]  = str(season)
            params["episode"] = str(episode)
        results = os_proxy.SearchSubtitles(token, [params])
        subs    = results.get("data")
        os_proxy.LogOut(token)
        if not subs:
            raise HTTPException(404, "No subtitles found")
        dl_url = subs[0]["SubDownloadLink"]
        req = urllib.request.Request(dl_url, headers={"User-Agent": "cine2.0 v1"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            srt_bytes = gzip.decompress(resp.read())
        fname = f"{imdb}_S{season:02d}E{episode:02d}.srt" if season else f"{imdb}.srt"
        return Response(
            content=srt_bytes,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'}
        )
    except HTTPException:
        raise
    except Exception as e:
        log(f"Subtitle error: {e}")
        raise HTTPException(404, "Subtitle fetch failed")


# ── Health + UI ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/", response_class=HTMLResponse)
async def ui():
    return open("ui.html").read()

if __name__ == "__main__":
    import uvicorn
    PORT = int(os.environ.get("PORT", 9090))
    uvicorn.run(app, host="0.0.0.0", port=PORT)