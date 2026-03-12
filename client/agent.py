#!/usr/bin/env python3
"""
grab-agent — runs on your phone in Termux
Listens on localhost:9090 for download commands from the web UI.

Setup:
    cp agent.py $PREFIX/bin/grab-agent
    chmod +x $PREFIX/bin/grab-agent
    grab-agent          ← run this in Termux, keep it open

The web UI will connect to your phone via this agent.
Your phone and the web UI browser must be on the same network,
OR you can expose it via a tunnel (see README).
"""

import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import xmlrpc.client
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# ── Load .env file if present ────────────────────────────────────────────────
def _load_env(path=None):
    candidates = [
        path,
        Path(__file__).parent / ".env",
        Path.home() / ".grab" / ".env",
    ]
    for p in candidates:
        if p and Path(p).exists():
            for line in Path(p).read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                val = val.strip().strip('"').strip("'")
                os.environ.setdefault(key.strip(), val)
            break

_load_env()

# ── Config ────────────────────────────────────────────────────────────────────
PORT       = int(os.environ.get("AGENT_PORT", 9090))

_default_out = (
    "/sdcard/Download"
    if os.path.exists("/sdcard")
    else str(Path.home() / "Downloads")
)
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", _default_out))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Active jobs: id → {status, label, progress, file, error}
jobs: dict = {}


# ── Logging ───────────────────────────────────────────────────────────────────

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ── OpenSubtitles ─────────────────────────────────────────────────────────────

class _OSTransport(xmlrpc.client.SafeTransport):
    def send_headers(self, connection, headers):
        connection.putheader("User-Agent", "cine2.0 v1")

def fetch_subtitle(imdb_id: str, season: int, episode: int, out_srt: Path):
    try:
        proxy = xmlrpc.client.ServerProxy(
            "https://api.opensubtitles.org/xml-rpc",
            transport=_OSTransport()
        )
        r = proxy.LogIn("", "", "en", "cine2.0 v1")
        if not r.get("status", "").startswith("200"):
            return
        token = r["token"]
        results = proxy.SearchSubtitles(token, [{
            "imdbid":        imdb_id.lstrip("t"),
            "sublanguageid": "eng",
            "season":        str(season),
            "episode":       str(episode),
        }])
        subs = results.get("data")
        proxy.LogOut(token)
        if not subs:
            return
        dl_url = subs[0]["SubDownloadLink"]
        req = urllib.request.Request(dl_url, headers={"User-Agent": "cine2.0 v1"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            gz_data = resp.read()
        out_srt.write_bytes(gzip.decompress(gz_data))
        log(f"✓ Subtitle: {out_srt.name}")
    except Exception as e:
        log(f"Subtitle failed: {e}")


# ── Downloader ────────────────────────────────────────────────────────────────

def run_download(job_id: str, payload: dict):
    job      = jobs[job_id]
    m3u8     = payload["m3u8"]
    headers  = payload.get("headers", {})
    imdb_id  = payload["imdb"]
    season   = int(payload.get("season", 0))
    episode  = int(payload.get("episode", 0))
    label    = payload.get("label", imdb_id)
    is_tv    = payload.get("type", "tv") == "tv"

    stem     = f"{imdb_id}_S{season:02d}E{episode:02d}" if is_tv else imdb_id
    mp4_file = OUTPUT_DIR / f"{stem}.mp4"
    srt_file = OUTPUT_DIR / f"{stem}.srt"
    zip_file = OUTPUT_DIR / f"{stem}.zip"

    job["status"]   = "downloading"
    job["progress"] = "Starting download…"
    log(f"Downloading {label} → {mp4_file.name}")

    # Start subtitle fetch in background
    sub_thread = threading.Thread(
        target=fetch_subtitle,
        args=(imdb_id, season, episode, srt_file),
        daemon=True
    )
    if is_tv:
        sub_thread.start()

    # Build ffmpeg command
    referer = headers.get("Referer",    "https://cloudnestra.com/")
    origin  = headers.get("Origin",     "https://cloudnestra.com")
    ua      = headers.get("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    header_str = f"Referer: {referer}\r\nOrigin: {origin}\r\nUser-Agent: {ua}\r\n"

    # Try N_m3u8DL-RE first
    n_tool = shutil.which("N_m3u8DL-RE") or shutil.which("n_m3u8dl-re")
    ok = False

    if n_tool:
        job["progress"] = "Downloading (N_m3u8DL-RE)…"
        cmd = [
            n_tool, m3u8,
            "--save-name", mp4_file.stem,
            "--save-dir",  str(OUTPUT_DIR),
            "--no-date-info", "--no-log",
            "--thread-count",         "64",
            "--download-retry-count", "20",
            "--concurrent-download",
            "--select-video", 'res="1280x720":for=best',
            "--select-audio", "codec=aac:for=best",
            "--header", f"Referer: {referer}",
            "--header", f"Origin: {origin}",
            "--header", f"User-Agent: {ua}",
        ]
        result = subprocess.run(cmd)
        for ext in [".mp4", ".mkv", ".ts"]:
            c = OUTPUT_DIR / f"{mp4_file.stem}{ext}"
            if c.exists() and c.stat().st_size > 100_000:
                if c != mp4_file:
                    c.rename(mp4_file)
                ok = True
                break
        if not ok:
            log("N_m3u8DL-RE failed, falling back to ffmpeg")

    if not ok:
        job["progress"] = "Downloading (ffmpeg)…"
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            job["status"] = "failed"
            job["error"]  = "No downloader found (install ffmpeg: pkg install ffmpeg)"
            return
        cmd = [
            ffmpeg, "-headers", header_str,
            "-i", m3u8, "-c", "copy",
            "-bsf:a", "aac_adtstoasc",
            str(mp4_file), "-y",
            "-loglevel", "warning", "-stats",
        ]
        result = subprocess.run(cmd)
        ok = result.returncode == 0 and mp4_file.exists() and mp4_file.stat().st_size > 100_000

    if not ok:
        job["status"] = "failed"
        job["error"]  = "Download failed — no output file produced"
        return

    # Wait for subtitle (max 3s)
    if is_tv:
        sub_thread.join(timeout=3)

    # Zip everything
    job["progress"] = "Zipping…"
    try:
        with zipfile.ZipFile(zip_file, "w", zipfile.ZIP_STORED) as zf:
            zf.write(mp4_file, mp4_file.name)
            if srt_file.exists():
                zf.write(srt_file, srt_file.name)
        mp4_file.unlink()
        if srt_file.exists():
            srt_file.unlink()
        size_mb = zip_file.stat().st_size / 1_048_576
        log(f"✓ Done: {zip_file.name} ({size_mb:.1f} MB)")
        job["status"]   = "done"
        job["progress"] = f"Done — {size_mb:.1f} MB"
        job["file"]     = str(zip_file)
    except Exception as e:
        job["status"] = "failed"
        job["error"]  = f"Zip failed: {e}"


# ── HTTP handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # silence default logging

    def _send(self, code: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"status": "ok"})

        elif self.path.startswith("/status/"):
            job_id = self.path.split("/")[-1]
            job    = jobs.get(job_id)
            if not job:
                self._send(404, {"error": "Job not found"})
            else:
                self._send(200, job)

        elif self.path == "/jobs":
            self._send(200, list(jobs.values()))

        else:
            self._send(404, {"error": "Not found"})

    def do_POST(self):
        if self.path == "/download":
            length  = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length))

            job = {
                "id":       str(__import__("uuid").uuid4()),
                "status":   "queued",
                "label":    payload.get("label", payload.get("imdb", "?")),
                "progress": "Queued",
                "file":     None,
                "error":    None,
                "created":  time.time(),
            }
            jobs[job["id"]] = job

            t = threading.Thread(target=run_download, args=(job["id"], payload), daemon=True)
            t.start()

            log(f"Job queued: {job['label']}")
            self._send(200, {"job_id": job["id"]})

        else:
            self._send(404, {"error": "Not found"})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    log(f"grab-agent listening on port {PORT}")
    log(f"Output dir: {OUTPUT_DIR}")
    log(f"Waiting for jobs from the web UI...")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Stopped.")