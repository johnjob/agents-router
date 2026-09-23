#!/bin/zsh
# Install (or re-install) the Jev Router launchd service.
# Run ONCE by the user, from THEIR Terminal (launchctl is deliberately
# restricted inside supervised agents).
#
#   bash ~/Documents/Github/jev-codex-router/server/install-service.sh
#
set -e

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$(command -v /usr/local/bin/python3 || command -v python3)"
LABEL="${JEV_ROUTER_LABEL:-com.thibaultsaintjean.jev-router}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGDIR="$HOME/Library/Logs"

[ -x "$PYTHON" ] || { echo "python3 not found"; exit 1; }
mkdir -p "$LOGDIR"

# A python.org framework build ships no CA bundle of its own, so an https call
# made by the service fails certificate verification unless SSL_CERT_FILE
# points at one. The user's shell usually has it exported; launchd does not
# inherit it, so resolve a readable bundle here and embed it in the plist.
CA_FILE="${SSL_CERT_FILE:-}"
if [ ! -r "$CA_FILE" ]; then
  CA_FILE=""
  for candidate in /etc/ssl/cert.pem \
                   "${HOMEBREW_PREFIX:-/opt/homebrew}/etc/ca-certificates/cacert.pem" \
                   /usr/local/etc/ca-certificates/cacert.pem; do
    if [ -r "$candidate" ]; then CA_FILE="$candidate"; break; fi
  done
fi
if [ -n "$CA_FILE" ]; then
  ENV_BLOCK="  <key>EnvironmentVariables</key>
  <dict>
    <key>SSL_CERT_FILE</key><string>$CA_FILE</string>
  </dict>"
  echo "Using CA bundle: $CA_FILE"
else
  ENV_BLOCK=""
  echo "Warning: no readable CA bundle found; https calls from the service may fail" >&2
fi

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$REPO/server/jev_server.py</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOGDIR/jev-router.out.log</string>
  <key>StandardErrorPath</key><string>$LOGDIR/jev-router.err.log</string>
  <key>WorkingDirectory</key><string>$REPO</string>
$ENV_BLOCK
</dict>
</plist>
EOF

# Replace any existing instance (watchdog / former label) with the service.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/io.0xnatoshi.jev-router.plist"
launchctl bootout "gui/$(id -u)/io.0xnatoshi.jev-router" 2>/dev/null || true
pkill -f "jev_server.py" 2>/dev/null || true
sleep 1
launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 1.5
if curl -s -m 5 http://127.0.0.1:4319/health; then
  echo ""
  echo "— Jev Router service OK ($LABEL)"
fi
echo "Uninstall: launchctl bootout gui/\$(id -u)/$LABEL"
