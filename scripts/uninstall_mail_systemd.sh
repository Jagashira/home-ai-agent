#!/bin/bash

set -euo pipefail

SERVICE_NAME="home-ai-mail.service"
TIMER_NAME="home-ai-mail.timer"
SYSTEMD_DIR="/etc/systemd/system"

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "This uninstaller requires Linux." >&2
    exit 1
fi

if ! command -v systemctl >/dev/null 2>&1 || [[ ! -d /run/systemd/system ]]; then
    echo "A running systemd environment is required." >&2
    exit 1
fi

if ! command -v sudo >/dev/null 2>&1; then
    echo "sudo is required to remove system units." >&2
    exit 1
fi

sudo systemctl disable --now "${TIMER_NAME}" >/dev/null 2>&1 || true
sudo rm -f \
    "${SYSTEMD_DIR}/${SERVICE_NAME}" \
    "${SYSTEMD_DIR}/${TIMER_NAME}"
sudo systemctl daemon-reload

echo "Uninstalled ${SERVICE_NAME} and ${TIMER_NAME}."
echo "Repository data, credentials, tokens, database, and Gmail labels were preserved."

