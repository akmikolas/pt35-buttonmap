#!/usr/bin/env bash
# pt35-buttonmap installer
#
# Add mouse capability to the built-in keyboard of a Waveshare PocketTerm35
# (Raspberry Pi 4B / Pi 5 handheld):
#   * hold a D-pad direction -> move the pointer (with acceleration)
#   * tap <- twice -> left click, tap -> twice -> right click
#   * tap Backspace twice -> toggle Mouse / Buttons mode
# No firmware flashing; everything happens at the Linux evdev/uinput layer,
# and it all reverts with a single command.
#
#   sudo ./install.sh                 # run unprivileged, as the sudo caller (recommended)
#   sudo ./install.sh --user=root     # run as root (no udev rule installed)
#   sudo ./install.sh --uninstall     # remove everything
#
# Only these locations are created or modified, and all are removed on uninstall:
#   /usr/local/bin/pt35-buttonmap.py
#   /etc/pt35-buttonmap.toml
#   /etc/systemd/system/pt35-buttonmap.service
#   /etc/udev/rules.d/70-pt35-uinput.rules
#   /usr/local/share/doc/pt35-buttonmap/
#   /run/pt35-buttonmap/            (runtime dir, gone after reboot)

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN=/usr/local/bin/pt35-buttonmap.py
CONF=/etc/pt35-buttonmap.toml
UNIT=/etc/systemd/system/pt35-buttonmap.service
UDEV=/etc/udev/rules.d/70-pt35-uinput.rules
DOCDIR=/usr/local/share/doc/pt35-buttonmap
SERVICE=pt35-buttonmap.service

RUN_USER="${SUDO_USER:-}"
UNINSTALL=0
for arg in "$@"; do
    case "$arg" in
        --uninstall) UNINSTALL=1 ;;
        --user=*)    RUN_USER="${arg#--user=}" ;;
        -h|--help)   sed -n '2,18p' "$0"; exit 0 ;;
        *)           echo "unknown argument: $arg (see --help)" >&2; exit 2 ;;
    esac
done
[ -n "$RUN_USER" ] || RUN_USER=root

die() { echo "error: $*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || die "please run with sudo"

# ---------------------------------------------------------------- uninstall
if [ "$UNINSTALL" -eq 1 ]; then
    echo "== uninstalling pt35-buttonmap =="
    systemctl disable --now "$SERVICE" 2>/dev/null || true
    rm -f "$UNIT" "$BIN" "$UDEV"
    rm -rf "$DOCDIR" /run/pt35-buttonmap
    udevadm control --reload 2>/dev/null || true
    udevadm trigger --sysname-match=uinput 2>/dev/null || true
    systemctl daemon-reload
    echo "Uninstalled. The keyboard reverts to stock behaviour immediately, no reboot needed."
    echo "Note: $CONF was kept (your own settings); remove it by hand if you want."
    exit 0
fi

# ---------------------------------------------------------------- install
echo "== 1/7 checking run account =="
if [ "$RUN_USER" = "root" ]; then
    echo "   running as root (no udev rule installed)"
else
    id -u "$RUN_USER" >/dev/null 2>&1 || die "account $RUN_USER does not exist"
    echo "   run account: $RUN_USER (unprivileged)"
fi

echo "== 2/7 installing dependencies =="
if ! python3 -c "import evdev" 2>/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3-evdev
fi
python3 -c "import evdev; assert hasattr(evdev, 'UInput')" || die "python3-evdev is not usable"

# Notifications are optional: with python3-gi we send D-Bus notifications via GIO,
# otherwise we fall back.
if python3 -c "import gi" 2>/dev/null; then
    echo "   python3-gi available -> mode switches will raise a desktop notification"
else
    echo "   note: python3-gi not installed, so mode switches will not notify"
    echo "         to enable it: sudo apt install python3-gi"
fi

echo "== 3/7 installing udev rule =="
if [ "$RUN_USER" = "root" ]; then
    echo "   skipped (root needs no grant)"
    rm -f "$UDEV"
else
    install -m 0644 "$HERE/70-pt35-uinput.rules" "$UDEV"
    udevadm control --reload
    udevadm trigger --sysname-match=uinput
    usermod -aG input "$RUN_USER"
    sleep 1
    echo "   installed; $RUN_USER added to the input group"
fi

echo "== 4/7 installing daemon to $BIN =="
install -m 0755 "$HERE/pt35-buttonmap.py" "$BIN"

echo "== 5/7 installing config to $CONF =="
if [ -f "$CONF" ]; then
    echo "   already exists, kept as is"
else
    "$BIN" --print-config > "$CONF"
    chmod 0644 "$CONF"
    echo "   wrote default configuration"
fi

echo "== 6/7 installing systemd unit =="
sed "s/__PT35_USER__/$RUN_USER/" "$HERE/pt35-buttonmap.service" > "$UNIT"
chmod 0644 "$UNIT"

echo "== 7/7 installing docs and starting =="
mkdir -p "$DOCDIR"
[ -f "$HERE/README.md" ] && install -m 0644 "$HERE/README.md" "$DOCDIR/README.md"
[ -f "$HERE/README.zh-CN.md" ] && install -m 0644 "$HERE/README.zh-CN.md" "$DOCDIR/README.zh-CN.md"
systemctl daemon-reload
systemctl enable --now "$SERVICE"
sleep 3

if ! systemctl is-active --quiet "$SERVICE"; then
    echo
    echo "service failed to start; recent log:" >&2
    journalctl -u "$SERVICE" -n 15 --no-pager >&2
    die "install incomplete (if the log mentions /dev/uinput permissions, reinstall with --user=root)"
fi

cat <<EOF

Installed. You should now be able to:
  * hold a D-pad direction to move the pointer
  * tap <- twice for left click, tap -> twice for right click
  * tap Backspace twice to toggle Mouse / Buttons mode

Inspect or switch mode:
  sudo pt35-buttonmap.py --status
  sudo pt35-buttonmap.py --toggle
  sudo pt35-buttonmap.py --set-mode buttons

Troubleshooting:
  journalctl -u pt35-buttonmap -f
  sudo pt35-buttonmap.py --debug --config $CONF

Uninstall:
  sudo $HERE/install.sh --uninstall
EOF
