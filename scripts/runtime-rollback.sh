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
#   /opt/shiftfestival/.stable_tags    – last known-good image SHA|TAG per service
#   /opt/shiftfestival/.rollback_state – rollback counter per service
#   /opt/shiftfestival/rollback.lock   – prevents two simultaneous rollbacks
#   /opt/shiftfestival/.rollback_pins  – active sticky pins awaiting a fix
#                                        format: service=bad_sha|env_var|readable_tag
#
# AUTOMATIC PIN RELEASE:
#   After a sticky rollback, check_pinned_services() runs every cycle and
#   queries the GHCR registry manifest. When a NEW SHA appears on :latest
#   (meaning the team pushed a fix), the pin is automatically released and
#   the service is restarted with the new image.
#
# DEPENDENCIES:
#   docker, curl, jq, python3 (install: apk add bash curl jq python3)
# =============================================================================

# NOTE: We deliberately do NOT use "set -e" here.
# This is a long-running monitoring daemon that must NEVER exit because of a
# single failed command. If docker pull fails, if a grep returns nothing, etc,
# the script must continue running and keep monitoring. We only use -u and
# pipefail for safety on undefined variables and broken pipes.
set -uo pipefail

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

# State file tracking active sticky pins (service=bad_sha|env_var|readable_tag)
ROLLBACK_PINS_FILE="${BASE_DIR}/.rollback_pins"

# State file tracking when a notification was last sent per (service, event_key).
# Prevents notification spam when a service fails repeatedly in a short window.
# Format: service:event_key=timestamp
NOTIFICATION_COOLDOWN_FILE="${BASE_DIR}/.notification_cooldown"

# How long (seconds) to suppress duplicate notifications for the same
# service + event_key. 10 minutes is a good balance between not missing
# new incidents and not spamming when a service keeps crashing.
NOTIFICATION_COOLDOWN_SECONDS=600


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
# CONTAINER NAME → DOCKER COMPOSE SERVICE NAME MAPPING
#
# Docker container names use underscores (e.g. kassa_integratie) but
# docker compose service names use hyphens (e.g. kassa-integratie).
# This mapping ensures docker compose up targets the correct service name.
# Any container NOT listed here falls back to using its own name as-is.
# -----------------------------------------------------------------------------
declare -A CONTAINER_TO_COMPOSE=(
    ["frontend_drupal"]="frontend-drupal"
    ["fossbilling_app"]="fossbilling-app"
    ["fossbilling_connector"]="fossbilling-connector"
    ["kassa_odoo"]="kassa-web"
    ["kassa_integratie"]="kassa-integratie"
    ["crm_receiver"]="crm-receiver"
    ["planning_service"]="planning-service"
    ["monitoring_heartbeat"]="monitoring-heartbeat"
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
    "monitoring_heartbeat"   # Monitors the ELK stack – must also be monitored itself
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
# =============================================================================
# HELPER: JSON ESCAPING
# Safely escapes a string for embedding inside a JSON value.
# =============================================================================
escape_json() {
    # Escape for JSON embedding:
    #   1. Protect literal \n sequences (our intentional line breaks) with a placeholder
    #   2. Escape real backslashes
    #   3. Escape double quotes
    #   4. Convert real newlines to \n
    #   5. Restore the placeholder back to \n
    # Uses only basic sed features compatible with busybox (Alpine container).
    printf '%s' "$1" \
        | sed 's/\\n/NEWLINE_PLACEHOLDER/g' \
        | sed 's/\\/\\\\/g' \
        | sed 's/"/\\"/g' \
        | sed ':a;N;$!ba;s/\n/\\n/g' \
        | sed 's/NEWLINE_PLACEHOLDER/\\n/g'
}

# =============================================================================
# HELPER: TEAMS NOTIFICATION
#
# Sends a Power Automate Adaptive Card to a Teams channel.
# Falls back to console if webhook URL is empty (local testing).
#
# Arguments:
#   $1 – webhook URL
#   $2 – severity: INFO | WARNING | CRITICAL
#   $3 – title string
#   $4 – body text
#   $5 – (optional) log snippet
# =============================================================================
send_teams_message() {
    local webhook_url="$1"
    local severity="$2"
    local title="$3"
    local body="$4"
    local log_snippet="${5:-}"

    # Map severity to Adaptive Card colour name
    local card_color
    case "${severity}" in
        CRITICAL) card_color="Attention" ;;
        WARNING)  card_color="Warning"   ;;
        INFO)     card_color="Good"      ;;
        *)        card_color="Default"   ;;
    esac

    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')

    local body_escaped
    body_escaped=$(escape_json "${body}")

    # Build the Adaptive Card body array using printf to avoid shellcheck
    # SC2140 warnings caused by embedded double quotes in assignment strings.
    local header_block
    header_block=$(printf '{"type":"TextBlock","text":"[%s] %s","weight":"Bolder","size":"Medium","color":"%s","wrap":true}'         "${severity}" "${title}" "${card_color}")

    local meta_block
    meta_block=$(printf '{"type":"TextBlock","text":"ShiftFestival · %s","isSubtle":true,"size":"Small"}'         "${timestamp}")

    local body_block
    body_block=$(printf '{"type":"TextBlock","text":"%s","wrap":true,"spacing":"Medium"}'         "${body_escaped}")

    # Start building the card body array
    local card_body="[${header_block},${meta_block},${body_block}"

    if [[ -n "${log_snippet}" ]]; then
        local logs_escaped
        logs_escaped=$(escape_json "${log_snippet}")

        local log_header_block
        log_header_block=$(printf '{"type":"TextBlock","text":"📋 Last log lines:","weight":"Bolder","spacing":"Medium"}')

        local log_body_block
        log_body_block=$(printf '{"type":"TextBlock","text":"%s","fontType":"Monospace","size":"Small","wrap":true}'             "${logs_escaped}")

        card_body="${card_body},${log_header_block},${log_body_block}"
    fi

    card_body="${card_body}]"

    # Assemble the full Power Automate Adaptive Card payload
    local payload
    payload=$(printf '{"type":"message","attachments":[{"contentType":"application/vnd.microsoft.card.adaptive","contentUrl":null,"content":{"type":"AdaptiveCard","version":"1.4","body":%s}}]}'         "${card_body}")

    if [[ -z "${webhook_url}" ]]; then
        warn "[TEAMS FALLBACK] ${severity}: ${title}"
        warn "[TEAMS FALLBACK] ${body}"
        [[ -n "${log_snippet}" ]] && warn "[TEAMS FALLBACK] LOGS: ${log_snippet}"
        return 0
    fi

    local http_status
    http_status=$(curl -s -o /dev/null -w "%{http_code}"         -H "Content-Type: application/json"         -d "${payload}"         "${webhook_url}")

    if [[ "${http_status}" == "200" || "${http_status}" == "202" ]]; then
        log "Teams notification sent: ${title}"
    else
        error "Teams webhook returned HTTP ${http_status} for: ${title}"
    fi
}


# =============================================================================
# HELPER: NOTIFICATION COOLDOWN
#
# Returns 0 (true) if a notification for this (service, event_key) combination
# was already sent within NOTIFICATION_COOLDOWN_SECONDS. This prevents the same
# alert from flooding Teams when a service is in a crash loop or a rollback
# keeps failing.
#
# Arguments:
#   $1 – service name (e.g. kassa_integratie)
#   $2 – event key (e.g. "rollback_pull_failed", "rollback_executed")
# =============================================================================
in_cooldown() {
    local service="$1"
    local event_key="$2"
    local cooldown_key="${service}:${event_key}"

    [[ ! -f "${NOTIFICATION_COOLDOWN_FILE}" ]] && return 1  # no cooldown file = not in cooldown

    local last_sent
    last_sent=$(grep "^${cooldown_key}=" "${NOTIFICATION_COOLDOWN_FILE}" 2>/dev/null         | cut -d'=' -f2 | head -1)

    [[ -z "${last_sent}" ]] && return 1  # never sent before = not in cooldown

    local now
    now=$(date +%s)
    local age=$(( now - last_sent ))

    if (( age < NOTIFICATION_COOLDOWN_SECONDS )); then
        return 0  # in cooldown – suppress the notification
    else
        return 1  # cooldown expired – allow the notification
    fi
}

# Records that a notification was just sent so future calls within the window are suppressed.
mark_notification_sent() {
    local service="$1"
    local event_key="$2"
    local cooldown_key="${service}:${event_key}"
    local now
    now=$(date +%s)

    # Remove any previous entry for this key, then write the new timestamp
    if [[ -f "${NOTIFICATION_COOLDOWN_FILE}" ]]; then
        sed -i "/^${cooldown_key}=/d" "${NOTIFICATION_COOLDOWN_FILE}"
    fi
    echo "${cooldown_key}=${now}" >> "${NOTIFICATION_COOLDOWN_FILE}"
}

# =============================================================================
# HELPER: notify() – send to Infra AND owning team (with cooldown)
#
# Always notifies the #Infra channel AND the service's owning team channel,
# but suppresses duplicate notifications within NOTIFICATION_COOLDOWN_SECONDS.
# Pass an event_key as the 6th arg to scope the cooldown per-event.
# =============================================================================
notify() {
    local service="$1"
    local severity="$2"
    local title="$3"
    local body="$4"
    local log_snippet="${5:-}"
    # Use the title as the event key by default – same title = same event = dedup
    local event_key="${6:-${title}}"

    # Check if we already sent this notification recently
    if in_cooldown "${service}" "${event_key}"; then
        log "Suppressing duplicate notification (cooldown active): ${title}"
        return 0
    fi

    # Always alert the Infra channel
    send_teams_message "${TEAMS_WEBHOOK_INFRA}" "${severity}" "${title}" "${body}" "${log_snippet}"

    # Also alert the owning team channel
    local team_webhook="${SERVICE_TEAM_WEBHOOK[${service}]:-}"
    if [[ -n "${team_webhook}" && "${team_webhook}" != "${TEAMS_WEBHOOK_INFRA}" ]]; then
        send_teams_message "${team_webhook}" "${severity}" "${title}" "${body}" "${log_snippet}"
    fi

    # Record that we just sent this notification
    mark_notification_sent "${service}" "${event_key}"
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
    # Escape logs for safe JSON embedding.
    # Each sed command is on its own pipe for busybox compatibility –
    # chaining multiple substitutions with ';' can fail in Alpine's busybox sed.
    docker logs --tail "${lines}" "${container}" 2>&1 \
        | sed 's/\\/\\\\/g' \
        | sed 's/"/\\"/g' \
        | sed 's/$/\\n/' \
        | tr -d '\n' \
        || echo "Could not retrieve logs."
}

# =============================================================================
# HELPER: COMPOSE SERVICE NAME RESOLVER
# Translates a Docker container name to the correct docker compose service name.
# Container names use underscores; compose service names may use hyphens.
# =============================================================================
get_compose_service() {
    local container="$1"
    # Look up in the mapping; fall back to the container name itself
    echo "${CONTAINER_TO_COMPOSE[${container}]:-${container}}"
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
    local s  # Use 's' to avoid clobbering the 'service' variable in the caller
    for s in "${MONITORED_SERVICES[@]}"; do
        local s_status
        s_status=$(get_container_status "${s}")
        local s_health
        s_health=$(get_health_status "${s}")

        if [[ "${s_status}" != "running" ]]             || [[ "${s_health}" == "unhealthy" ]]; then
            (( down_count++ )) || true
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
    current_image=$(get_container_image "${service}")   # readable tag: ghcr.io/org/repo:latest
    local current_sha
    current_sha=$(docker inspect --format='{{.Image}}' "${service}" 2>/dev/null || echo "")  # real SHA digest
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
        # Infra outage → only notify #Infra. Owning teams cannot fix VM-level issues.
        # Use cooldown to prevent spam when multiple services keep failing.
        if ! in_cooldown "infra" "outage_${cause}"; then
            send_teams_message "${TEAMS_WEBHOOK_INFRA}" "CRITICAL"                 "🔴 Infra outage detected – ${cause}"                 "Multiple services went down simultaneously. This is an infrastructure problem, NOT a bad application image. **No rollback has been triggered.**\n\nAffected service checked: \`${service}\` (${container_status})\nCause: ${cause}\n\nInfra team: please check RabbitMQ, Elasticsearch, and VM health immediately."                 "${log_snippet}"
            mark_notification_sent "infra" "outage_${cause}"
        else
            log "Infra outage notification suppressed (cooldown active)."
        fi
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
        # No stable baseline = deploy/infra issue → only notify #Infra (with cooldown)
        if ! in_cooldown "${service}" "no_baseline"; then
            send_teams_message "${TEAMS_WEBHOOK_INFRA}" "WARNING"                 "⚠️ No stable baseline for ${service} – cannot rollback"                 "Container \`${service}\` is down, but no stable image tag has been recorded in \`.stable_tags\` yet.\n\nThis happens on the very first deployment before a successful health check.\n\nInfra team: investigate manually, then run a clean deploy to establish a baseline."                 "${log_snippet}"
            mark_notification_sent "${service}" "no_baseline"
        fi
        return 0
    fi

    # Extract the stable SHA from the SHA|TAG entry (everything before '|')
    local stable_sha="${stable_image%%|*}"
    local stable_readable="${stable_image##*|}"
    local stable_tag
    stable_tag=$(get_image_tag "${stable_readable}")

    # Compare using SHA digests:
    #   current_sha = docker inspect {{.Image}} of the (now stopped) container
    #   stable_sha  = SHA recorded by deploy.yml after last successful deploy
    #
    # If they match → same image version → rollback would be pointless → config issue
    # If they differ → new image was deployed after stable baseline → rollback makes sense
    #
    # NOTE: current_sha may be empty if the container never started. In that case
    # we fall through to rollback as a safe default.
    if [[ -n "${current_sha}" && "${current_sha}" == "${stable_sha}" ]]; then
        warn "STEP 4 FAIL – Current SHA (${current_sha:0:19}...) matches stable SHA. Config/env issue, not a bad image. No rollback."
        notify "${service}" "WARNING" \
            "⚠️ Config/env issue detected in ${service}" \
            "Container \`${service}\` is down, but it is running the same image that was previously stable (\`${stable_tag}\`, SHA: \`${stable_sha:0:19}...\`).\n\nThis points to a **configuration or environment problem** (missing env var, wrong secret, volume issue).\n\n**No rollback triggered** – rolling back to the same image would not help. Infra team: check \`.env\` and docker-compose volumes." \
            "${log_snippet}"
        return 0
    fi

    log "STEP 4 OK – Current SHA (${current_sha:0:19}...) differs from stable SHA (${stable_sha:0:19}...). New image is the likely cause."

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
    # STEP 6: EXECUTE STICKY ROLLBACK
    #
    # "Sticky" means we PIN the stable image SHA into the .env file so
    # that docker-compose uses it directly. Because the container then
    # runs on a SHA digest (not a floating tag like :latest), Watchtower
    # has nothing to monitor and will leave it alone until the next
    # successful deploy clears the pin.
    #
    # Order:
    #   a. Acquire lock
    #   b. Pause Watchtower (prevent race during the pin operation)
    #   c. Pull the stable SHA from the registry
    #   d. Write the pin into .env  (e.g. FRONTEND_DRUPAL_IMAGE=...@sha256:...)
    #   e. Restart the service – compose now reads the pinned SHA from .env
    #   f. Update rollback counter
    #   g. Unpause Watchtower – it ignores SHA-pinned containers
    #   h. Notify teams
    #   i. Release lock
    # ------------------------------------------------------------------
    if ! acquire_lock; then
        warn "STEP 6 – Could not acquire lock. Another rollback is in progress. Skipping."
        return 0
    fi

    # Read the combined SHA|TAG entry written by deploy.yml
    local stable_entry
    stable_entry=$(grep "^${service}=" "${STABLE_TAGS_FILE}" | cut -d'=' -f2- | head -1)

    if [[ -z "${stable_entry}" ]]; then
        error "STEP 6 – No stable entry found for ${service}. Aborting rollback."
        release_lock
        return 1
    fi

    # Split into technical SHA (for docker pull) and readable tag (for logs/Teams)
    local sha_image="${stable_entry%%|*}"
    local readable_stable_tag="${stable_entry##*|}"

    log "STEP 6 – Executing STICKY rollback: ${service} → ${readable_stable_tag} (${sha_image})"

    # a. Pause Watchtower to prevent it interfering during the pin operation
    log "Pausing Watchtower container..."
    docker pause watchtower 2>/dev/null || warn "Could not pause Watchtower (may already be paused or missing)."

    # b. Pull the exact stable image by SHA digest.
    #
    # IMPORTANT: docker pull requires a fully-qualified reference when pulling
    # by digest. A bare sha256:abc... is NOT a valid pull target because Docker
    # does not know which registry to query.
    #
    # Correct format:  ghcr.io/org/repo@sha256:abc123...
    # Wrong format:    sha256:abc123...   (→ "pull access denied for sha256")
    #
    # We build the pull reference by combining the readable tag's registry/repo
    # part with the SHA digest.
    local pull_ref
    if [[ "${sha_image}" == sha256:* ]]; then
        # Extract registry/repo from the readable tag (strip the :tag suffix)
        local registry_repo="${readable_stable_tag%%:*}"
        pull_ref="${registry_repo}@${sha_image}"
    else
        # sha_image already looks like a full reference (legacy fallback)
        pull_ref="${sha_image}"
    fi

    log "Pulling stable image: ${pull_ref}"
    if ! docker pull "${pull_ref}"; then
        error "Failed to pull stable image ${pull_ref}. Aborting rollback."
        docker unpause watchtower 2>/dev/null || true
        release_lock
        # Use pull_ref as part of the event_key so a new pull target
        # (e.g. after a redeploy updates the stable SHA) is not suppressed
        notify "${service}" "CRITICAL" \
            "🔴 Rollback FAILED – could not pull stable image for ${service}" \
            "Attempted to pull \`${pull_ref}\` but the pull failed.\n\nThis may be a registry authentication issue or the image was deleted.\n\n**Manual intervention required.**" \
            "${log_snippet}" \
            "rollback_pull_failed_${pull_ref:0:30}"
        return 1
    fi

    # Tag the pulled image with the readable reference so docker compose can
    # find it by name when restarting the service.
    docker tag "${pull_ref}" "${readable_stable_tag}" 2>/dev/null || true

    # c. PIN the SHA into the .env file.
    #    Convert service name to uppercase env var name:
    #    e.g. "frontend_drupal" → FRONTEND_DRUPAL_IMAGE
    #    docker-compose.yml must use ${FRONTEND_DRUPAL_IMAGE:-ghcr.io/.../frontend:latest}
    local env_var_name
    env_var_name=$(echo "${service}" | tr '[:lower:]' '[:upper:]' | tr '-' '_')_IMAGE

    log "Pinning ${service} in .env: ${env_var_name}=${sha_image}"
    # Remove any existing pin for this service, then append the new one
    sed -i "/^${env_var_name}=/d" "${BASE_DIR}/.env"
    echo "${env_var_name}=${sha_image}" >> "${BASE_DIR}/.env"

    # d. Restart the service – docker compose now reads the pinned SHA from .env
    #    IMPORTANT: use the compose service name (hyphens), not the container name
    local compose_service
    compose_service=$(get_compose_service "${service}")
    cd "${BASE_DIR}" || return 1
    log "Restarting compose service '${compose_service}' (container: ${service}) with pinned SHA..."
    if ! docker compose up -d --no-deps "${compose_service}"; then
        error "docker compose up failed for ${service} (compose: ${compose_service}) after sticky rollback."
        # Clean up the pin so the next deploy is not blocked
        sed -i "/^${env_var_name}=/d" "${BASE_DIR}/.env"
        docker unpause watchtower 2>/dev/null || true
        release_lock
        notify "${service}" "CRITICAL"             "🔴 Sticky rollback compose-up FAILED for ${service}"             "Pulled stable SHA \`${sha_image}\` but \`docker compose up ${compose_service}\` failed.\n\nInfra team: check compose file and container logs."             "${log_snippet}"
        return 1
    fi

    # e. Update rollback counter
    increment_rollback_count "${service}" "sticky_rollback_${readable_stable_tag}"

    # f. Record the BAD SHA in .rollback_pins so check_pinned_services() can
    #    automatically detect when the team pushes a fix to GHCR and release
    #    the pin without any manual intervention.
    #    Format: service=bad_sha|env_var_name|readable_tag
    #
    #    IMPORTANT: we store current_sha (the real SHA digest via {{.Image}}),
    #    NOT current_image (the readable tag like ghcr.io/.../kassa:latest).
    #    check_pinned_services() compares this against the registry manifest
    #    digest from `docker buildx imagetools inspect`. Both must use the
    #    same SHA format for the comparison to work correctly.
    local bad_sha_to_record="${current_sha:-${current_image}}"
    sed -i "/^${service}=/d" "${ROLLBACK_PINS_FILE}" 2>/dev/null || true
    echo "${service}=${bad_sha_to_record}|${env_var_name}|${readable_stable_tag}" >> "${ROLLBACK_PINS_FILE}"
    log "Recorded bad SHA in .rollback_pins: ${bad_sha_to_record:0:19}..."

    # g. Unpause Watchtower – SHA-pinned container will be ignored automatically
    sleep 5
    docker unpause watchtower 2>/dev/null || true
    log "Watchtower unpaused. Container ${service} is pinned – Watchtower will ignore it."

    # h. Collect fresh logs for the notification
    sleep 10
    local post_rollback_logs
    post_rollback_logs=$(get_container_logs "${service}" 20)

    # i. Send notification WITH action button to BOTH Infra AND the owning team.
    #    The button links to GitHub Actions so the team can manually trigger a
    #    redeploy as a fallback if the automatic fix detection does not fire.
    local rollback_body
    rollback_body="Container \`${service}\` was automatically rolled back and PINNED to its last stable SHA.\n\n"
    rollback_body+="**Failing image:** \`${current_tag}\` (SHA: \`${current_sha:0:19}...\`)\n"
    rollback_body+="**Restored + pinned to:** \`${stable_readable}\` (SHA: \`${stable_sha:0:19}...\`)\n"
    rollback_body+="**Rollback attempt:** $((rollback_count + 1)) of ${MAX_ROLLBACKS}\n\n"
    rollback_body+="The rollback monitor will automatically detect when a new image is pushed to GHCR and release the pin.\n\n"
    rollback_body+="⚠️ Fix the failing image and push a new version. The rollback monitor will automatically detect the new image within 30 seconds."

    # Use rollback attempt number as part of event_key so attempt 1 and
    # attempt 2 both trigger a notification even within the cooldown window.
    notify "${service}" "CRITICAL" \
        "🔄 STICKY Rollback executed – ${service}" \
        "${rollback_body}" \
        "=== Logs BEFORE rollback (${service}) ===
${log_snippet}

=== Logs AFTER rollback start ===
${post_rollback_logs}" \
        "rollback_executed_attempt_$((rollback_count + 1))"

    release_lock
    log "Sticky rollback complete: ${service} is pinned to ${readable_stable_tag}."
}

# =============================================================================
# HELPER: CHECK PINNED SERVICES FOR AUTOMATIC FIX DETECTION
#
# Runs at the start of every check cycle. For each service in .rollback_pins,
# it queries the GHCR registry manifest (WITHOUT downloading the image) to see
# if the team has pushed a new SHA to :latest.
#
# If a NEW SHA is found  → release pin, restart service, notify both channels
# If SAME SHA is found   → keep pin, service stays stable on the rollback image
# If registry unreachable → keep pin, log warning, try again next cycle
#
# This makes the fix flow fully automatic:
#   Team pushes fix → GHCR:latest gets new SHA → rollback monitor detects it
#   → pin released → service restarted with new image → Watchtower takes over
# =============================================================================
check_pinned_services() {
    # Nothing to do if no pins are active
    [[ ! -f "${ROLLBACK_PINS_FILE}" ]] && return 0
    [[ ! -s "${ROLLBACK_PINS_FILE}" ]] && return 0

    log "Checking pinned services for new images in registry..."

    while IFS='=' read -r service pin_data; do
        # Skip comment lines and empty lines
        [[ "${service}" =~ ^#.*$ ]] && continue
        [[ -z "${service}" ]] && continue

        # Parse the pin entry: bad_sha|env_var_name|readable_tag
        local bad_sha env_var_name readable_tag
        bad_sha="${pin_data%%|*}"
        local rest="${pin_data#*|}"
        env_var_name="${rest%%|*}"
        readable_tag="${rest##*|}"

        log "PIN CHECK: ${service} – bad SHA: ${bad_sha:0:19}... tag: ${readable_tag}"

        # Query the registry MANIFEST DIGEST without fully downloading layers.
        # We use `docker buildx imagetools inspect` because it returns the SAME
        # SHA that `docker inspect --format='{{.Image}}' <container>` returns
        # for a running container – they match exactly. This is the correct
        # value to compare against `bad_sha` (which was captured via {{.Image}}).
        #
        # Alternative approaches we tried and REJECTED:
        #   - `docker manifest inspect` + .config.digest → returns the CONFIG
        #     blob digest, which is NOT the same as {{.Image}}. Would cause
        #     every check to fire "new image detected" incorrectly.
        #   - `docker pull --quiet` → only prints image name, not the digest.
        #   - HTTP HEAD to /v2/*/manifests/* → requires manual auth token
        #     extraction, brittle.
        local registry_sha=""
        registry_sha=$(docker buildx imagetools inspect "${readable_tag}" 2>/dev/null             | awk '/^Digest:/ {print $2; exit}' || echo "")

        # Fallback: if buildx is unavailable for any reason, use docker pull
        # and parse its "Digest:" output line. docker pull is a no-op when
        # the image is already local AND the registry digest is unchanged,
        # so this is safe and cheap.
        if [[ -z "${registry_sha}" ]]; then
            registry_sha=$(docker pull "${readable_tag}" 2>&1                 | awk '/^Digest:/ {print $2; exit}' || echo "")
        fi

        if [[ -z "${registry_sha}" ]]; then
            warn "PIN CHECK: Could not reach registry for ${service} – keeping pin, will retry next cycle."
            continue
        fi

        if [[ "${registry_sha}" == "${bad_sha}" ]]; then
            log "PIN CHECK: ${service} – registry still has bad image (${registry_sha:0:19}...) – pin remains active."
            continue
        fi

        # ── NEW IMAGE DETECTED ──────────────────────────────────────────────
        log "PIN CHECK: NEW image detected for ${service}!"
        log "  Bad image:  ${bad_sha:0:19}..."
        log "  New image:  ${registry_sha:0:19}..."
        log "  Releasing sticky pin and restarting ${service}..."

        # Remove the pin from .env
        sed -i "/^${env_var_name}=/d" "${BASE_DIR}/.env"

        # Remove the entry from .rollback_pins
        sed -i "/^${service}=/d" "${ROLLBACK_PINS_FILE}"

        # Pull the new image and restart the service using the compose service name
        local pin_compose_service
        pin_compose_service=$(get_compose_service "${service}")
        cd "${BASE_DIR}" || return 1
        if docker compose up -d --no-deps "${pin_compose_service}" 2>/dev/null; then
            log "PIN RELEASED: ${service} restarted with new image. Watchtower will monitor normally."
            notify "${service}" "INFO" \
                "✅ Sticky pin released – ${service} updated with new image" \
                "A new image was detected in the registry for \`${service}\`.

The sticky rollback pin has been **automatically released** and the service has been restarted.

**Bad image (was pinned to):** \`${bad_sha:0:19}...\`
**New image (now running):** \`${registry_sha:0:19}...\`

If the new image also fails, the rollback system will activate again." \
                ""                 ""                 ""
        else
            error "PIN RELEASE: docker compose up failed for ${service} (compose: ${pin_compose_service}) after pin release."
            # Re-add the pin since the restart failed
            echo "${service}=${bad_sha}|${env_var_name}|${readable_tag}" >> "${ROLLBACK_PINS_FILE}"
            sed -i "/^${env_var_name}=/d" "${BASE_DIR}/.env"
            echo "${env_var_name}=${bad_sha}" >> "${BASE_DIR}/.env"

            # Pin release failure = compose/VM issue → only notify #Infra (with cooldown)
            if ! in_cooldown "${service}" "pin_release_failed"; then
                send_teams_message "${TEAMS_WEBHOOK_INFRA}" "CRITICAL"                     "🔴 Pin release FAILED for ${service}"                     "A new image was detected but \`docker compose up\` failed after releasing the pin.\n\nThe pin has been re-applied. Infra team: manual investigation required."                     ""
                mark_notification_sent "${service}" "pin_release_failed"
            fi
        fi
    done < "${ROLLBACK_PINS_FILE}"
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

    # Load .env safely – handles passwords containing $, %, &, ! and other
    # special characters that would break a plain `source` command.
    if [[ -f "${BASE_DIR}/.env" ]]; then
        while IFS= read -r line || [[ -n "${line}" ]]; do
            # Strip Windows carriage returns
            line="${line%$'\r'}"
            # Trim leading/trailing whitespace
            line=$(echo "${line}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
            # Skip empty lines and comments
            [[ -z "${line}" || "${line}" == \#* ]] && continue
            # Only export valid KEY=VALUE pairs (ignore malformed lines)
            if [[ "${line}" =~ ^[a-zA-Z_][a-zA-Z0-9_]*= ]]; then
                export "${line?}"
            fi
        done < "${BASE_DIR}/.env"
        log ".env loaded and sanitized."
    fi

    # Ensure all state files exist so reads never fail on first run
    touch "${STABLE_TAGS_FILE}" "${ROLLBACK_STATE_FILE}" "${ROLLBACK_PINS_FILE}" "${BASE_DIR}/.restart_counts" "${NOTIFICATION_COOLDOWN_FILE}"

    while true; do
        # ── Deploy lock check ────────────────────────────────────────────────
        # When deploy.yml is running, it creates .deploy_in_progress to signal
        # that services are intentionally being restarted. We skip the entire
        # check cycle to avoid false CRITICAL alerts during deployments.
        # The lock is always removed by deploy.yml when it finishes (success or failure).
        if [[ -f "${BASE_DIR}/.deploy_in_progress" ]]; then
            log "Deploy in progress – skipping check cycle to avoid false alerts."
            sleep "${CHECK_INTERVAL}"
            continue
        fi

        log "--- Starting check cycle ---"

        # ── Phase 1: Check if any pinned services have a new image available ──
        # This runs BEFORE the health checks so that a fixed service is already
        # restarted with the new image before we evaluate its health status.
        check_pinned_services

        # ── Phase 2: Health check every monitored service ─────────────────────
        for service in "${MONITORED_SERVICES[@]}"; do
            local status
            status=$(get_container_status "${service}")
            local health
            health=$(get_health_status "${service}")

            # ── Crash loop detection via RestartCount delta ──────────────────
            # Docker RestartCount is cumulative and never resets. By comparing
            # the current count to what we saw last cycle (stored in
            # .restart_counts), we detect services that have restarted 2+
            # times in a single 30s window – a strong indicator of a crash loop.
            local restart_count
            restart_count=$(docker inspect --format='{{.RestartCount}}' "${service}" 2>/dev/null || echo 0)
            local restart_count_file="${BASE_DIR}/.restart_counts"
            local prev_count
            prev_count=$(grep "^${service}=" "${restart_count_file}" 2>/dev/null | cut -d'=' -f2 || echo 0)
            # Update stored count for next cycle comparison
            sed -i "/^${service}=/d" "${restart_count_file}" 2>/dev/null || true
            echo "${service}=${restart_count}" >> "${restart_count_file}"
            local recent_restarts=$(( restart_count - prev_count ))

            # Investigate if not running, unhealthy, OR in a crash loop
            if [[ "${status}" != "running" ]]                 || [[ "${health}" == "unhealthy" ]]                 || (( recent_restarts >= 2 )); then
                warn "Service ${service} needs attention: status=${status}, health=${health}, restarts_this_cycle=${recent_restarts}"
                diagnose_and_recover "${service}"
            else
                log "Service ${service}: OK (${status}/${health})"
                # Service is healthy – clear any active notification cooldowns
                # so the next incident is not suppressed by a stale cooldown
                if [[ -f "${BASE_DIR}/.notification_cooldown" ]]; then
                    if grep -q "^${service}:" "${BASE_DIR}/.notification_cooldown" 2>/dev/null; then
                        sed -i "/^${service}:/d" "${BASE_DIR}/.notification_cooldown"
                        log "Cleared notification cooldown for recovered service: ${service}"
                    fi
                fi
            fi
        done

        log "--- Cycle complete. Sleeping ${CHECK_INTERVAL}s ---"
        sleep "${CHECK_INTERVAL}"
    done
}

main "$@"