#!/bin/bash

set -euo pipefail

LABEL="com.jagashira.home-ai-agent.mail-daily"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEMPLATE_PATH="${REPO_ROOT}/launchd/${LABEL}.plist.template"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
PLIST_PATH="${LAUNCH_AGENTS_DIR}/${LABEL}.plist"
DOMAIN="gui/$(id -u)"

if [[ ! -f "${TEMPLATE_PATH}" ]]; then
    echo "launchd template not found: ${TEMPLATE_PATH}" >&2
    exit 1
fi

if [[ ! -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    echo "Virtual environment Python not found: ${REPO_ROOT}/.venv/bin/python" >&2
    exit 1
fi

if [[ "${REPO_ROOT}" == *['&<>|']* ]]; then
    echo "Repository path contains characters unsupported by this installer." >&2
    exit 1
fi

mkdir -p "${LAUNCH_AGENTS_DIR}" "${REPO_ROOT}/logs/mail"

TEMP_PLIST="$(mktemp "${PLIST_PATH}.tmp.XXXXXX")"
trap 'rm -f "${TEMP_PLIST}"' EXIT

sed "s|__REPO_ROOT__|${REPO_ROOT}|g" "${TEMPLATE_PATH}" > "${TEMP_PLIST}"
plutil -lint "${TEMP_PLIST}" >/dev/null

launchctl bootout "${DOMAIN}" "${PLIST_PATH}" >/dev/null 2>&1 || true
install -m 600 "${TEMP_PLIST}" "${PLIST_PATH}"
launchctl bootstrap "${DOMAIN}" "${PLIST_PATH}"
launchctl enable "${DOMAIN}/${LABEL}"

echo "Installed ${LABEL}"
echo "Schedule: daily at 08:00"
echo "Configuration: ${PLIST_PATH}"
echo "Logs: ${REPO_ROOT}/logs/mail/"
