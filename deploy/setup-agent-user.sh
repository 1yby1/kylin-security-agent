#!/usr/bin/env bash
set -euo pipefail

AGENT_USER="${AGENT_USER:-software-cup-agent}"
AGENT_GROUP="${AGENT_GROUP:-software-cup-agent}"
STATE_DIR="${STATE_DIR:-/var/lib/software-cup-ops}"
LOG_DIR="${LOG_DIR:-/var/log/software-cup-ops}"
TMP_DIR="${TMP_DIR:-/opt/software-cup-ops/tmp}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "setup-agent-user.sh must be run as root." >&2
  exit 1
fi

if ! getent group "${AGENT_GROUP}" >/dev/null; then
  groupadd --system "${AGENT_GROUP}"
fi

if ! id -u "${AGENT_USER}" >/dev/null 2>&1; then
  useradd \
    --system \
    --gid "${AGENT_GROUP}" \
    --home-dir "${STATE_DIR}" \
    --no-create-home \
    --shell /sbin/nologin \
    --comment "Software Cup Ops Agent least-privilege user" \
    "${AGENT_USER}"
fi

if getent group systemd-journal >/dev/null; then
  usermod -a -G systemd-journal "${AGENT_USER}"
fi

install -d -o "${AGENT_USER}" -g "${AGENT_GROUP}" -m 0750 "${STATE_DIR}"
install -d -o "${AGENT_USER}" -g "${AGENT_GROUP}" -m 0750 "${LOG_DIR}"
install -d -o "${AGENT_USER}" -g "${AGENT_GROUP}" -m 0750 "${TMP_DIR}"

echo "Agent user ready: ${AGENT_USER}:${AGENT_GROUP}"
echo "State directory: ${STATE_DIR}"
echo "Log directory: ${LOG_DIR}"

