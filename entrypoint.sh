#!/bin/sh

# Default PUID and PGID to 1000 if not set
PUID=${PUID:-1000}
PGID=${PGID:-1000}
PORT=${PORT:-8080}

echo "Starting Container with $PUID:$PGID permissions..."

if [ "$PUID" = "0" ]; then
    echo "Running as root user"
    USER_HOME="/root"
    mkdir -p "$USER_HOME"
else
    # Validate PUID and PGID are integers
    if ! echo "$PUID" | grep -qE '^[0-9]+$'; then
        echo "PUID is not a valid integer. Exiting..."
        exit 1
    fi
    
    if ! echo "$PGID" | grep -qE '^[0-9]+$'; then
        echo "PGID is not a valid integer. Exiting..."
        exit 1
    fi
    
    # Default USERNAME and GROUPNAME if not set
    USERNAME=${USERNAME:-riven}
    GROUPNAME=${GROUPNAME:-riven}
    
    # Create group if it doesn't exist
    if ! getent group "$PGID" > /dev/null; then
        addgroup --gid "$PGID" "$GROUPNAME"
        if [ $? -ne 0 ]; then
            echo "Failed to create group. Exiting..."
            exit 1
        fi
    else
        GROUPNAME=$(getent group "$PGID" | cut -d: -f1)
    fi
    
    # Create user if it doesn't exist
    if ! getent passwd "$USERNAME" > /dev/null; then
        adduser -D -h "$USER_HOME" -u "$PUID" -G "$GROUPNAME" "$USERNAME"
        if [ $? -ne 0 ]; then
            echo "Failed to create user. Exiting..."
            exit 1
        fi
    else
        if [ "$PUID" -ne 0 ]; then
            usermod -u "$PUID" -g "$PGID" "$USERNAME"
            if [ $? -ne 0 ]; then
                echo "Failed to modify user UID/GID. Exiting..."
                exit 1
            fi
        else
            echo "Skipping usermod for root user."
        fi
    fi
    
    USER_HOME="/home/$USERNAME"
    mkdir -p "$USER_HOME"
    chown -R "$PUID:$PGID" "$USER_HOME"
    chown -R "$PUID:$PGID" /riven/data
fi

umask 002

export XDG_CONFIG_HOME="$USER_HOME/.config"
export XDG_DATA_HOME="$USER_HOME/.local/share"
export POETRY_CACHE_DIR="$USER_HOME/.cache/pypoetry"
export HOME="$USER_HOME"

# Ensure poetry is in the PATH
export PATH="$PATH:/app/.venv/bin"

echo "Container Initialization complete."

echo "Starting Riven (Backend)..."
if [ "$PUID" = "0" ]; then
    if [ "${DEBUG}" != "" ]; then  # check if DEBUG is set to a truthy value
        cd /riven/src && poetry add debugpy && poetry run python3 -m debugpy --listen 0.0.0.0:5678 main.py --port $PORT
    else
        cd /riven/src && poetry run python3 main.py --port $PORT
    fi
else
    if [ "${DEBUG}" != "" ]; then  # check if DEBUG is set to a truthy value
        poetry add debugpy 
        exec su -m $USERNAME -c "cd /riven/src && poetry run python3 -m debugpy --listen 0.0.0.0:5678 main.py --port $PORT"
    else
        su -m "$USERNAME" -c "cd /riven/src && poetry run python3 main.py --port $PORT"
    fi
fi