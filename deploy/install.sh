#!/bin/sh
# Creates a new P42cloud installation and starts it; the rest happens in the web installer.
#
#   deploy/install.sh <name> [--image p42cloud:<version>] [--qa]
#
# Writes the installation's folder ($P42_HOME/<name>, by default ~/.p42cloud/<name>) with its
# settings (random passwords, the one-time setup code) and its master key, makes the backup
# folder ($P42_BACKUPS/p42cloud-<name>, by default ~/Backups/p42cloud-<name>), starts the database, sets
# it up and checks that it's safe. Refuses to touch an installation that exists already.
# --qa: an installation that never acts in the real world.
set -eu

usage() {
    echo "usage: $0 <name> [--image p42cloud:<version>] [--qa]" >&2
    exit 2
}

[ $# -ge 1 ] || usage
name=$1
shift
image=p42cloud:latest
environment=production
while [ $# -gt 0 ]; do
    case $1 in
        --image) [ $# -ge 2 ] || usage; image=$2; shift 2 ;;
        --qa) environment=qa; shift ;;
        *) usage ;;
    esac
done

case $name in
    *[!a-z0-9-]* | "" | -*) echo "The name may only contain a-z, 0-9 and -." >&2; exit 2 ;;
esac
for tool in docker openssl; do
    command -v "$tool" >/dev/null || { echo "$tool is needed but not installed." >&2; exit 1; }
done
docker compose version >/dev/null 2>&1 || { echo "Docker Compose is needed." >&2; exit 1; }

home=${P42_HOME:-$HOME/.p42cloud}
folder=$home/$name
backups=${P42_BACKUPS:-$HOME/Backups}/p42cloud-$name
project=p42cloud-$name
if [ -e "$folder" ]; then
    echo "An installation named $name exists already ($folder). Nothing changed." >&2
    exit 1
fi
# Docker may know the name from another installation whose folder is elsewhere (or another
# tool using the same names): starting ours would take over its containers and its data.
label=com.docker.compose.project=$project
if [ -n "$(docker ps -aq --filter "label=$label")$(docker volume ls -q --filter "label=$label")" ]; then
    echo "Docker already has containers or data named $project. Choose another name." >&2
    echo "Nothing changed." >&2
    exit 1
fi

here=$(cd "$(dirname "$0")" && pwd)
umask 077
mkdir -p "$folder" "$backups"
cp "$here/compose.yml" "$folder/compose.yml"
openssl rand -base64 32 >"$folder/master.key"
setup_code=$(openssl rand -hex 4 | tr 'a-f' 'A-F')
cat >"$folder/.env" <<EOF
COMPOSE_PROJECT_NAME=$project
P42_IMAGE=$image
P42_ENVIRONMENT=$environment
P42_ADMIN_PASSWORD=$(openssl rand -hex 24)
P42_OWNER_PASSWORD=$(openssl rand -hex 24)
P42_APP_PASSWORD=$(openssl rand -hex 24)
P42_SETUP_CODE=$setup_code
P42_BACKUPS=$backups
EOF

cd "$folder"
echo "Starting the database of $name ..."
docker compose up -d --wait postgres
echo "Setting up the database ..."
docker compose run --rm setup
echo "Checking that it's safe ..."
docker compose run --rm check

cat <<EOF

The installation $name is ready.
  Folder:      $folder
  Backups:     $backups
  Setup code:  $setup_code

Keep a copy of $folder/master.key somewhere safe: without it, encrypted secrets
can't be recovered.
EOF
