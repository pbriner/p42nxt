#!/bin/sh
# Restarts an installation's app and worker with its settings applied on this machine.
#
#   deploy/restart.sh <name>
#
# Some settings need the machine: the backup folder (Platform, Settings, Backup folder) is given
# to the worker by Docker. After changing it in the app, run this once.
set -eu

[ $# -eq 1 ] || { echo "usage: $0 <name>" >&2; exit 2; }
name=$1
case $name in
    *[!a-z0-9-]* | "" | -*) echo "The name may only contain a-z, 0-9 and -." >&2; exit 2 ;;
esac
here=$(cd "$(dirname "$0")" && pwd)
folder=${P42_HOME:-$HOME/.p42cloud}/$name
if [ ! -f "$folder/.env" ] || [ ! -f "$folder/compose.yml" ]; then
    echo "There's no installation named $name ($folder)." >&2
    exit 1
fi
cd "$folder"
. "$here/lib.sh"
only_the_installation

apply_backup_folder
echo "Restarting the app and its worker ..."
docker compose up -d --force-recreate web worker
echo "$name restarted. Backups go to $(setting P42_BACKUPS)."
