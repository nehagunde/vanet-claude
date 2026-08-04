#!/usr/bin/env bash
# install_kali.sh — one-shot environment setup for the VANET project on Kali.
# Idempotent: safe to run multiple times.
#
# What this does:
#   1. Confirms required tools are on PATH (sumo, sumo-gui, python3, pip3).
#   2. Installs apt packages we need that may be missing on a fresh Kali.
#   3. Creates a Python virtualenv at ./.venv.
#   4. Installs everything in requirements.txt into the venv.
#   5. Verifies the Python imports succeed.
#
# Usage (from the project root inside Kali):
#   bash scripts/install_kali.sh

set -euo pipefail

# ---------- locate project root (one level up from this script) ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "==> VANET project setup"
echo "    Project root: $PROJECT_ROOT"
echo

# ---------- 1. Verify required tools are present ----------
have() { command -v "$1" >/dev/null 2>&1; }
need() {
    if have "$1"; then
        echo "  [OK]      $1  ($(command -v "$1"))"
        return 0
    else
        echo "  [MISSING] $1"
        return 1
    fi
}

echo "==> Checking required tools"
MISSING=0
need sumo     || MISSING=1
need sumo-gui || MISSING=1
need python3  || MISSING=1
need pip3     || MISSING=1

# NS-3 (ns-3-dev) is usually built from source and may not be on PATH.
# We just probe for it; the actual path will be wired up in Phase 3.
if have ns3; then
    echo "  [OK]      ns3       ($(command -v ns3))"
else
    echo "  [INFO]    ns3 not on PATH — that's fine; Phase 3 will reference"
    echo "            the build directory directly."
fi
echo

if [ "$MISSING" -ne 0 ]; then
    echo "Some required tools are missing. Install them with:"
    echo "  sudo apt update"
    echo "  sudo apt install -y sumo sumo-gui sumo-tools python3 python3-pip python3-venv"
    echo
    echo "Then re-run this script."
    exit 1
fi

# ---------- 2. apt packages ----------
echo "==> Installing/refreshing apt packages"
sudo apt update
sudo apt install -y \
    python3-venv \
    python3-dev \
    build-essential \
    git \
    curl
# osmctools is nice-to-have for Phase 1 but not strictly required (netconvert
# can read .osm directly). Try to install it; don't fail if unavailable.
sudo apt install -y osmctools || echo "  [INFO] osmctools not available — fine, we'll use netconvert directly."
echo

# ---------- 3. Python virtualenv ----------
# IMPORTANT: the venv must live OUTSIDE the project tree on this setup,
# because the project root is a VMware shared folder (vmhgfs-fuse) which
# doesn't support the lib64 → lib symlink that `python3 -m venv` creates.
# We put it in $HOME/.venvs/vanet_claude — standard, off the shared folder.
VENV_DIR="$HOME/.venvs/vanet_claude"
mkdir -p "$(dirname "$VENV_DIR")"

# Clean up any stale in-tree .venv from prior failed attempts (harmless if absent).
if [ -d "$PROJECT_ROOT/.venv" ]; then
    echo "==> Removing stale in-tree .venv (venv lives outside the shared folder now)"
    rm -rf "$PROJECT_ROOT/.venv"
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "==> Creating Python virtualenv at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
else
    echo "==> Reusing existing virtualenv at $VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "==> Upgrading pip and installing requirements.txt"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo

# ---------- 4. Verify Python imports ----------
echo "==> Verifying Python imports"
python - <<'PY'
import importlib, sys
mods = ["googlemaps", "dotenv", "traci", "sumolib", "numpy", "shapely", "requests"]
fail = []
for m in mods:
    try:
        importlib.import_module(m)
        print(f"  [OK]   {m}")
    except Exception as e:
        print(f"  [FAIL] {m}  ({e})")
        fail.append(m)
sys.exit(1 if fail else 0)
PY
echo

# ---------- 5. SUMO_HOME hint ----------
if [ -z "${SUMO_HOME:-}" ]; then
    SUMO_BIN="$(command -v sumo)"
    SUMO_PREFIX="$(dirname "$(dirname "$SUMO_BIN")")"
    if [ -d "$SUMO_PREFIX/share/sumo" ]; then
        echo "==> SUMO_HOME is not set. Suggest adding to ~/.bashrc:"
        echo "       export SUMO_HOME=$SUMO_PREFIX/share/sumo"
        echo "       export PATH=\$PATH:\$SUMO_HOME/tools"
        echo
    fi
fi

echo "==> Setup complete."
echo
echo "Next steps:"
echo "  1. Copy .env.example to .env and put your real GOOGLE_MAPS_API_KEY in it:"
echo "       cp .env.example .env && \$EDITOR .env"
echo "  2. Activate the venv whenever you work on the project:"
echo "       source $VENV_DIR/bin/activate"
echo "     (Tip: add this alias to ~/.bashrc for convenience:"
echo "       alias vanet-venv='source $VENV_DIR/bin/activate')"
echo "  3. Tell Claude 'go' for Phase 1."
