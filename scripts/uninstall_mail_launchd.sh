#!/bin/bash

set -euo pipefail

LABEL="com.jagashira.home-ai-agent.mail-daily"
PLIST_PATH="${HOME}/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"

if [[ -f "${PLIST_PATH}" ]]; then
    launchctl bootout "${DOMAIN}" "${PLIST_PATH}" >/dev/null 2>&1 || true
    rm -f "${PLIST_PATH}"
    echo "Uninstalled ${LABEL}"
else
    echo "${LABEL} is not installed."
fi

echo "Existing log files were preserved."
