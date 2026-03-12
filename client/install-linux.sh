#!/bin/bash
# grab-agent installer for Linux desktop
# Usage: bash install-linux.sh

set -e

OUTPUT_DIR="$HOME/Downloads"
AGENT_PORT=9090
LOG_DIR="$HOME/.grab"
AGENT_LOG="$LOG_DIR/agent.log"
CF_LOG="$LOG_DIR/cloudflare.log"
INSTALL_BIN="$HOME/.local/bin"

# ── Colours ───────────────────────────────────────────────────────────────────
R="\033[31m"; G="\033[32m"; Y="\033[33m"; C="\033[36m"; B="\033[1m"; RST="\033[0m"
ok()   { echo -e "  ${G}✓${RST} $1"; }
err()  { echo -e "  ${R}✗${RST} $1"; exit 1; }
info() { echo -e "  ${C}·${RST} $1"; }
hdr()  { echo -e "\n${B}${C}  $1${RST}\n  $(printf '─%.0s' $(seq 1 ${#1}))"; }

hdr "grab-agent installer (Linux)"
mkdir -p "$LOG_DIR" "$INSTALL_BIN"

# ── Check OS ──────────────────────────────────────────────────────────────────
if [[ "$OSTYPE" == "darwin"* ]]; then
    PKG_INSTALL="brew install"
    PKG_CHECK() { brew list "$1" &>/dev/null; }
elif command -v apt-get &>/dev/null; then
    PKG_INSTALL="sudo apt-get install -y"
    PKG_CHECK() { dpkg -l "$1" &>/dev/null; }
elif command -v dnf &>/dev/null; then
    PKG_INSTALL="sudo dnf install -y"
    PKG_CHECK() { rpm -q "$1" &>/dev/null; }
elif command -v pacman &>/dev/null; then
    PKG_INSTALL="sudo pacman -S --noconfirm"
    PKG_CHECK() { pacman -Q "$1" &>/dev/null; }
else
    err "Unsupported OS. Install python3 and ffmpeg manually."
fi

# ── Python ────────────────────────────────────────────────────────────────────
hdr "Checking Python"
if command -v python3 &>/dev/null; then
    PY_VER=$(python3 --version 2>&1)
    ok "Python found: $PY_VER"
else
    info "Installing Python…"
    $PKG_INSTALL python3 || err "Failed to install Python"
    ok "Python installed"
fi

# ── ffmpeg ────────────────────────────────────────────────────────────────────
hdr "Checking ffmpeg"
if command -v ffmpeg &>/dev/null; then
    ok "ffmpeg already installed"
else
    info "Installing ffmpeg…"
    $PKG_INSTALL ffmpeg || err "Failed to install ffmpeg"
    ok "ffmpeg installed"
fi

# ── cloudflared ───────────────────────────────────────────────────────────────
hdr "Checking cloudflared"
if command -v cloudflared &>/dev/null; then
    ok "cloudflared already installed"
else
    info "Installing cloudflared…"
    ARCH=$(uname -m)
    CF_VERSION="2026.3.0"
    if [[ "$OSTYPE" == "darwin"* ]]; then
        brew install cloudflared
    elif [[ "$ARCH" == "x86_64" ]]; then
        curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64" \
            -o "$INSTALL_BIN/cloudflared"
        chmod +x "$INSTALL_BIN/cloudflared"
    elif [[ "$ARCH" == "aarch64" ]]; then
        curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64" \
            -o "$INSTALL_BIN/cloudflared"
        chmod +x "$INSTALL_BIN/cloudflared"
    else
        err "Unknown architecture: $ARCH — download cloudflared manually from https://github.com/cloudflare/cloudflared/releases"
    fi
    # Make sure INSTALL_BIN is in PATH
    export PATH="$INSTALL_BIN:$PATH"
    ok "cloudflared installed"
fi

# ── Install agent.py ──────────────────────────────────────────────────────────
hdr "Installing grab-agent"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$SCRIPT_DIR/agent.py" ]; then
    cp "$SCRIPT_DIR/agent.py" "$INSTALL_BIN/grab-agent"
    ok "Copied agent.py from local directory"
else
    info "Downloading agent.py…"
    curl -fsSL "https://raw.githubusercontent.com/yusuf-bot/grab/main/client/agent.py" \
        -o "$INSTALL_BIN/grab-agent" \
        || err "Failed to download agent.py — place agent.py next to this script"
    ok "Downloaded agent.py"
fi

chmod +x "$INSTALL_BIN/grab-agent"

# Make sure ~/.local/bin is in PATH
if [[ ":$PATH:" != *":$INSTALL_BIN:"* ]]; then
    echo "export PATH=\"$INSTALL_BIN:\$PATH\"" >> "$HOME/.bashrc"
    export PATH="$INSTALL_BIN:$PATH"
    info "Added $INSTALL_BIN to PATH in ~/.bashrc"
fi
ok "grab-agent installed → $INSTALL_BIN/grab-agent"

# ── Stop any existing instances ───────────────────────────────────────────────
hdr "Stopping existing instances"
pkill -f grab-agent  2>/dev/null && info "Stopped old grab-agent"  || true
pkill -f cloudflared 2>/dev/null && info "Stopped old cloudflared" || true
sleep 1
ok "Clean slate"

# ── Custom output dir ─────────────────────────────────────────────────────────
hdr "Output directory"
echo -e "  Default: ${C}$OUTPUT_DIR${RST}"
read -rp "  Press Enter to use default, or type a different path: " CUSTOM_DIR
if [ -n "$CUSTOM_DIR" ]; then
    OUTPUT_DIR="$CUSTOM_DIR"
fi
mkdir -p "$OUTPUT_DIR"
ok "Output → $OUTPUT_DIR"

# ── Start grab-agent ──────────────────────────────────────────────────────────
hdr "Starting grab-agent"
nohup env OUTPUT_DIR="$OUTPUT_DIR" AGENT_PORT="$AGENT_PORT" \
    python3 "$INSTALL_BIN/grab-agent" > "$AGENT_LOG" 2>&1 &
AGENT_PID=$!
sleep 2

if kill -0 "$AGENT_PID" 2>/dev/null; then
    ok "grab-agent running (PID $AGENT_PID) on port $AGENT_PORT"
else
    err "grab-agent failed to start. Check: cat $AGENT_LOG"
fi

# ── Start cloudflared ─────────────────────────────────────────────────────────
hdr "Starting Cloudflare tunnel"
info "Requesting tunnel URL… (takes ~10s)"
nohup cloudflared tunnel --url "http://localhost:$AGENT_PORT" \
    > "$CF_LOG" 2>&1 &

CF_URL=""
for i in $(seq 1 30); do
    sleep 1
    CF_URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$CF_LOG" 2>/dev/null | head -1)
    [ -n "$CF_URL" ] && break
    printf "."
done
echo ""

[ -z "$CF_URL" ] && err "Cloudflare tunnel failed. Check: cat $CF_LOG"
ok "Tunnel active: $CF_URL"
echo "$CF_URL" > "$LOG_DIR/tunnel_url.txt"

# ── systemd user service (auto-start) ────────────────────────────────────────
hdr "Setting up auto-start"
if command -v systemctl &>/dev/null && systemctl --user status &>/dev/null 2>&1; then
    SYSDIR="$HOME/.config/systemd/user"
    mkdir -p "$SYSDIR"

    cat > "$SYSDIR/grab-agent.service" << SVCEOF
[Unit]
Description=grab agent
After=network.target

[Service]
Environment=OUTPUT_DIR=$OUTPUT_DIR
Environment=AGENT_PORT=$AGENT_PORT
ExecStart=$INSTALL_BIN/grab-agent
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
SVCEOF

    cat > "$SYSDIR/grab-cloudflared.service" << SVCEOF
[Unit]
Description=grab cloudflared tunnel
After=grab-agent.service

[Service]
ExecStart=$(which cloudflared) tunnel --url http://localhost:$AGENT_PORT
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
SVCEOF

    systemctl --user daemon-reload
    systemctl --user enable grab-agent grab-cloudflared
    ok "systemd services enabled — will auto-start on login"
else
    # Fallback: add to .bashrc
    STARTUP="nohup env OUTPUT_DIR=$OUTPUT_DIR grab-agent > $AGENT_LOG 2>&1 & nohup cloudflared tunnel --url http://localhost:$AGENT_PORT > $CF_LOG 2>&1 &"
    grep -q "grab-agent" "$HOME/.bashrc" || echo "$STARTUP" >> "$HOME/.bashrc"
    ok "Added to ~/.bashrc (auto-starts on terminal open)"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${B}${G}  ✓ All done!${RST}"
echo ""
echo -e "  ${B}Agent URL (paste into web UI):${RST}"
echo -e "  ${C}${B}  $CF_URL${RST}"
echo ""
echo -e "  To get the URL anytime:"
echo -e "  ${C}  cat $LOG_DIR/tunnel_url.txt${RST}"
echo ""