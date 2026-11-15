#!/bin/sh
# The updater of an installation: carries out the updates administrators request in the app
# (Platform, Available updates: Update now or Update tonight).
#
#   deploy/updater.sh <name>          every minute, for good (run as a service: updater-install.sh)
#   deploy/updater.sh <name> --once   once, then stop
#
# The app never updates itself: replacing its containers needs control over Docker, which only
# this machine has. The updater asks the running worker what's due, does it as deploy/update.sh
# would (the core) or with the module command (a module), backup first, and reports how it went;
# administrators are told.
set -u

usage() {
    echo "usage: $0 <name> [--once]" >&2
    exit 2
}

[ $# -ge 1 ] || usage
name=$1
once=
[ "${2:-}" = "--once" ] && once=yes
case $name in
    *[!a-z0-9-]* | "" | -*) echo "The name may only contain a-z, 0-9 and -." >&2; exit 2 ;;
esac
here=$(cd "$(dirname "$0")" && pwd)
folder=${P42_HOME:-$HOME/.p42cloud}/$name
if [ ! -f "$folder/.env" ] || [ ! -f "$folder/compose.yml" ]; then
    echo "There's no installation named $name ($folder)." >&2
    exit 1
fi
cd "$folder" || exit 1
. "$here/lib.sh"
only_the_installation
logs=$folder/updates
mkdir -p "$logs"

# The worker's command line (it's what knows the requests); waits while it restarts (exit 3 is the
# app's own no, anything else means it can't be reached yet).
# Its first argument is a file given as its input (each try reads it again).
worker() {
    input=$1
    shift
    for _ in $(seq 1 60); do
        docker compose exec -T worker p42cloud updater "$@" <"$input"
        status=$?
        [ "$status" -eq 0 ] && return 0
        [ "$status" -eq 3 ] && return 1  # the app answered: no
        sleep 5  # the worker is restarting: once more
    done
    return 1
}

carry_out() {
    id=$1 target=$2 version=$3
    log=$logs/$id.log
    echo "$(date -u +%FT%TZ) $target $version: starting"
    worker /dev/null start "$id" || { echo "  no longer waiting (cancelled?)"; return; }
    if [ "$target" = "sys" ]; then
        "$here/update.sh" "$name" --version "$version" >"$log" 2>&1
        status=$?
    else
        (
            backup_before_update
            docker compose run --rm -T module update "$target" --version "$version" &&
                docker compose restart web worker
        ) >"$log" 2>&1
        status=$?
    fi
    backup=$(sed -n 's/^Backing up the database to \(.*\) \.\.\.$/\1/p' "$log" | tail -n 1)
    if [ "$status" -eq 0 ]; then
        state=succeeded message="Updated to $version."
    elif grep -q "Starting the version before again" "$log"; then
        state=put_back message="The update failed; the version before runs again."
    else
        state=failed message="The update failed: see what the updater did."
    fi
    echo "  $state"
    tail -c 20000 "$log" >"$log.tail"
    worker "$log.tail" finish "$id" "$state" --message "$message" --backup "$backup"
    rm -f "$log.tail"
}

while :; do
    due=$(worker /dev/null next 2>/dev/null)
    if [ -n "$due" ]; then
        # shellcheck disable=SC2086  # the ID, the target and the version, split on purpose
        carry_out $due
    fi
    [ -n "$once" ] && exit 0
    sleep 60
done
