#!/data/data/com.termux/files/usr/bin/bash
# grab-agent installer for Termux (Android)
# Usage: bash install.sh

set -e

AGENT_URL="https://raw.githubusercontent.com/yusuf-bot/grab/main/phone/agent.py"
OUTPUT_DIR="/sdcard/Download"
AGENT_PORT=9090
LOG_DIR="$HOME/.grab"
AGENT_LOG="$LOG_DIR/agent.log"
CF_LOG="$LOG_DIR/cloudflare.log"

# ── Colours ───────────────────────────────────────────────────────────────────
R="\033[31m"; G="\033[32m"; Y="\033[33m"; C="\033[36m"; B="\033[1m"; RST="\033[0m"
ok()   { echo -e "  ${G}✓${RST} $1"; }
err()  { echo -e "  ${R}✗${RST} $1"; exit 1; }
info() { echo -e "  ${C}·${RST} $1"; }
hdr()  { echo -e "\n${B}${C}  $1${RST}\n  $(printf '─%.0s' $(seq 1 ${#1}))"; }

# ── Check we're in Termux ─────────────────────────────────────────────────────
hdr "grab-agent installer"
[ -d "/data/data/com.termux" ] || err "This script must run in Termux"
mkdir -p "$LOG_DIR"

# ── Update packages ───────────────────────────────────────────────────────────
hdr "Updating packages"
pkg update -y -o Dpkg::Options::="--force-confold" 2>/dev/null | tail -3
ok "Packages updated"

# ── Install dependencies ──────────────────────────────────────────────────────
hdr "Installing dependencies"

install_pkg() {
    if ! command -v "$2" &>/dev/null; then
        info "Installing $1…"
        pkg install -y "$1" 2>/dev/null | tail -1
        ok "$1 installed"
    else
        ok "$1 already installed"
    fi
}

install_pkg python       python3
install_pkg ffmpeg       ffmpeg
install_pkg cloudflared  cloudflared

# ── Storage access ────────────────────────────────────────────────────────────
hdr "Storage access"
if [ ! -d "$HOME/storage" ]; then
    info "Requesting storage permission…"
    termux-setup-storage
    sleep 3
fi

if [ ! -d "/sdcard/Download" ]; then
    err "Could not access /sdcard/Download — please allow storage permission and re-run"
fi
ok "Storage access OK → $OUTPUT_DIR"

# ── Download agent.py ─────────────────────────────────────────────────────────
hdr "Installing grab-agent"

# If agent.py is in the same folder as this script, use it directly
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/agent.py" ]; then
    cp "$SCRIPT_DIR/agent.py" "$PREFIX/bin/grab-agent"
    ok "Copied agent.py from local directory"
else
    info "Downloading agent.py…"
    curl -fsSL "$AGENT_URL" -o "$PREFIX/bin/grab-agent" \
        || err "Failed to download agent.py — check AGENT_URL in this script or place agent.py next to install.sh"
    ok "Downloaded agent.py"
fi

chmod +x "$PREFIX/bin/grab-agent"
ok "grab-agent installed → $PREFIX/bin/grab-agent"

# ── Stop any existing instances ───────────────────────────────────────────────
hdr "Stopping existing instances"
pkill -f grab-agent   2>/dev/null && info "Stopped old grab-agent"  || true
pkill -f cloudflared  2>/dev/null && info "Stopped old cloudflared" || true
# Stop runsv-managed versions if they exist
[ -d "$PREFIX/var/service/grab-agent" ]  && { touch "$PREFIX/var/service/grab-agent/down";  sv down grab-agent  2>/dev/null || true; }
[ -d "$PREFIX/var/service/cloudflared" ] && { touch "$PREFIX/var/service/cloudflared/down"; sv down cloudflared 2>/dev/null || true; }
sleep 2
ok "Clean slate"

# ── Start grab-agent in background ───────────────────────────────────────────
hdr "Starting grab-agent"
nohup env OUTPUT_DIR="$OUTPUT_DIR" AGENT_PORT="$AGENT_PORT" grab-agent \
    > "$AGENT_LOG" 2>&1 &
AGENT_PID=$!
sleep 2

if kill -0 "$AGENT_PID" 2>/dev/null; then
    ok "grab-agent running (PID $AGENT_PID) on port $AGENT_PORT"
    ok "Output → $OUTPUT_DIR"
else
    err "grab-agent failed to start. Check log: cat $AGENT_LOG"
fi

# ── Start cloudflared tunnel ──────────────────────────────────────────────────
hdr "Starting Cloudflare tunnel"
info "Requesting tunnel URL… (takes ~10s)"
nohup cloudflared tunnel --url "http://localhost:$AGENT_PORT" \
    > "$CF_LOG" 2>&1 &
CF_PID=$!

# Wait for URL to appear in log (up to 30s)
CF_URL=""
for i in $(seq 1 30); do
    sleep 1
    CF_URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$CF_LOG" 2>/dev/null | head -1)
    [ -n "$CF_URL" ] && break
done

if [ -z "$CF_URL" ]; then
    err "Cloudflare tunnel failed. Check log: cat $CF_LOG"
fi

ok "Tunnel active: $CF_URL"

# ── Set up auto-start on boot ─────────────────────────────────────────────────
hdr "Setting up auto-start"

# Check if Termux:Boot is available
BOOT_DIR="$HOME/.termux/boot"
mkdir -p "$BOOT_DIR"

cat > "$BOOT_DIR/grab.sh" << BOOTEOF
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
sleep 5
touch $PREFIX/var/service/grab-agent/down  2>/dev/null || true
touch $PREFIX/var/service/cloudflared/down 2>/dev/null || true
pkill -f grab-agent  2>/dev/null || true
pkill -f cloudflared 2>/dev/null || true
sleep 2
nohup env OUTPUT_DIR=$OUTPUT_DIR AGENT_PORT=$AGENT_PORT grab-agent > $AGENT_LOG 2>&1 &
sleep 3
nohup cloudflared tunnel --url http://localhost:$AGENT_PORT > $CF_LOG 2>&1 &
BOOTEOF

chmod +x "$BOOT_DIR/grab.sh"
ok "Auto-start script → $BOOT_DIR/grab.sh"

# ── Save tunnel URL for easy access ──────────────────────────────────────────
echo "$CF_URL" > "$LOG_DIR/tunnel_url.txt"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${B}${G}  ✓ All done!${RST}"
echo ""
echo -e "  ${B}Agent URL (paste into web UI):${RST}"
echo -e "  ${C}${B}  $CF_URL${RST}"
echo ""
echo -e "  ${Y}Note:${RST} This URL changes if the tunnel restarts."
echo -e "  To get the current URL anytime:"
echo -e "  ${C}  cat $LOG_DIR/tunnel_url.txt${RST}"
echo -e "  or:"
echo -e "  ${C}  grep trycloudflare.com $CF_LOG | tail -1${RST}"
echo ""
echo -e "  ${Y}Important:${RST} Install ${B}Termux:Boot${RST} from F-Droid for auto-start on reboot."
echo ""