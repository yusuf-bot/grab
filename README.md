# grab

A self-hosted TV show and movie downloader with a clean mobile web UI. Search by name, browse seasons and episodes, and download directly to your phone or computer — all from a browser.

---

## How it works

```
┌─────────────────────────────────────────────────────────────┐
│  Your Browser  →  grab.yourdomain.com                       │
│                   (web UI served by VPS)                    │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           │  1. Search OMDB for title
                           │  2. Extract M3U8 stream via Playwright
                           │  3. Send stream URL to your device
                           │
┌──────────────────────────▼──────────────────────────────────┐
│  Your Device (phone / PC)                                   │
│  agent.py — local HTTP server exposed via Cloudflare tunnel │
│                                                             │
│  • Downloads video with ffmpeg                              │
│  • Fetches English subtitles from OpenSubtitles             │
│  • Zips .mp4 + .srt → saves to Downloads folder            │
└─────────────────────────────────────────────────────────────┘
```

The VPS never stores any video. It only extracts the stream URL and passes it to your device. All downloading happens locally on your device.

---

## Features

- 🔍 **Search** TV shows and movies by name via OMDB
- 📺 **Browse** seasons and episodes with ratings and air dates
- ⬇️ **Download** any episode or movie in 720p
- 📝 **Subtitles** fetched automatically from OpenSubtitles (English)
- 🗜️ **Zipped output** — `.mp4` + `.srt` bundled into a single `.zip`
- 📱 **Mobile-first UI** — works great on phone, installable as a PWA
- 🔒 **API key protected** — only you can use it
- 🌐 **Works anywhere** — phone agent exposed via Cloudflare tunnel

---

## Repository structure

```
grab/
├── server/                  # Runs on your VPS
│   ├── server.py            # FastAPI backend
│   ├── ui.html              # Web UI (served by server.py)
│   ├── Dockerfile           # For Docker / Coolify deployment
│   └── requirements.txt
│
└── client/                  # Runs on your phone or PC
    ├── agent.py             # Local download agent
    ├── install-android.sh   # One-command setup for Android (Termux)
    └── install-linux.sh     # One-command setup for Linux / macOS
```

---

## Dependencies

### VPS (server)

| Dependency | Version | Purpose |
|---|---|---|
| Python | 3.12+ | Runtime |
| FastAPI | ≥ 0.110 | HTTP API framework |
| Uvicorn | ≥ 0.29 | ASGI server |
| Playwright | ≥ 1.44 | Headless Chromium for M3U8 extraction |
| Chromium | latest | Browser engine used by Playwright |

All Python dependencies are in `server/requirements.txt`. Chromium is installed automatically by the Dockerfile.

### Client (your device)

| Dependency | Purpose | Auto-installed by script |
|---|---|---|
| Python 3 | Runs agent.py | ✅ |
| ffmpeg | Downloads and muxes video | ✅ |
| cloudflared | Exposes agent via HTTPS tunnel | ✅ |

### External APIs (free)

| API | Purpose | Sign up |
|---|---|---|
| [OMDB API](https://www.omdbapi.com/) | Movie/show search and metadata | Free tier: 1000 req/day |
| [OpenSubtitles](https://www.opensubtitles.org/) | Subtitle download | No account needed (anonymous XML-RPC) |
| [Cloudflare Tunnel](https://www.cloudflare.com/) | HTTPS tunnel for phone agent | Free, no domain needed |

---

## Part 1 — VPS Setup

### Option A: Docker / Coolify (recommended)

1. **Fork or clone this repo to GitHub**

2. **In Coolify** → New Resource → Public Repository → paste your repo URL

3. Set the following:
   - **Root Directory:** `server`
   - **Dockerfile path:** `server/Dockerfile`

4. Add **environment variables:**
   ```
   API_KEY=your_secret_key_here
   OMDB_KEY=your_omdb_api_key_here
   ```
   Get a free OMDB key at [omdbapi.com/apikey.aspx](https://www.omdbapi.com/apikey.aspx)

5. Set your **domain** (e.g. `grab.yourdomain.com`) — Coolify + Traefik handle SSL automatically

6. Hit **Deploy**

---

### Option B: Manual (PM2 / screen)

```bash
# Clone the repo
git clone https://github.com/yusuf-bot/grab
cd grab/server

# Install Python deps
pip install -r requirements.txt

# Install Chromium for Playwright
playwright install chromium
playwright install-deps chromium

# Set environment variables
export API_KEY=your_secret_key
export OMDB_KEY=your_omdb_key

# Run with PM2
pm2 start "uvicorn server:app --host 0.0.0.0 --port 8000" --name grab
pm2 save
pm2 startup
```

Or with screen:
```bash
screen -S grab
uvicorn server:app --host 0.0.0.0 --port 8000
# Ctrl+A then D to detach
```

---

## Part 2 — Client Setup (your device)

### Android — Termux (one command)

1. Install [Termux](https://f-droid.org/packages/com.termux/) from **F-Droid** (not Play Store)
2. Install [Termux:Boot](https://f-droid.org/packages/com.termux.boot/) from F-Droid for auto-start on reboot
3. Open Termux and run:

```bash
curl -fsSL https://raw.githubusercontent.com/yusuf-bot/grab/main/client/install-android.sh | bash
```

The script will:
- Install Python, ffmpeg, and cloudflared
- Set up storage access (`/sdcard/Download`)
- Install and start the agent in the background
- Start a Cloudflare tunnel and print your tunnel URL
- Set up auto-start on reboot via Termux:Boot

---

### Linux / macOS (one command)

```bash
curl -fsSL https://raw.githubusercontent.com/yusuf-bot/grab/main/client/install-linux.sh | bash
```

The script will:
- Detect your OS and install ffmpeg and cloudflared
- Install the agent to `~/.local/bin`
- Start both services in the background
- Set up systemd user services for auto-start (Linux)
- Print your tunnel URL

Default output directory is `~/Downloads`. The script will ask if you want to change it.

---

### Manual setup (any platform)

```bash
# Download the agent
curl -fsSL https://raw.githubusercontent.com/yusuf-bot/grab/main/client/agent.py -o agent.py

# Install ffmpeg
# Android (Termux): pkg install ffmpeg
# Ubuntu/Debian:    sudo apt install ffmpeg
# macOS:            brew install ffmpeg

# Run the agent
OUTPUT_DIR=/path/to/downloads python3 agent.py

# In another terminal, start the Cloudflare tunnel
cloudflared tunnel --url http://localhost:9090
```

---

## Part 3 — Connect the web UI to your device

1. Open your grab URL in a browser
2. Enter your `API_KEY` when prompted — saved in the browser for future visits
3. Tap the **●** dot in the top-right corner
4. Paste your **Cloudflare tunnel URL** (printed at the end of the install script)
   - Example: `https://something.trycloudflare.com`
5. The dot turns **green** when the agent is reachable

> **Finding your tunnel URL later:**
> ```bash
> cat ~/.grab/tunnel_url.txt
> ```

---

## Usage

### Downloading a TV show

1. Type the show name in the search bar → hit **Go**
2. Pick the show from results
3. Pick a season
4. Tap episodes to select them (tap multiple for batch download)
5. Tap **Download**
6. Switch to the **Downloads** tab to watch progress

### Downloading a movie

1. Search for the movie name
2. Pick it from results
3. Review the details → tap **Download**

### Output files

Files are saved to `/sdcard/Download` (Android) or `~/Downloads` (Linux):

```
tt0108778_S01E01.zip
  ├── tt0108778_S01E01.mp4   ← 720p video
  └── tt0108778_S01E01.srt   ← English subtitles (if found)
```

### Add to home screen (PWA)

On Android Chrome: tap `⋮` → **Add to Home Screen** — opens fullscreen like a native app.

---

## Environment variables

### Server

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | ✅ | `changeme123` | Secret key for web UI auth |
| `OMDB_KEY` | ✅ | — | OMDB API key |
| `TOR_PROXY` | ❌ | — | SOCKS5 proxy e.g. `socks5://127.0.0.1:9050` |

### Client

| Variable | Default | Description |
|---|---|---|
| `OUTPUT_DIR` | `/sdcard/Download` or `~/Downloads` | Where to save downloads |
| `AGENT_PORT` | `9090` | Local port the agent listens on |

---

## API reference

All server endpoints require the `x-api-key` header.

### Server

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Web UI |
| `GET` | `/health` | Health check |
| `GET` | `/api/search?q=` | Search OMDB |
| `GET` | `/api/detail?imdb=` | Get title details |
| `GET` | `/api/season?imdb=&s=` | Get episode list for a season |
| `GET` | `/api/m3u8/tv?imdb=&season=&episode=` | Extract M3U8 for a TV episode |
| `GET` | `/api/m3u8/movie?imdb=` | Extract M3U8 for a movie |

### Agent (on your device)

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `POST` | `/download` | Queue a download job |
| `GET` | `/status/<job_id>` | Get job status |
| `GET` | `/jobs` | List all jobs |

---

## Troubleshooting

**Agent dot shows offline**
- Check agent is running: `ps aux | grep grab-agent`
- Check tunnel is running: `ps aux | grep cloudflared`
- Get current tunnel URL: `cat ~/.grab/tunnel_url.txt`
- Free Cloudflare tunnel URLs change on restart — re-paste the new URL in the UI

**Port 9090 already in use**
```bash
pkill -f grab-agent
pkill -f cloudflared
sleep 2
nohup env OUTPUT_DIR=/sdcard/Download grab-agent > ~/.grab/agent.log 2>&1 &
nohup cloudflared tunnel --url http://localhost:9090 > ~/.grab/cloudflare.log 2>&1 &
```

**Download fails — no stream found**
- Try again — M3U8 extraction can occasionally need a retry
- Check server logs for Playwright/Chromium errors

**No subtitles in zip**
- OpenSubtitles may not have English subs for that episode
- The zip still contains the video — subtitles are best-effort only

**Permanent tunnel URL**
The free tunnel gives a random URL each run. For a permanent URL, sign up for a free Cloudflare account:
```bash
cloudflared tunnel login
cloudflared tunnel create grab-agent
```
Then follow the [Cloudflare tunnel docs](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/).

---

## License

MIT