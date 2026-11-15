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

# The first free port on this machine from 8080 on (other installations may run here too).
port=${P42_PORT:-}
if [ -z "$port" ]; then
    for candidate in $(seq 8080 8180); do
        if ! (exec 3<>"/dev/tcp/127.0.0.1/$candidate") 2>/dev/null; then
            port=$candidate
            break
        fi
    done
fi
if [ -z "$port" ]; then
    echo "No free port between 8080 and 8180; set P42_PORT. Nothing changed." >&2
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
# A copy of the setting Backup folder (change it in the app, then deploy/restart.sh).
P42_BACKUPS=$backups
P42_PORT=$port
# Who the app runs as: the person installing it (their key, their backup folder).
P42_UID=$(id -u)
P42_GID=$(id -g)
EOF

cd "$folder"
. "$here/lib.sh"
only_the_installation  # read above already; from here on only .env counts
echo "Starting the database of $name ..."
docker compose up -d --wait postgres
echo "Setting up the database ..."
docker compose run --rm setup
echo "Bringing it to this version ..."
docker compose run --rm upgrade
echo "Recording the backup folder in the settings ..."
docker compose run --rm -T --entrypoint p42cloud upgrade backup folder --set "$backups" >/dev/null
echo "Checking that it's safe ..."
docker compose run --rm check
echo "Starting the app and its worker ..."
docker compose up -d web worker
for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:$port/api/v1/health" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
if ! curl -fsS "http://127.0.0.1:$port/api/v1/health" >/dev/null; then
    echo "The app didn't start; see: docker compose --project-directory $folder logs web" >&2
    exit 1
fi

cat <<EOF

The installation $name is ready.
  Folder:      $folder
  Backups:     $backups
  Setup code:  $setup_code
  App:         http://127.0.0.1:$port (put a reverse proxy with TLS in front of it)

Keep a copy of $folder/master.key somewhere safe: without it, encrypted secrets
can't be recovered.

To update it from the app (Platform, Available updates), set up its updater once:
  $here/updater-install.sh $name

Open the app and set it up with the setup code: your company's name, the first administrator,
the modules. Or make the first administrator here instead (asks for a password and a code from
an authenticator app):
  docker compose --project-directory $folder run --rm create-admin --name "Your Name" --email you@example.com
EOF
