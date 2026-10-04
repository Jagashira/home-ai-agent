#!/bin/bash

set -euo pipefail

SERVICE_NAME="home-ai-mail.service"
TIMER_NAME="home-ai-mail.timer"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUN_USER="$(id -un)"
SYSTEMD_DIR="/etc/systemd/system"
SERVICE_TEMPLATE="${REPO_DIR}/systemd/${SERVICE_NAME}.template"
TIMER_SOURCE="${REPO_DIR}/systemd/${TIMER_NAME}"

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "This installer requires Linux." >&2
    exit 1
fi

if ! command -v systemctl >/dev/null 2>&1 || [[ ! -d /run/systemd/system ]]; then
    echo "A running systemd environment is required." >&2
    exit 1
fi

if ! command -v sudo >/dev/null 2>&1; then
    echo "sudo is required to install system units." >&2
    exit 1
fi

if [[ "$(id -u)" -eq 0 ]]; then
    echo "Run this installer as the general user that should run Home Mail Agent, not as root." >&2
    exit 1
fi

if [[ "${REPO_DIR}" == *['&|%']* ]]; then
    echo "Repository path contains characters unsupported by this installer." >&2
    exit 1
fi

if [[ ! -f "${SERVICE_TEMPLATE}" || ! -f "${TIMER_SOURCE}" ]]; then
    echo "systemd template files are missing." >&2
    exit 1
fi

if [[ ! -x "${REPO_DIR}/.venv/bin/python" ]]; then
    echo "Virtual environment Python not found: ${REPO_DIR}/.venv/bin/python" >&2
    exit 1
fi

required_private_files=(
    "${REPO_DIR}/.env"
    "${REPO_DIR}/secrets/google/credentials.json"
    "${REPO_DIR}/secrets/google/token_google_1.json"
    "${REPO_DIR}/secrets/google/token_google_2.json"
    "${REPO_DIR}/secrets/google/token_google_3.json"
    "${REPO_DIR}/secrets/google/token_google_4.json"
    "${REPO_DIR}/data/mail_agent.db"
)

for required_file in "${required_private_files[@]}"; do
    if [[ ! -f "${required_file}" ]]; then
        echo "Required private file not found: ${required_file}" >&2
        exit 1
    fi
done

TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TEMP_DIR}"' EXIT
RENDERED_SERVICE="${TEMP_DIR}/${SERVICE_NAME}"

sed \
    -e "s|__REPO_DIR__|${REPO_DIR}|g" \
    -e "s|__RUN_USER__|${RUN_USER}|g" \
    "${SERVICE_TEMPLATE}" > "${RENDERED_SERVICE}"

if command -v systemd-analyze >/dev/null 2>&1; then
    systemd-analyze verify "${RENDERED_SERVICE}" "${TIMER_SOURCE}"
fi

sudo -v
sudo install -o root -g root -m 0644 \
    "${RENDERED_SERVICE}" "${SYSTEMD_DIR}/${SERVICE_NAME}"
sudo install -o root -g root -m 0644 \
    "${TIMER_SOURCE}" "${SYSTEMD_DIR}/${TIMER_NAME}"
sudo systemctl daemon-reload
sudo systemctl enable --now "${TIMER_NAME}"

systemctl status "${TIMER_NAME}" --no-pager
systemctl list-timers --all --no-pager "${TIMER_NAME}"

echo "Installed ${SERVICE_NAME} for user ${RUN_USER}."
echo "Next run is shown above."
