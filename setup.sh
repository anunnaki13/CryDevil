#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════
#  HOKIDRAW BOT — Setup Otomatis untuk Ubuntu VPS
#  Jalankan: bash setup.sh
# ═══════════════════════════════════════════════════════
set -e

INSTALL_DIR="${INSTALL_DIR:-/opt/hokidraw-bot}"
SERVICE_NAME="${SERVICE_NAME:-hokidraw-bot}"
PYTHON="python3.11"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║     HOKIDRAW BOT — SETUP VPS UBUNTU     ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── Cek root / sudo ────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    SUDO="sudo"
else
    SUDO=""
fi

# ── System packages ────────────────────────────────────
echo "[1/6] Install system dependencies..."
$SUDO apt-get update -qq
$SUDO apt-get install -y \
    python3.11 python3.11-venv python3.11-dev \
    build-essential libssl-dev libffi-dev \
    curl wget nano git 2>/dev/null
echo "      OK"

# ── Buat install dir ───────────────────────────────────
echo "[2/6] Siapkan direktori $INSTALL_DIR..."
$SUDO mkdir -p "$INSTALL_DIR"
$SUDO chown "$USER:$USER" "$INSTALL_DIR"

# Copy semua file ke install dir (kecuali .git dan venv)
rsync -a --exclude='.git' --exclude='venv' --exclude='__pycache__' \
    "$(dirname "$0")/" "$INSTALL_DIR/" 2>/dev/null \
    || cp -r "$(dirname "$0")/." "$INSTALL_DIR/"

cd "$INSTALL_DIR"
echo "      OK"

# ── Python virtual environment ─────────────────────────
echo "[3/6] Buat Python virtual environment..."
$PYTHON -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "      OK"

# ── Playwright (opsional, untuk bypass Cloudflare) ─────
echo "[4/6] Install Playwright Chromium (Cloudflare bypass)..."
python -m playwright install chromium --with-deps -q 2>/dev/null \
    && echo "      OK" \
    || echo "      SKIP (Playwright gagal install, bot tetap bisa jalan tanpa ini)"

# ── Direktori data & log ───────────────────────────────
mkdir -p data logs instances
echo "[5/6] Direktori data/, logs/, dan instances/ siap."

# ── File .env ──────────────────────────────────────────
echo ""
echo "[6/6] Setup file konfigurasi .env..."
if [ -f "$INSTALL_DIR/.env" ]; then
    echo "      File .env sudah ada, tidak ditimpa."
else
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    echo "      File .env dibuat dari template."
    echo ""
    echo "  ┌─────────────────────────────────────────────────┐"
    echo "  │  PENTING: Isi file .env sebelum menjalankan bot │"
    echo "  └─────────────────────────────────────────────────┘"
    echo "  Buka dengan: nano $INSTALL_DIR/.env"
    echo ""
    echo "  Yang WAJIB diisi:"
    echo "    SITE_URL            → alamat website (ganti jika domain berubah)"
    echo "    PARTAI_USERNAME     → username akun Anda"
    echo "    PARTAI_PASSWORD     → password akun Anda"
    echo "    OPENROUTER_API_KEY  → API key dari openrouter.ai"
    echo "    TELEGRAM_BOT_TOKEN  → token bot Telegram (opsional)"
    echo "    TELEGRAM_CHAT_ID    → chat ID Telegram (opsional)"
    echo ""
    echo "  Yang bisa dikustomisasi (sudah ada default):"
    echo "    INSTANCE_NAME       → nama unik bot untuk multi-instance"
    echo "    BET_TARGET          → depan / tengah / belakang"
    echo "    BASE_BET            → nominal bet per angka"
    echo "    MARTINGALE_LEVELS   → level martingale, pisah koma"
    echo "    DAILY_LOSS_LIMIT    → batas rugi harian (default: Rp 200.000)"
    echo ""
fi

# ── Systemd service ────────────────────────────────────
$SUDO cp "$INSTALL_DIR/hokidraw-bot.service" /etc/systemd/system/$SERVICE_NAME.service
$SUDO cp "$INSTALL_DIR/hokidraw-bot@.service" /etc/systemd/system/hokidraw-bot@.service
$SUDO sed -i "s|/opt/hokidraw-bot|$INSTALL_DIR|g" /etc/systemd/system/$SERVICE_NAME.service
$SUDO sed -i "s|/opt/hokidraw-bot|$INSTALL_DIR|g" /etc/systemd/system/hokidraw-bot@.service
$SUDO sed -i "s|User=ubuntu|User=$USER|g" /etc/systemd/system/$SERVICE_NAME.service
$SUDO sed -i "s|User=ubuntu|User=$USER|g" /etc/systemd/system/hokidraw-bot@.service
$SUDO systemctl daemon-reload

# ═══════════════════════════════════════════════════════
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║           SETUP SELESAI!                ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  LANGKAH SELANJUTNYA:"
echo ""
echo "  1. Isi konfigurasi:"
echo "     nano $INSTALL_DIR/.env"
echo ""
echo "  2. Cek konfigurasi sudah benar:"
echo "     cd $INSTALL_DIR && source venv/bin/activate"
echo "     python main.py --check-config"
echo ""
echo "  3. Test tanpa bet sungguhan (dry-run 1 siklus):"
echo "     python main.py --dry-run"
echo ""
echo "  4. Jalankan sebagai service (otomatis mulai saat VPS reboot):"
echo "     sudo systemctl enable --now $SERVICE_NAME"
echo ""
echo "  4b. Untuk multi-instance:"
echo "     mkdir -p $INSTALL_DIR/instances/bot-1 $INSTALL_DIR/instances/bot-2 $INSTALL_DIR/instances/bot-3 $INSTALL_DIR/shared"
echo "     cp $INSTALL_DIR/instances/bot-template.env $INSTALL_DIR/instances/bot-1/.env"
echo "     cp $INSTALL_DIR/instances/bot-template.env $INSTALL_DIR/instances/bot-2/.env"
echo "     cp $INSTALL_DIR/instances/bot-template.env $INSTALL_DIR/instances/bot-3/.env"
echo "     # bot-1=leader(depan), bot-2=worker(tengah), bot-3=worker(belakang)"
echo "     sudo systemctl enable --now hokidraw-bot@bot-1"
echo "     sudo systemctl enable --now hokidraw-bot@bot-2"
echo "     sudo systemctl enable --now hokidraw-bot@bot-3"
echo ""
echo "  5. Cek status & log:"
echo "     sudo systemctl status $SERVICE_NAME"
echo "     journalctl -u $SERVICE_NAME -f"
echo "     tail -f $INSTALL_DIR/logs/bot.log"
echo ""
echo "  PERINTAH BERGUNA:"
echo "     sudo systemctl stop $SERVICE_NAME      # hentikan bot"
echo "     sudo systemctl restart $SERVICE_NAME   # restart bot"
echo "     sudo systemctl disable $SERVICE_NAME   # nonaktifkan autostart"
echo ""
