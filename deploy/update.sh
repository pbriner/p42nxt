#!/bin/sh
# Updates an installation to a new core release, or to an image built here.
#
#   deploy/update.sh <name> [--version <version>]   a release: the newest, or this one
#   deploy/update.sh <name> --image <image>          an image built on this machine (not checked)
#
# A release's image is checked by the image running now: it must be in the registry's signed
# index, signed by a release key that image trusts, and never a lower version. Then: a backup,
# the app and the worker stopped, the new image's compose file and image set, the database
# upgraded, the settings the machine applies (the backup folder) applied, and both started
# again. If the upgrade fails, the version before starts again.
set -eu

usage() {
    echo "usage: $0 <name> [--version <version> | --image <image>]" >&2
    exit 2
}

[ $# -ge 1 ] || usage
name=$1
shift
version=
image=
while [ $# -gt 0 ]; do
    case $1 in
        --version) [ $# -ge 2 ] || usage; version=$2; shift 2 ;;
        --image) [ $# -ge 2 ] || usage; image=$2; shift 2 ;;
        *) usage ;;
    esac
done
[ -z "$version" ] || [ -z "$image" ] || usage
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

before=$(setting P42_IMAGE)
if [ -n "$image" ]; then
    echo "Updating $name to $image (built here: not checked against the registry)."
    target=$image
else
    echo "Asking the registry, checked by the image running now ($before) ..."
    if ! target=$(docker compose run --rm -T \
        --entrypoint p42cloud upgrade core image ${version:+--version "$version"}); then
        echo "No release to update to (see above). Nothing changed." >&2
        echo "An image older than 0.31.0 reads no release address from the app: update it once with --image." >&2
        exit 1
    fi
fi
if [ "$target" = "$before" ]; then
    echo "$name runs $target already. Nothing changed."
    exit 0
fi
docker image inspect "$target" >/dev/null 2>&1 || docker pull "$target"

backup_before_update

# The compose file that belongs to the new image, from the image itself.
if docker run --rm --entrypoint cat "$target" /app/deploy/compose.yml >compose.yml.new 2>/dev/null; then
    :
else
    echo "The new image has no compose file; keeping this one." >&2
    cp compose.yml compose.yml.new
fi
cp -p compose.yml compose.yml.before
cp -p .env .env.before
set_env P42_IMAGE "$target"
mv compose.yml.new compose.yml

put_back() {
    echo "The upgrade failed. Starting the version before again ($before)." >&2
    mv compose.yml.before compose.yml
    mv .env.before .env
    docker compose up -d web worker
    echo "The database as it was before the update: $backup" >&2
    exit 1
}

echo "Stopping the app and its worker ..."
docker compose stop web worker
echo "Upgrading the database ..."
docker compose run --rm upgrade || put_back
echo "Checking that it's safe ..."
docker compose run --rm check || put_back
# Settings the machine applies (the backup folder), from the database.
apply_backup_folder || put_back
echo "Starting the app and its worker ..."
docker compose up -d web worker
port=$(setting P42_PORT)
for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:$port/api/v1/health" >/dev/null 2>&1; then
        rm -f compose.yml.before .env.before
        echo "$name runs $target. The backup from before: $backup"
        exit 0
    fi
    sleep 1
done
echo "The app didn't answer; see: docker compose --project-directory $folder logs web" >&2
echo "The version before: compose.yml.before and .env.before; its database: $backup" >&2
exit 1
