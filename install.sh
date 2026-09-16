#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
#  install.sh — Ubuntu/Debian VPS Setup for GW Bulk Provisioner
#
#  Usage:  chmod +x install.sh && sudo ./install.sh
#
#  ⚠  This script ONLY installs dependencies for this project.
#     It does NOT touch any existing services, containers, or data.
# ─────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "  ${GREEN}✔${RESET}  $*"; }
warn() { echo -e "  ${YELLOW}⚠${RESET}  $*"; }
err()  { echo -e "  ${RED}✘${RESET}  $*"; exit 1; }
info() { echo -e "  ${CYAN}ℹ${RESET}  $*"; }

# ── Root check ───────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    err "Please run as root:  sudo ./install.sh"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_USER="${SUDO_USER:-$(whoami)}"

echo -e "\n${BOLD}═══════════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}  GW Bulk User Provisioner — VPS Installer${RESET}"
echo -e "${BOLD}═══════════════════════════════════════════════════════${RESET}\n"
info "Project directory : $SCRIPT_DIR"
info "Installing for user: $PROJECT_USER"
echo

# ── 1. System packages ───────────────────────────────────────────
echo -e "${BOLD}[1/6] Installing system packages…${RESET}"
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    curl git xclip xdotool \
    > /dev/null
ok "System packages installed."

# ── 2. Python version check ──────────────────────────────────────
echo -e "\n${BOLD}[2/6] Checking Python version…${RESET}"
PY_VERSION=$(python3 --version | awk '{print $2}')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [[ $PY_MAJOR -lt 3 || ($PY_MAJOR -eq 3 && $PY_MINOR -lt 9) ]]; then
    err "Python 3.9+ required. Found: $PY_VERSION"
fi
ok "Python $PY_VERSION — OK"

# ── 3. Virtual environment ───────────────────────────────────────
echo -e "\n${BOLD}[3/6] Setting up virtual environment…${RESET}"
VENV_DIR="$SCRIPT_DIR/venv"

if [[ -d "$VENV_DIR" ]]; then
    warn "Virtual environment already exists at $VENV_DIR — skipping creation."
else
    sudo -u "$PROJECT_USER" python3 -m venv "$VENV_DIR"
    ok "Virtual environment created: $VENV_DIR"
fi

# ── 4. Python dependencies ───────────────────────────────────────
echo -e "\n${BOLD}[4/6] Installing Python dependencies…${RESET}"
sudo -u "$PROJECT_USER" "$VENV_DIR/bin/pip" install --quiet --upgrade pip
sudo -u "$PROJECT_USER" "$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"
ok "Python packages installed."

# ── 5. Directory permissions ─────────────────────────────────────
echo -e "\n${BOLD}[5/6] Configuring secure directory permissions…${RESET}"

mkdir -p "$SCRIPT_DIR/logs" "$SCRIPT_DIR/exports"

# Directories readable only by owner
chmod 700 "$SCRIPT_DIR/logs" "$SCRIPT_DIR/exports"
chown -R "$PROJECT_USER:$PROJECT_USER" "$SCRIPT_DIR/logs" "$SCRIPT_DIR/exports"

# If credential files exist, lock them down
for f in credentials.json token.json .env; do
    TARGET="$SCRIPT_DIR/$f"
    if [[ -f "$TARGET" ]]; then
        chmod 600 "$TARGET"
        chown "$PROJECT_USER:$PROJECT_USER" "$TARGET"
        ok "Secured: $f (600)"
    fi
done
ok "Directory permissions set."

# ── 6. Environment file ──────────────────────────────────────────
echo -e "\n${BOLD}[6/6] Environment configuration…${RESET}"
ENV_FILE="$SCRIPT_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    sudo -u "$PROJECT_USER" cp "$SCRIPT_DIR/.env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    ok ".env created from .env.example — edit it to set WORKSPACE_DOMAIN."
else
    warn ".env already exists — not overwritten."
fi

# ── Summary ──────────────────────────────────────────────────────
echo -e "\n${BOLD}═══════════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}  Installation complete!${RESET}"
echo -e "${BOLD}═══════════════════════════════════════════════════════${RESET}"
echo
echo -e "  ${BOLD}Next steps:${RESET}"
echo -e "  1. Place your ${CYAN}credentials.json${RESET} in: $SCRIPT_DIR/"
echo -e "     ${YELLOW}chmod 600 $SCRIPT_DIR/credentials.json${RESET}"
echo -e "  2. Edit ${CYAN}.env${RESET}:"
echo -e "     ${YELLOW}nano $SCRIPT_DIR/.env${RESET}"
echo -e "  3. Run the provisioner:"
echo -e "     ${YELLOW}./run.sh${RESET}"
echo -e "     or"
echo -e "     ${YELLOW}$VENV_DIR/bin/python main.py${RESET}"
echo

# ── Optional: systemd service ─────────────────────────────────────
read -rp "  Create systemd on-demand service? (y/N): " CREATE_SERVICE
if [[ "${CREATE_SERVICE,,}" == "y" ]]; then
    SERVICE_FILE="/etc/systemd/system/gw-provisioner.service"
    cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Google Workspace Bulk User Provisioner
After=network.target

[Service]
Type=oneshot
User=$PROJECT_USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=$VENV_DIR/bin/python $SCRIPT_DIR/main.py
StandardInput=tty
TTYPath=/dev/tty0
TTYReset=yes
TTYVHangup=yes
RemainAfterExit=no

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    ok "Service created: gw-provisioner.service"
    info "Run with: sudo systemctl start gw-provisioner"
fi

echo -e "\n${GREEN}Setup finished!${RESET}\n"
