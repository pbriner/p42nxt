# Shared by the deploy scripts (sourced), run in the installation's folder.
#
# The installation's settings live in its database. Docker needs a few of them before anything
# runs, so the scripts copy those into .env; .env is a copy, never the place to change them.

# Docker Compose prefers the shell's variables to .env: a P42_* (or the project's name) left in
# the shell would quietly win over the installation's own values. Only .env counts.
only_the_installation() {
    for variable in $(env | sed -n 's/^\(P42_[A-Z0-9_]*\)=.*/\1/p') COMPOSE_PROJECT_NAME; do
        unset "$variable"
    done
}

# A value from .env, read, not run.
setting() {
    sed -n "s/^$1=//p" .env | tail -n 1
}

# Sets a value in .env (kept readable only by its owner).
set_env() {
    { grep -v "^$1=" .env; echo "$1=$2"; } >.env.new
    chmod 600 .env.new
    mv .env.new .env
}

# The setting "Backup folder", from the database (or --set it there).
backup_folder_setting() {
    docker compose run --rm -T --entrypoint p42cloud upgrade backup folder "$@"
}

# Gives the worker the folder the setting names: made if it isn't there, and copied into .env
# for Docker. An installation from before the setting existed keeps its folder: it's recorded.
apply_backup_folder() {
    folder=$(backup_folder_setting) || return 1
    if [ -z "$folder" ]; then
        folder=$(setting P42_BACKUPS)
        if [ -z "$folder" ]; then
            echo "No backup folder is set: set it in the app (Platform, Settings)." >&2
            return 0
        fi
        echo "Recording the backup folder $folder in the settings ..."
        backup_folder_setting --set "$folder" >/dev/null || return 1
    fi
    mkdir -p "$folder"
    chmod 700 "$folder"
    if [ "$folder" != "$(setting P42_BACKUPS)" ]; then
        echo "Backups go to $folder from now on."
        set_env P42_BACKUPS "$folder"
    fi
}
