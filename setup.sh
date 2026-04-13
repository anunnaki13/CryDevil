#!/usr/bin/env bash
set -e

INSTALL_DIR="${INSTALL_DIR:-/opt/hokidraw-bot}"
SERVICE_NAME="${SERVICE_NAME:-hokidraw-bot}"
PYTHON="python3.11"

if [[ $EUID -ne 0 ]]; then
    SUDO="sudo"
else
    SUDO=""
fi

echo "[1/6] Install system dependencies..."
$SUDO apt-get update -qq
$SUDO apt-get install -y \
    python3.11 python3.11-venv python3.11-dev \
    build-essential libssl-dev libffi-dev \
    curl wget nano git 2>/dev/null

echo "[2/6] Siapkan direktori $INSTALL_DIR..."
$SUDO mkdir -p "$INSTALL_DIR"
$SUDO chown "$USER:$USER" "$INSTALL_DIR"
rsync -a --exclude='.git' --exclude='venv' --exclude='__pycache__' \
    "$(dirname "$0")/" "$INSTALL_DIR/" 2>/dev/null \
    || cp -r "$(dirname "$0")/." "$INSTALL_DIR/"
cd "$INSTALL_DIR"

echo "[3/6] Buat Python virtual environment..."
$PYTHON -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo "[4/6] Install Playwright Chromium..."
python -m playwright install chromium --with-deps -q 2>/dev/null \
    && echo "      OK" \
    || echo "      SKIP (Playwright gagal install, bot tetap bisa jalan tanpa ini)"

mkdir -p data logs instances
echo "[5/6] Direktori data/, logs/, dan instances/ siap."

echo "[6/6] Setup file konfigurasi .env..."
if [ -f "$INSTALL_DIR/.env" ]; then
    echo "      File .env sudah ada, tidak ditimpa."
else
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    echo "      File .env dibuat dari template."
fi

$SUDO cp "$INSTALL_DIR/hokidraw-bot.service" /etc/systemd/system/$SERVICE_NAME.service
$SUDO cp "$INSTALL_DIR/hokidraw-bot@.service" /etc/systemd/system/hokidraw-bot@.service
$SUDO sed -i "s|/opt/hokidraw-bot|$INSTALL_DIR|g" /etc/systemd/system/$SERVICE_NAME.service
$SUDO sed -i "s|/opt/hokidraw-bot|$INSTALL_DIR|g" /etc/systemd/system/hokidraw-bot@.service
$SUDO sed -i "s|User=__SERVICE_USER__|User=$USER|g" /etc/systemd/system/$SERVICE_NAME.service
$SUDO sed -i "s|User=__SERVICE_USER__|User=$USER|g" /etc/systemd/system/hokidraw-bot@.service
$SUDO systemctl daemon-reload

echo ""
echo "Setup selesai."
echo ""
echo "Langkah selanjutnya:"
echo "  1. Edit konfigurasi: nano $INSTALL_DIR/.env"
echo "  2. Cek konfigurasi:"
echo "     cd $INSTALL_DIR && source venv/bin/activate"
echo "     python main.py --check-config"
echo "  3. Test dry-run:"
echo "     python main.py --dry-run"
echo "  4. Jalankan service:"
echo "     sudo systemctl enable --now $SERVICE_NAME"
echo "  5. Cek status/log:"
echo "     sudo systemctl status $SERVICE_NAME"
echo "     journalctl -u $SERVICE_NAME -f"
