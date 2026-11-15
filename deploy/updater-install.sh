#!/bin/sh
# Sets up the updater of an installation as a service on this machine, running as you: a
# launchd agent on macOS, a systemd user service on Linux. It then carries out the updates
# administrators request in the app.
#
#   deploy/updater-install.sh <name>            set it up (again) and start it
#   deploy/updater-install.sh <name> --remove   stop it and take it away
set -eu

[ $# -ge 1 ] || { echo "usage: $0 <name> [--remove]" >&2; exit 2; }
name=$1
remove=
[ "${2:-}" = "--remove" ] && remove=yes
case $name in
    *[!a-z0-9-]* | "" | -*) echo "The name may only contain a-z, 0-9 and -." >&2; exit 2 ;;
esac
here=$(cd "$(dirname "$0")" && pwd)
folder=${P42_HOME:-$HOME/.p42cloud}/$name
[ -f "$folder/.env" ] || { echo "There's no installation named $name ($folder)." >&2; exit 1; }
mkdir -p "$folder/updates"
# Where Docker's command is found by a service (which gets no shell's PATH).
path=/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin:/usr/bin:/bin

case $(uname) in
Darwin)
    label=cloud.p42.updater.$name
    plist=$HOME/Library/LaunchAgents/$label.plist
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
    if [ -n "$remove" ]; then
        rm -f "$plist"
        echo "The updater of $name is stopped and gone."
        exit 0
    fi
    mkdir -p "$HOME/Library/LaunchAgents"
    cat >"$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array><string>/bin/sh</string><string>$here/updater.sh</string><string>$name</string></array>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$path</string><key>P42_HOME</key><string>${P42_HOME:-$HOME/.p42cloud}</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$folder/updates/updater.log</string>
  <key>StandardErrorPath</key><string>$folder/updates/updater.log</string>
</dict>
</plist>
EOF
    launchctl bootstrap "gui/$(id -u)" "$plist"
    ;;
Linux)
    unit=p42cloud-updater-$name.service
    file=$HOME/.config/systemd/user/$unit
    systemctl --user disable --now "$unit" 2>/dev/null || true
    if [ -n "$remove" ]; then
        rm -f "$file"
        systemctl --user daemon-reload
        echo "The updater of $name is stopped and gone."
        exit 0
    fi
    mkdir -p "$(dirname "$file")"
    cat >"$file" <<EOF
[Unit]
Description=P42cloud updater of $name

[Service]
Environment=PATH=$path
Environment=P42_HOME=${P42_HOME:-$HOME/.p42cloud}
ExecStart=/bin/sh $here/updater.sh $name
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now "$unit"
    echo "So it also runs while you're signed out: sudo loginctl enable-linger $(id -un)"
    ;;
*)
    echo "Only macOS and Linux: run deploy/updater.sh $name yourself." >&2
    exit 1
    ;;
esac
echo "The updater of $name runs: it carries out the updates requested in the app."
echo "Its log: $folder/updates/updater.log"
