#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Installing gp-split-saml desktop integration..."

# Install .desktop file
mkdir -p ~/.local/share/applications
cp "${SCRIPT_DIR}/data/com.github.dlewis7444.gp-split-saml.desktop" \
   ~/.local/share/applications/

# Install icons
for size in 48 64 128; do
    ICON_DIR=~/.local/share/icons/hicolor/${size}x${size}/apps
    mkdir -p "${ICON_DIR}"
    cp "${SCRIPT_DIR}/src/gp_split_saml/data/icons/gp-split-saml.svg" \
       "${ICON_DIR}/gp-split-saml.svg"
done

# Symbolic icon for tray
SYMBOLIC_DIR=~/.local/share/icons/hicolor/symbolic/apps
mkdir -p "${SYMBOLIC_DIR}"
cp "${SCRIPT_DIR}/src/gp_split_saml/data/icons/gp-split-saml.svg" \
   "${SYMBOLIC_DIR}/gp-split-saml-symbolic.svg"

# Update icon cache
gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor 2>/dev/null || true

# Update desktop database
update-desktop-database ~/.local/share/applications 2>/dev/null || true

echo "Desktop integration installed."
echo ""
echo "Install the Python package with:"
echo "  pip install -e '${SCRIPT_DIR}'"
