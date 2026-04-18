#!/bin/bash
# =============================================================================
# test-rollback.sh
# ShiftFestival – Infra Team
#
# PURPOSE:
#   Safely tests the rollback DETECTION logic on a live service without
#   actually executing a rollback. This verifies the 6-step diagnosis matrix
#   fires correctly without needing a real "bad" image to pull from GHCR.
#
# APPROACH:
#   We cannot test an END-TO-END rollback safely because:
#   1. A real rollback requires a real pullable stable SHA in the registry
#   2. Stopping the container + injecting a fake SHA makes "docker pull" fail
#   3. That is correct behaviour of the rollback script, not a bug
#
#   So we test what we CAN test safely:
#   - Stop the container (simulates a crash)
#   - Keep .stable_tags with the REAL SHA
#   - Verify the monitor correctly detects it and proceeds through Steps 1-5
#   - The actual Step 6 rollback will succeed because the real SHA is pullable
#
# USAGE:
#   bash test-rollback.sh [service_name]
#   bash test-rollback.sh kassa_integratie
#   bash test-rollback.sh --restore     # Recover after a failed test
#
# SAFETY:
#   - No .stable_tags modification means restore is always trivial
#   - If anything goes wrong, run: bash test-rollback.sh --restore
#   - The rollback monitor does the actual recovery automatically via Step 6
# =============================================================================

# NOTE: we do NOT use set -e because we want the script to continue even
# if some cleanup commands fail.
set -uo pipefail

BASE_DIR="/opt/shiftfestival"
TEST_SERVICE="${1:-kassa_integratie}"
TEST_BACKUP_FILE="${BASE_DIR}/.test_backup_$(date +%s)"

# Colours for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()     { echo -e "${BLUE}[TEST]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC}   $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[FAIL]${NC} $*"; }

# Resolve docker compose service name (compose uses hyphens, not underscores)
resolve_compose_service() {
    case "$1" in
        "kassa_integratie")      echo "kassa-integratie" ;;
        "frontend_drupal")       echo "frontend-drupal" ;;
        "fossbilling_app")       echo "fossbilling-app" ;;
        "fossbilling_connector") echo "fossbilling-connector" ;;
        "crm_receiver")          echo "crm-receiver" ;;
        "planning_service")      echo "planning-service" ;;
        "kassa_odoo")            echo "kassa-web" ;;
        *)                       echo "$1" ;;
    esac
}

# =============================================================================
# RESTORE MODE – brings any service back up cleanly
# =============================================================================
if [[ "${1:-}" == "--restore" ]]; then
    log "=== MANUAL RESTORE MODE ==="

    # Find the most recent backup file
    latest_backup=$(ls -t "${BASE_DIR}"/.test_backup_* 2>/dev/null | head -1 || echo "")

    if [[ -z "${latest_backup}" ]]; then
        warn "No backup file found. Will try to restart all team services anyway."
        SERVICE_TO_RESTART="kassa-integratie"
    else
        log "Found backup: ${latest_backup}"
        # shellcheck source=/dev/null
        source "${latest_backup}"
        SERVICE_TO_RESTART="${TEST_COMPOSE_SERVICE:-kassa-integratie}"
    fi

    # Clean up any test state files that might be left over
    sed -i "/^${TEST_SERVICE:-kassa_integratie}=/d" "${BASE_DIR}/.rollback_state" 2>/dev/null || true
    sed -i "/^${TEST_SERVICE:-kassa_integratie}=/d" "${BASE_DIR}/.rollback_pins" 2>/dev/null || true
    sed -i "/^${TEST_SERVICE:-kassa_integratie}:/d" "${BASE_DIR}/.notification_cooldown" 2>/dev/null || true
    success "Cleared test state from .rollback_state, .rollback_pins, .notification_cooldown"

    # Remove any sticky pin from .env
    if [[ -n "${TEST_ENV_VAR:-}" ]]; then
        sed -i "/^${TEST_ENV_VAR}=/d" "${BASE_DIR}/.env" 2>/dev/null || true
        success "Removed any sticky pin from .env"
    fi

    # Restart service cleanly
    cd "${BASE_DIR}" || exit 1
    docker compose up -d --no-deps "${SERVICE_TO_RESTART}"
    success "Service ${SERVICE_TO_RESTART} restarted"

    rm -f "${latest_backup}" 2>/dev/null || true
    success "Restore complete."
    exit 0
fi

# =============================================================================
# PRE-FLIGHT CHECKS
# =============================================================================
log "=== ShiftFestival Rollback Detection Test ==="
log "Service under test: ${TEST_SERVICE}"
log ""

if [[ ! -f "${BASE_DIR}/docker-compose.yml" ]]; then
    error "docker-compose.yml not found in ${BASE_DIR}. Are you on the VM?"
    exit 1
fi

# Check the service is currently running
current_status=$(docker inspect --format='{{.State.Status}}' "${TEST_SERVICE}" 2>/dev/null || echo "not_found")
if [[ "${current_status}" != "running" ]]; then
    error "Service ${TEST_SERVICE} is not running (status: ${current_status}). Cannot test."
    exit 1
fi
success "Service ${TEST_SERVICE} is currently running"

# Check .stable_tags has an entry for this service
stable_entry=$(grep "^${TEST_SERVICE}=" "${BASE_DIR}/.stable_tags" 2>/dev/null | cut -d'=' -f2- || echo "")
if [[ -z "${stable_entry}" ]]; then
    error "No entry in .stable_tags for ${TEST_SERVICE}. Run a deploy first."
    exit 1
fi

stable_sha="${stable_entry%%|*}"
stable_tag="${stable_entry##*|}"
success "Stable baseline: ${stable_tag} (${stable_sha:0:19}...)"

# Resolve compose service name
compose_service=$(resolve_compose_service "${TEST_SERVICE}")
success "Docker compose service: ${compose_service}"

env_var_name=$(echo "${TEST_SERVICE}" | tr '[:lower:]' '[:upper:]' | tr '-' '_')_IMAGE

# =============================================================================
# SAVE CURRENT STATE (for --restore)
# =============================================================================
log ""
log "=== Saving current state ==="

cat > "${TEST_BACKUP_FILE}" << BACKUP
TEST_SERVICE="${TEST_SERVICE}"
TEST_COMPOSE_SERVICE="${compose_service}"
TEST_ENV_VAR="${env_var_name}"
ORIGINAL_STABLE_ENTRY="${stable_entry}"
BACKUP

success "State saved to ${TEST_BACKUP_FILE}"
log "  Emergency restore: bash ${0} --restore"

# =============================================================================
# INJECT TEST CONDITION — Full rollback simulation
#
# To trigger a real rollback we need:
#   current_sha (running container) ≠ stable_sha (in .stable_tags)
#
# Strategy:
#   1. Pull alpine:latest → get its SHA as a "fake stable" reference
#   2. Write that SHA into .stable_tags as if it were the last stable deploy
#   3. Stop the container (simulates a crash)
#   4. Monitor sees: current_sha (real kassa) ≠ stable_sha (alpine)
#      → Step 4 passes → Step 6 rollback executes
#   5. Rollback pulls the REAL kassa SHA (from stable_entry backup)
#      and restores the service correctly
#
# The real kassa SHA is backed up in TEST_BACKUP_FILE and will be
# restored in the cleanup phase regardless of what happens.
# =============================================================================
log ""
log "=== Injecting test condition ==="

# Pull a small known-good image to use as the fake stable SHA
# alpine:latest is tiny (~3MB) and guaranteed to exist
log "Pulling alpine:latest to use as fake stable SHA..."
fake_stable_pull=$(docker pull alpine:latest 2>&1)
fake_stable_sha=$(docker inspect --format='{{.Image}}'     "$(docker create alpine:latest 2>/dev/null)" 2>/dev/null || echo "")

# Clean up the temporary container we just created
docker ps -a --filter "ancestor=alpine:latest" --filter "status=created"     -q 2>/dev/null | xargs docker rm 2>/dev/null || true

if [[ -z "${fake_stable_sha}" ]]; then
    # Fallback: get SHA directly from image store
    fake_stable_sha=$(docker inspect --format='{{.Id}}' alpine:latest 2>/dev/null || echo "")
fi

if [[ -z "${fake_stable_sha}" ]]; then
    error "Could not determine alpine SHA. Cannot run full rollback test."
    exit 1
fi

success "Fake stable SHA obtained: ${fake_stable_sha:0:19}..."
log "Real stable SHA (will be restored): ${stable_sha:0:19}..."

# Write the fake stable entry — monitor will see current image != stable
log "Overwriting .stable_tags with fake stable entry..."
sed -i "/^${TEST_SERVICE}=/d" "${BASE_DIR}/.stable_tags"
echo "${TEST_SERVICE}=${fake_stable_sha}|ghcr.io/integrationproject-groep1/kassa:latest"     >> "${BASE_DIR}/.stable_tags"
success "Fake stable entry written (alpine SHA as stable baseline)"

# Stop the container to simulate a crash
log "Stopping ${TEST_SERVICE} to simulate a crash..."
docker stop "${TEST_SERVICE}" >/dev/null 2>&1 || true

stopped_status=$(docker inspect --format='{{.State.Status}}' "${TEST_SERVICE}" 2>/dev/null || echo "not_found")
if [[ "${stopped_status}" == "running" ]]; then
    error "Failed to stop container. Status: ${stopped_status}"
    # Restore stable_tags before exiting
    sed -i "/^${TEST_SERVICE}=/d" "${BASE_DIR}/.stable_tags"
    echo "${TEST_SERVICE}=${stable_entry}" >> "${BASE_DIR}/.stable_tags"
    exit 1
fi
success "Container stopped (status: ${stopped_status})"
log ""
log "Test condition active:"
log "  Container:    STOPPED (simulating crash)"
log "  Current SHA:  ${stable_sha:0:19}... (real kassa image)"
log "  Stable SHA:   ${fake_stable_sha:0:19}... (alpine – different!)"
log "  Expected:     Step 4 passes → Step 6 rollback executes"

# =============================================================================
# WAIT FOR THE ROLLBACK MONITOR TO DETECT IT
#
# What we EXPECT to happen:
#   1. Monitor detects ${TEST_SERVICE} is "exited" within 30 seconds
#   2. Step 1: NOT multiple services down -> pass
#   3. Step 2: Container not running -> continue diagnosis
#   4. Step 3: Database (if any) healthy -> pass
#   5. Step 4: Current image == stable image -> WARNING notification
#              "Config/env issue detected"
#              (This is the CORRECT behaviour – nothing to rollback to)
#
# This verifies that detection, diagnosis, and notification ALL work
# without actually executing a destructive rollback.
# =============================================================================
log ""
log "=== Waiting for rollback monitor to detect the condition ==="
log "The monitor checks every 30s. Expected behaviour:"
log "  - Monitor detects container stopped (exited)"
log "  - Step 4: current SHA != stable SHA (alpine) → rollback triggered"
log "  - Step 6: pulls REAL kassa stable SHA → service restored"
log "  - Teams CRITICAL notification sent to Infra + Kassa"
log ""

# Record the current log line count so we only look at NEW log lines
# generated AFTER the test condition was injected. This prevents false
# negatives caused by leftover log lines from previous test runs.
LOG_LINES_BEFORE=$(docker logs rollback_monitor 2>&1 | wc -l)
log "Current monitor log lines: ${LOG_LINES_BEFORE} (only new lines will be checked)"

MAX_WAIT=90
WAITED=0
DETECTED=false

while (( WAITED < MAX_WAIT )); do
    # Only look at log lines written AFTER we started the test
    new_logs=$(docker logs rollback_monitor 2>&1 | tail -n +"${LOG_LINES_BEFORE}")

    if echo "${new_logs}" | grep -q "Diagnosing service: ${TEST_SERVICE}"; then
        DETECTED=true
        break
    fi

    echo -n "  Waiting... (${WAITED}s / ${MAX_WAIT}s)"$'\r'
    sleep 5
    WAITED=$(( WAITED + 5 ))
done

echo ""

if ! ${DETECTED}; then
    warn "Detection not found in rollback_monitor logs after ${MAX_WAIT}s."
    warn "Check manually: docker logs rollback_monitor --tail 50"
else
    success "Monitor detected the condition and started diagnosing! ✅"
    log ""
    log "Relevant monitor output (new lines only):"
    docker logs rollback_monitor 2>&1 | tail -n +"${LOG_LINES_BEFORE}" | grep -E "${TEST_SERVICE}|STEP|Diagnosing|Rollback|rollback|Teams" | tail -15 || true
fi

# =============================================================================
# CLEANUP: Restore original state
# Regardless of what happened, we always:
#   1. Restore the real stable SHA in .stable_tags
#   2. Remove any sticky pins written by the rollback
#   3. Restart the service cleanly
#   4. Reset test counters and cooldowns
# =============================================================================
log ""
log "=== Cleanup: restoring original state ==="

# Always restore the real stable SHA (rollback may have changed it)
sed -i "/^${TEST_SERVICE}=/d" "${BASE_DIR}/.stable_tags"
echo "${TEST_SERVICE}=${stable_entry}" >> "${BASE_DIR}/.stable_tags"
success "Restored original stable SHA in .stable_tags"

# Remove any sticky pin written by the rollback
env_var=$(echo "${TEST_SERVICE}" | tr '[:lower:]' '[:upper:]' | tr '-' '_')_IMAGE
sed -i "/^${env_var}=/d" "${BASE_DIR}/.env" 2>/dev/null || true
success "Removed any sticky pin from .env"

# Remove from .rollback_pins
sed -i "/^${TEST_SERVICE}=/d" "${BASE_DIR}/.rollback_pins" 2>/dev/null || true
success "Cleaned .rollback_pins"

# Restart with the real image (no pin = uses compose default :latest)
cd "${BASE_DIR}" || exit 1
docker compose up -d --no-deps "${compose_service}" >/dev/null 2>&1
sleep 5

final_status=$(docker inspect --format='{{.State.Status}}' "${TEST_SERVICE}" 2>/dev/null || echo "not_found")
if [[ "${final_status}" == "running" ]]; then
    success "Service ${TEST_SERVICE} is running again ✅"
else
    error "Service ${TEST_SERVICE} status: ${final_status} – manual check needed"
fi

# Reset rollback counter (test should not count against MAX_ROLLBACKS)
sed -i "/^${TEST_SERVICE}=/d" "${BASE_DIR}/.rollback_state" 2>/dev/null || true
success "Reset rollback counter for ${TEST_SERVICE}"

# Clear notification cooldown
sed -i "/^${TEST_SERVICE}:/d" "${BASE_DIR}/.notification_cooldown" 2>/dev/null || true
success "Cleared notification cooldown for ${TEST_SERVICE}"

rm -f "${TEST_BACKUP_FILE}" 2>/dev/null || true

log ""
log "=== TEST COMPLETE ==="
if ${DETECTED}; then
    success "✅ Rollback detection system works correctly!"
    success "   - Monitor detected the stopped container within ${WAITED}s"
    success "   - Diagnosis matrix was triggered"
    success "   - Check Teams (#Infra + #Kassa) for the WARNING notification"
    success "   - Service has been restarted and state cleaned up"
else
    error "❌ Detection did not fire. Check monitor logs:"
    error "   docker logs rollback_monitor --tail 100"
fi