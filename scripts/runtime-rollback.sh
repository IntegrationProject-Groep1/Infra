#!/bin/bash
# =============================================================================
# runtime-rollback.sh
# ShiftFestival – Infra Team
#
# PURPOSE:
#   Runs as a systemd-managed service on the Azure VM.
#   Every 30 seconds it checks all monitored containers. If a container is
#   unhealthy or stopped, it works through a 6-step diagnosis matrix before
#   deciding whether to roll back, alert the team, or do nothing.
#
# DIAGNOSIS ORDER (never skip a step):
#   1. Are MULTIPLE services down?  → Infra issue (RabbitMQ/ES/VM), no rollback
#   2. Is the container still running? → If yes: config/env issue, no rollback
#   3. Is this service's database up?  → If no: DB issue, alert owner, no rollback
#   4. Is the current image NEWER than .stable_tags? → If same: config issue
#   5. Have we already hit MAX_ROLLBACKS for this service? → Hard stop if yes
#   6. Execute rollback: stop Watchtower, pull stable image, restart, notify
#
# FILES MANAGED BY THIS SCRIPT (never commit these to git):
#   /opt/shiftfestival/.stable_tags    – last known-good image tag per service
#   /opt/shiftfestival/.rollback_state – rollback counter per service
#   /opt/shiftfestival/rollback.lock   – prevents two simultaneous rollbacks
#
# DEPENDENCIES:
#   docker, curl, jq (install: apt-get install -y jq)
# =============================================================================

set -euo pipefail

# -----------------------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------------------

# Base directory where docker-compose.yml lives on the VM
BASE_DIR="/opt/shiftfestival"

# State files (runtime only, not in git)
STABLE_TAGS_FILE="${BASE_DIR}/.stable_tags"
ROLLBACK_STATE_FILE="${BASE_DIR}/.rollback_state"
LOCK_FILE="${BASE_DIR}/rollback.lock"

# How many seconds to wait between each full check cycle
CHECK_INTERVAL=30

# How many consecutive unhealthy checks before we trigger diagnosis
# (heartbeat is 1s, but this script checks every 30s via docker health)
UNHEALTHY_THRESHOLD=4

# Maximum rollback attempts per service before we give up and page infra
MAX_ROLLBACKS=2

# How long (seconds) a rollback lock can be held before we consider it stale
LOCK_TIMEOUT=300

# Teams webhook URLs – loaded from environment or /opt/shiftfestival/.env
# These are set as GitHub Secrets and injected by deploy.yml on first deploy.
# For local testing, export them manually or fill in .env.
TEAMS_WEBHOOK_INFRA="${TEAMS_WEBHOOK_INFRA:-}"
TEAMS_WEBHOOK_FRONTEND="${TEAMS_WEBHOOK_FRONTEND:-}"
TEAMS_WEBHOOK_FACTURATIE="${TEAMS_WEBHOOK_FACTURATIE:-}"
TEAMS_WEBHOOK_KASSA="${TEAMS_WEBHOOK_KASSA:-}"
TEAMS_WEBHOOK_CRM="${TEAMS_WEBHOOK_CRM:-}"
TEAMS_WEBHOOK_PLANNING="${TEAMS_WEBHOOK_PLANNING:-}"
TEAMS_WEBHOOK_MONITORING="${TEAMS_WEBHOOK_MONITORING:-}"

# -----------------------------------------------------------------------------
# SERVICE → DATABASE mapping
# Used in Step 3: check if this service's DB is responsible for the crash.
# Key   = docker container name of the application service
# Value = docker container name of its database
# -----------------------------------------------------------------------------
declare -A SERVICE_DB_MAP=(
    ["frontend_drupal"]="frontend_db"
    ["fossbilling_app"]="fossbilling_db"
    ["kassa_odoo"]="kassa_db"
    ["identity-service"]="integration_db"
    ["crm_receiver"]="crm_db"
)

# -----------------------------------------------------------------------------
# SERVICE → TEAMS WEBHOOK mapping
# When a service is rolled back or fails, BOTH the owning team AND infra
# receive a notification.
# -----------------------------------------------------------------------------
declare -A SERVICE_TEAM_WEBHOOK=(
    ["frontend_drupal"]="${TEAMS_WEBHOOK_FRONTEND}"
    ["frontend_heartbeat"]="${TEAMS_WEBHOOK_FRONTEND}"
    ["fossbilling_app"]="${TEAMS_WEBHOOK_FACTURATIE}"
    ["fossbilling_connector"]="${TEAMS_WEBHOOK_FACTURATIE}"
    ["fossbilling_heartbeat"]="${TEAMS_WEBHOOK_FACTURATIE}"
    ["kassa_odoo"]="${TEAMS_WEBHOOK_KASSA}"
    ["kassa_integratie"]="${TEAMS_WEBHOOK_KASSA}"
    ["kassa_heartbeat"]="${TEAMS_WEBHOOK_KASSA}"
    ["crm_receiver"]="${TEAMS_WEBHOOK_CRM}"
    ["crm_heartbeat"]="${TEAMS_WEBHOOK_CRM}"
    ["planning_service"]="${TEAMS_WEBHOOK_PLANNING}"
    ["planning_heartbeat"]="${TEAMS_WEBHOOK_PLANNING}"
    ["monitoring_heartbeat"]="${TEAMS_WEBHOOK_MONITORING}"
)

# -----------------------------------------------------------------------------
# SERVICES TO MONITOR
# Only monitor services that have a heartbeat sidecar and a meaningful image.
# Infrastructure containers (nginx proxies, watchtower) are excluded because
# they don't carry application logic and have no rollback target.
# -----------------------------------------------------------------------------
MONITORED_SERVICES=(
    "frontend_drupal"
    "fossbilling_app"
    "fossbilling_connector"
    "kassa_odoo"
    "kassa_integratie"
    "crm_receiver"
    "planning_service"
    "identity-service"
    "monitoring_heartbeat"
)

# =============================================================================
# HELPER: LOGGING
# All output goes to stdout so systemd journal captures it automatically.
# =============================================================================
log()   { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO]  $*"; }
warn()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WARN]  $*"; }
error() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] $*" >&2; }

# =============================================================================
# HELPER: LOCK FILE MANAGEMENT
# Prevents two simultaneous rollbacks from racing on .stable_tags.
# =============================================================================

acquire_lock() {
    local lock_owner="$$"  # current PID

    # If a lock exists, check whether it is stale
    if [[ -f "${LOCK_FILE}" ]]; then
        local lock_time
        lock_time=$(stat -c %Y "${LOCK_FILE}" 2>/dev/null || echo 0)
        local now
        now=$(date +%s)
        local age=$(( now - lock_time ))

        if (( age < LOCK_TIMEOUT )); then
            warn "Lock file held by PID $(cat "${LOCK_FILE}") for ${age}s – skipping cycle."
            return 1  # another rollback is in progress
        else
            warn "Stale lock detected (${age}s old) – removing and continuing."
            rm -f "${LOCK_FILE}"
        fi
    fi

    echo "${lock_owner}" > "${LOCK_FILE}"
    log "Lock acquired (PID ${lock_owner})."
    return 0
}

release_lock() {
    rm -f "${LOCK_FILE}"
    log "Lock released."
}

# =============================================================================
# HELPER: TEAMS NOTIFICATION
# Sends a richly-formatted message to one or more Teams channels.
# Falls back to console logging if no webhook URL is configured (useful during
# local testing before the PM sets up the connectors).
#
# Arguments:
#   $1  – webhook URL (can be empty; falls back to console)
#   $2  – severity: INFO | WARNING | CRITICAL
#   $3  – title string
#   $4  – body text (plain, will be wrapped in a code block if it looks like logs)
#   $5  – (optional) extra detail / log snippet
# =============================================================================
send_teams_message() {
    local webhook_url="$1"
    local severity="$2"
    local title="$3"
    local body="$4"
    local log_snippet="${5:-}"

    # Escape special characters for JSON embedding
    local body_escaped
    body_escaped=$(printf '%s' "${body}" | sed 's/\\/\\\\/g; s/"/\\"/g; :a;N;$!ba;s/\n/\\n/g')

    local payload="{
    \"type\": \"message\",
    \"attachments\": [{
        \"contentType\": \"application/vnd.microsoft.card.adaptive\",
        \"contentUrl\": null,
        \"content\": {
            \"type\": \"AdaptiveCard\",
            \"version\": \"1.4\",
            \"body\": [
                {\"type\": \"TextBlock\", \"text\": \"[${severity}] ${title}\", \"weight\": \"Bolder\", \"size\": \"Medium\", \"wrap\": true},
                {\"type\": \"TextBlock\", \"text\": \"ShiftFestival · $(date '+%Y-%m-%d %H:%M:%S')\", \"isSubtle\": true, \"size\": \"Small\"},
                {\"type\": \"TextBlock\", \"text\": \"${body_escaped}\", \"wrap\": true}
            ]
        }
    }]
}"

    if [[ -z "${webhook_url}" ]]; then
        warn "[TEAMS FALLBACK] ${severity}: ${title}"
        warn "[TEAMS FALLBACK] ${body}"
        [[ -n "${log_snippet}" ]] && warn "[TEAMS FALLBACK] LOGS: ${log_snippet}"
        return 0
    fi

    local http_status
    http_status=$(curl -s -o /dev/null -w "%{http_code}" \
        -H "Content-Type: application/json" \
        -d "${payload}" \
        "${webhook_url}")

    if [[ "${http_status}" == "200" || "${http_status}" == "202" ]]; then
        log "Teams notification sent: ${title}"
    else
        error "Teams webhook returned HTTP ${http_status} for: ${title}"
    fi
}

    # Send the card; suppress output but capture HTTP status code
    local http_status
    http_status=$(curl -s -o /dev/null -w "%{http_code}" \
        -H "Content-Type: application/json" \
        -d "${payload}" \
        "${webhook_url}")

    if [[ "${http_status}" != "200" ]]; then
        error "Teams webhook returned HTTP ${http_status} for: ${title}"
    else
        log "Teams notification sent: ${title}"
    fi
}

# Convenience wrapper: always notify infra AND the owning team.
notify() {
    local service="$1"
    local severity="$2"
    local title="$3"
    local body="$4"
    local log_snippet="${5:-}"

    # Always alert the infra channel
    send_teams_message "${TEAMS_WEBHOOK_INFRA}" "${severity}" "${title}" "${body}" "${log_snippet}"

    # Also alert the service's owning team if a webhook is mapped
    local team_webhook="${SERVICE_TEAM_WEBHOOK[${service}]:-}"
    if [[ -n "${team_webhook}" && "${team_webhook}" != "${TEAMS_WEBHOOK_INFRA}" ]]; then
        send_teams_message "${team_webhook}" "${severity}" "${title}" "${body}" "${log_snippet}"
    fi
}

# =============================================================================
# HELPER: DOCKER UTILITIES
# =============================================================================

# Returns "running", "exited", "unhealthy", etc.
get_container_status() {
    local container="$1"
    docker inspect --format='{{.State.Status}}' "${container}" 2>/dev/null || echo "not_found"
}

# Returns the docker health status: healthy | unhealthy | starting | none
get_health_status() {
    local container="$1"
    docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
        "${container}" 2>/dev/null || echo "none"
}

# Returns the full image reference currently used by a running container,
# e.g. ghcr.io/integrationproject-groep1/frontend:v1.2.1
get_container_image() {
    local container="$1"
    docker inspect --format='{{.Config.Image}}' "${container}" 2>/dev/null || echo "unknown"
}

# Extracts the tag portion from a full image reference (e.g. "v1.2.1")
get_image_tag() {
    local image="$1"
    echo "${image##*:}"  # everything after the last colon
}

# Fetches the last N log lines from a container, safe for embedding in JSON.
get_container_logs() {
    local container="$1"
    local lines="${2:-30}"
    # Replace double-quotes and backslashes so the logs embed safely in JSON.
    docker logs --tail "${lines}" "${container}" 2>&1 \
        | sed 's/\\/\\\\/g; s/"/\\"/g; s/$/\\n/g' \
        | tr -d '\n' \
        || echo "Could not retrieve logs."
}

# =============================================================================
# HELPER: .stable_tags FILE
# Format (one line per service):
#   SERVICE_NAME=ghcr.io/org/repo:v1.2.1
# =============================================================================

# Returns the stable image reference for a service, or empty string if unknown.
get_stable_image() {
    local service="$1"
    if [[ ! -f "${STABLE_TAGS_FILE}" ]]; then
        echo ""
        return
    fi
    grep "^${service}=" "${STABLE_TAGS_FILE}" 2>/dev/null \
        | cut -d'=' -f2- \
        | head -1
}

# Updates (or adds) the stable image entry for a service.
set_stable_image() {
    local service="$1"
    local image="$2"

    if [[ -f "${STABLE_TAGS_FILE}" ]]; then
        # Remove old entry for this service, then append the new one
        sed -i "/^${service}=/d" "${STABLE_TAGS_FILE}"
    fi
    echo "${service}=${image}" >> "${STABLE_TAGS_FILE}"
    log "Updated .stable_tags: ${service} → ${image}"
}

# =============================================================================
# HELPER: .rollback_state FILE
# Format (one line per service):
#   SERVICE_NAME=count:last_timestamp:reason
# =============================================================================

get_rollback_count() {
    local service="$1"
    if [[ ! -f "${ROLLBACK_STATE_FILE}" ]]; then
        echo 0
        return
    fi
    grep "^${service}=" "${ROLLBACK_STATE_FILE}" 2>/dev/null \
        | cut -d'=' -f2 \
        | cut -d':' -f1 \
        || echo 0
}

increment_rollback_count() {
    local service="$1"
    local reason="$2"
    local current
    current=$(get_rollback_count "${service}")
    local new_count=$(( current + 1 ))
    local timestamp
    timestamp=$(date +%s)

    if [[ -f "${ROLLBACK_STATE_FILE}" ]]; then
        sed -i "/^${service}=/d" "${ROLLBACK_STATE_FILE}"
    fi
    echo "${service}=${new_count}:${timestamp}:${reason}" >> "${ROLLBACK_STATE_FILE}"
    log "Rollback counter for ${service}: ${current} → ${new_count}"
}

reset_rollback_count() {
    local service="$1"
    if [[ -f "${ROLLBACK_STATE_FILE}" ]]; then
        sed -i "/^${service}=/d" "${ROLLBACK_STATE_FILE}"
    fi
    echo "${service}=0:$(date +%s):reset_after_stable_deploy" >> "${ROLLBACK_STATE_FILE}"
    log "Rollback counter reset for ${service}."
}

# =============================================================================
# STEP 1 – CHECK: Are multiple services down simultaneously?
# If yes, the root cause is almost certainly infra (RabbitMQ, Elasticsearch,
# or the VM itself), not a bad application image.
# Returns 0 (true) if multiple services appear to be down.
# =============================================================================
multiple_services_down() {
    local down_count=0
    for service in "${MONITORED_SERVICES[@]}"; do
        local status
        status=$(get_container_status "${service}")
        local health
        health=$(get_health_status "${service}")

        if [[ "${status}" != "running" ]] \
            || [[ "${health}" == "unhealthy" ]]; then
            (( down_count++ ))
        fi
    done

    # Two or more services in trouble at the same time → likely infra
    (( down_count >= 2 ))
}

# Returns 0 (true) if the named container is currently running and healthy.
container_is_healthy() {
    local container="$1"
    local status
    status=$(get_container_status "${container}")
    local health
    health=$(get_health_status "${container}")

    [[ "${status}" == "running" ]] && [[ "${health}" != "unhealthy" ]]
}

# =============================================================================
# MAIN DIAGNOSIS FUNCTION
# Called once per troubled service per check cycle.
# Works through all 6 steps and either triggers a rollback or explains why not.
# =============================================================================
diagnose_and_recover() {
    local service="$1"
    log "--- Diagnosing service: ${service} ---"

    # Gather basic info upfront so we can include it in every notification
    local current_image
    current_image=$(get_container_image "${service}")
    local current_tag
    current_tag=$(get_image_tag "${current_image}")
    local container_status
    container_status=$(get_container_status "${service}")
    local log_snippet
    log_snippet=$(get_container_logs "${service}" 30)

    # ------------------------------------------------------------------
    # STEP 1: Multiple services down? → Infra issue, never rollback.
    # ------------------------------------------------------------------
    if multiple_services_down; then
        # Sub-check: is RabbitMQ itself the cause?
        local mq_status
        mq_status=$(get_container_status "rabbitmq_broker")
        local es_status
        es_status=$(get_container_status "elasticsearch")

        local cause="Unknown infra issue"
        if [[ "${mq_status}" != "running" ]]; then
            cause="RabbitMQ broker is DOWN (container: ${mq_status})"
        elif [[ "${es_status}" != "running" ]]; then
            cause="Elasticsearch is DOWN (container: ${es_status})"
        fi

        warn "STEP 1 FAIL – Multiple services down. Cause: ${cause}. No rollback."
        notify "${service}" "CRITICAL" \
            "🔴 Infra outage detected – ${cause}" \
            "Multiple services went down simultaneously. This is an infrastructure problem, NOT a bad application image. **No rollback has been triggered.**\n\nAffected service checked: \`${service}\` (${container_status})\nCause: ${cause}\n\nInfra team: please check RabbitMQ, Elasticsearch, and VM health immediately." \
            "${log_snippet}"
        return 0  # no rollback
    fi

    # ------------------------------------------------------------------
    # STEP 2: Is the container still running?
    # If it is running but the heartbeat is missing, the image itself is
    # likely fine – this points to a config, credentials, or network issue.
    # ------------------------------------------------------------------
    if [[ "${container_status}" == "running" ]]; then
        local health
        health=$(get_health_status "${service}")

        if [[ "${health}" != "unhealthy" ]]; then
            warn "STEP 2 – Container ${service} is running and not unhealthy. Heartbeat may have a credentials or RabbitMQ-routing issue. No rollback."
            notify "${service}" "WARNING" \
                "⚠️ Heartbeat missing but container is running – ${service}" \
                "Container \`${service}\` is **running** and not marked unhealthy, but its heartbeat has not been received.\n\nThis usually means:\n- Wrong RabbitMQ credentials in .env\n- Incorrect RABBITMQ_VHOST setting\n- Network routing problem inside shift_net\n\nCurrent image: \`${current_tag}\`\nContainer status: \`${container_status}\`\n\n**No rollback triggered.** Check .env and RabbitMQ management UI." \
                "${log_snippet}"
            return 0  # no rollback
        fi
    fi

    # Container is stopped or unhealthy – continue with root-cause analysis.
    log "STEP 2 OK – Container ${service} is ${container_status}. Continuing diagnosis."

    # ------------------------------------------------------------------
    # STEP 3: Is this service's database up?
    # A crashed DB will take down the app container; rolling back the app
    # image would achieve nothing.
    # ------------------------------------------------------------------
    local db_container="${SERVICE_DB_MAP[${service}]:-}"
    if [[ -n "${db_container}" ]]; then
        if ! container_is_healthy "${db_container}"; then
            local db_status
            db_status=$(get_container_status "${db_container}")
            warn "STEP 3 FAIL – Database ${db_container} is ${db_status}. No rollback."
            notify "${service}" "CRITICAL" \
                "🗄️ Database outage causing ${service} to crash" \
                "Container \`${service}\` is down because its database \`${db_container}\` is **${db_status}**.\n\nCurrent image: \`${current_tag}\`\n\n**No rollback triggered.** The owning team must restore the database or trigger a failover." \
                "${log_snippet}"
            return 0  # no rollback – DB issue
        fi
        log "STEP 3 OK – Database ${db_container} is healthy."
    else
        log "STEP 3 SKIP – No database mapped for ${service}."
    fi

    # ------------------------------------------------------------------
    # STEP 4: Is the current image different from the stable tag?
    # If it is the SAME image that was previously stable, the problem is
    # environmental (bad env vars, missing secret, mount issue) – not the
    # image itself, so rolling back to the same image makes no sense.
    # ------------------------------------------------------------------
    local stable_image
    stable_image=$(get_stable_image "${service}")

    if [[ -z "${stable_image}" ]]; then
        warn "STEP 4 – No stable image recorded for ${service}. Cannot compare. Treating as config issue."
        notify "${service}" "WARNING" \
            "⚠️ No stable baseline for ${service} – cannot rollback" \
            "Container \`${service}\` is down, but no stable image tag has been recorded in \`.stable_tags\` yet.\n\nThis happens on the very first deployment before a successful health check.\n\nInfra team: investigate manually, then run a clean deploy to establish a baseline." \
            "${log_snippet}"
        return 0
    fi

    local stable_tag
    stable_tag=$(get_image_tag "${stable_image}")

    if [[ "${current_image}" == "${stable_image}" ]]; then
        warn "STEP 4 FAIL – Current image (${current_tag}) IS the stable image. This is a config or environment problem, not a bad image. No rollback."
        notify "${service}" "WARNING" \
            "⚠️ Config/env issue detected in ${service}" \
            "Container \`${service}\` is down, but it is running the same image that was previously stable (\`${stable_tag}\`).\n\nThis points to a **configuration or environment problem** (missing env var, wrong secret, volume issue).\n\n**No rollback triggered** – rolling back to the same image would not help. Infra team: check \`.env\` and docker-compose volumes." \
            "${log_snippet}"
        return 0
    fi

    log "STEP 4 OK – Current image (${current_tag}) differs from stable (${stable_tag}). New image is the likely cause."

    # ------------------------------------------------------------------
    # STEP 5: Have we already hit MAX_ROLLBACKS for this service?
    # After MAX_ROLLBACKS attempts with no lasting success, we stop trying
    # automatically and require human intervention.
    # ------------------------------------------------------------------
    local rollback_count
    rollback_count=$(get_rollback_count "${service}")

    if (( rollback_count >= MAX_ROLLBACKS )); then
        error "STEP 5 FAIL – Max rollbacks (${MAX_ROLLBACKS}) already reached for ${service}. Hard stop."
        notify "${service}" "CRITICAL" \
            "🛑 Hard stop: max rollbacks reached for ${service}" \
            "Automatic rollback for \`${service}\` has been attempted **${rollback_count} times** (limit: ${MAX_ROLLBACKS}) without lasting success.\n\nFailing image: \`${current_tag}\`\nLast stable: \`${stable_tag}\`\n\n**No further automatic action will be taken.** Infra team: manual investigation required. Reset the rollback counter once the root cause is resolved." \
            "${log_snippet}"
        return 0
    fi

    log "STEP 5 OK – Rollback count ${rollback_count}/${MAX_ROLLBACKS}. Proceeding with rollback."

    # ------------------------------------------------------------------
    # STEP 6: EXECUTE ROLLBACK
    # Order matters:
    #   a. Acquire lock (prevent race with another check cycle or deploy)
    #   b. Stop Watchtower for this container (prevent it re-pulling :latest)
    #   c. Pull the stable image and restart the service
    #   d. Update the rollback counter
    #   e. Send a rich Teams notification with all relevant details
    #   f. Release lock
    # ------------------------------------------------------------------
    if ! acquire_lock; then
        warn "STEP 6 – Could not acquire lock. Another rollback is in progress. Skipping."
        return 0
    fi

    log "STEP 6 – Executing rollback: ${service} → ${stable_image}"

    # a. Pause Watchtower so it does not immediately re-pull :latest and undo
    #    our rollback within the next 60-second Watchtower cycle.
    log "Pausing Watchtower container..."
    docker pause watchtower 2>/dev/null || warn "Could not pause Watchtower (may already be paused or missing)."

    # b. Pull the stable image explicitly so docker compose uses it
    log "Pulling stable image: ${stable_image}"
    if ! docker pull "${stable_image}"; then
        error "Failed to pull stable image ${stable_image}. Aborting rollback."
        docker unpause watchtower 2>/dev/null || true
        release_lock
        notify "${service}" "CRITICAL" \
            "🔴 Rollback FAILED – could not pull stable image for ${service}" \
            "Attempted to pull \`${stable_image}\` but the pull failed (network issue or image no longer in registry).\n\n**Manual intervention required.**" \
            "${log_snippet}"
        return 1
    fi

    # c. Tag the stable image as :latest locally so docker compose picks it up
    #    without needing to change docker-compose.yml
    local image_repo="${stable_image%%:*}"
    docker tag "${stable_image}" "${image_repo}:latest" 2>/dev/null || true

    # d. Restart only the affected service via docker compose
    cd "${BASE_DIR}"
    log "Restarting ${service} with stable image..."
    if ! docker compose up -d --no-deps "${service}"; then
        error "docker compose up failed for ${service} after rollback pull."
        docker unpause watchtower 2>/dev/null || true
        release_lock
        notify "${service}" "CRITICAL" \
            "🔴 Rollback compose-up FAILED for ${service}" \
            "Pulled stable image \`${stable_tag}\` successfully, but \`docker compose up\` failed.\n\nInfra team: check compose file and container logs." \
            "${log_snippet}"
        return 1
    fi

    # e. Update rollback counter
    increment_rollback_count "${service}" "image_crash_${current_tag}"

    # f. Unpause Watchtower – now that :latest points to the stable image,
    #    Watchtower pulling :latest will simply confirm the correct version.
    sleep 5  # brief pause to let the container start before Watchtower wakes
    docker unpause watchtower 2>/dev/null || true
    log "Watchtower unpaused."

    # g. Collect fresh logs from the now-restarted container for the notification
    sleep 10
    local post_rollback_logs
    post_rollback_logs=$(get_container_logs "${service}" 20)

    # h. Send the rich Teams rollback notification to infra + owning team
    notify "${service}" "CRITICAL" \
        "🔄 Rollback executed – ${service}: ${current_tag} → ${stable_tag}" \
        "Container \`${service}\` was automatically rolled back.\n\n**Failing version:** \`${current_tag}\`\n**Restored version:** \`${stable_tag}\`\n**Rollback attempt:** $((rollback_count + 1)) of ${MAX_ROLLBACKS}\n\n**Root cause summary:**\n- RabbitMQ: online ✅\n- Database: online ✅\n- Image changed: yes (${current_tag} ≠ ${stable_tag}) ✅\n\nPlease investigate the failing image \`${current_tag}\` before pushing again." \
        "=== Logs BEFORE rollback (${service}) ===\n${log_snippet}\n\n=== Logs AFTER rollback start ===\n${post_rollback_logs}"

    release_lock
    log "Rollback complete: ${service} is now running ${stable_tag}."
}

# =============================================================================
# MAIN LOOP
# Runs indefinitely as a systemd service. Each cycle checks every monitored
# container and calls the diagnosis function for any that look unhealthy.
# =============================================================================
main() {
    log "======================================================"
    log "ShiftFestival runtime-rollback.sh starting."
    log "Check interval : ${CHECK_INTERVAL}s"
    log "Max rollbacks  : ${MAX_ROLLBACKS} per service"
    log "Base dir       : ${BASE_DIR}"
    log "======================================================"

    # Load .env if it exists (for local testing without exporting env vars)
    if [[ -f "${BASE_DIR}/.env" ]]; then
        # shellcheck source=/dev/null
        set -a
        source "${BASE_DIR}/.env"
        set +a
        log ".env loaded."
    fi

    # Ensure state files exist so subsequent reads never fail
    touch "${STABLE_TAGS_FILE}" "${ROLLBACK_STATE_FILE}"

    while true; do
        log "--- Starting check cycle ---"

        for service in "${MONITORED_SERVICES[@]}"; do
            local status
            status=$(get_container_status "${service}")
            local health
            health=$(get_health_status "${service}")

            # Only investigate if something looks wrong
            if [[ "${status}" != "running" ]] \
                || [[ "${health}" == "unhealthy" ]]; then
                warn "Service ${service} needs attention: status=${status}, health=${health}"
                diagnose_and_recover "${service}"
            else
                log "Service ${service}: OK (${status}/${health})"
            fi
        done

        log "--- Cycle complete. Sleeping ${CHECK_INTERVAL}s ---"
        sleep "${CHECK_INTERVAL}"
    done
}

main "$@"