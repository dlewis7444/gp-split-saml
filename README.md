# gp-split-saml

A GTK3 GUI application for GlobalProtect VPN with SAML authentication and split tunneling.

## Features

- **SAML Authentication** — Built-in WebKit2 browser for interactive SAML login (replaces gp-saml-gui)
- **Split Tunneling** — Routes only corporate traffic through VPN, keeps home internet direct
- **Split DNS** — Uses `resolvectl` for per-interface DNS configuration
- **GNOME Integration** — System tray (AppIndicator3), desktop notifications, GNOME Keyring cookie storage
- **Bold Theme** — Vivacious dark UI with teal/orange/pink accents
- **Health Monitoring** — Auto-detects VPN drops and restores network state

## System Dependencies (Fedora)

```bash
sudo dnf install python3-gobject gtk3 webkit2gtk4.1 libappindicator-gtk3 \
    libnotify libsecret openconnect
```

## Install

```bash
# Clone and install
cd gp-split-saml
pip install -e .

# Desktop integration (optional)
./install.sh
```

## Configuration

Create a `.env` file in one of these locations (searched in order):

1. `$GP_SPLIT_SAML_ENV` (environment variable)
2. `./.env` (current directory)
3. `~/.config/gp-split-saml/.env`
4. `~/VPN/.env`

```bash
cp .env.example ~/.config/gp-split-saml/.env
# Edit with your VPN settings
```

### .env Format

```ini
VPN_GATEWAY=vpn.example.com
VPN_DNS=10.0.0.1
VPN_DOMAINS="~corp.example.com ~internal.example.com"
VPN_INTERNAL_ROUTE=10.0.0.0/8
#HOME_DNS=192.168.1.1      # auto-detected if omitted
#HOME_DOMAIN=home.local    # auto-detected if omitted
```

## Usage

```bash
# Launch GUI
gp-split-saml

# Or run as module
python -m gp_split_saml
```

Click **CONNECT** to start the SAML authentication flow. After login, the app configures split tunnel routes and DNS automatically.

The window minimizes to the system tray when closed while connected. Use the tray menu to show/hide the window or disconnect.

## How It Works

1. **SAML Prelogin** — POSTs to the GlobalProtect gateway's `prelogin.esp` endpoint
2. **Browser Login** — Opens a WebKit2 window for interactive SAML authentication
3. **VPN Connect** — Launches `openconnect` with the SAML cookie via `sudo`
4. **Route Setup** — Removes VPN default routes, restores home default, adds internal route via `tun0`
5. **DNS Setup** — Configures split DNS via `resolvectl` (VPN domains through tunnel, everything else through home)
6. **Health Monitor** — Checks every 10 seconds that `openconnect` is still running
7. **Clean Disconnect** — Kills `openconnect`, removes routes, reverts DNS to original state

## Tux Mascot

The Tux penguin image is based on the original by Larry Ewing (lewing@isc.tamu.edu), created using GIMP. The image is freely distributable with attribution.

## License

MIT — see [LICENSE](LICENSE).
