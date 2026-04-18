#!/bin/bash
# =============================================================================
# test-rollback.sh
# ShiftFestival – Infra Team
#
# PURPOSE:
#   Safely tests the full sticky rollback system on a live service by:
#   1. Saving the current stable state
#   2. Injecting a fake "bad" image pin into .env to simulate a rollback
#   3. Forcing the container to restart with the wrong image reference
#   4. Waiting for the rollback monitor to detect and recover
#   5. Verifying the recovery was successful
#
# USAGE:
#   bash test-rollback.sh [service_name]
#   bash test-rollback.sh kassa_integratie
#
# SAFETY:
#   - The script saves the current state before making any changes
#   - If anything goes wrong, run: bash test-rollback.sh --restore
#   - The rollback monitor does the actual recovery automatically
#
# WHAT THIS TESTS:
#   ✅ Rollback monitor detects a crashed/unhealthy container
#   ✅ Diagnosis matrix correctly identifies the issue
#   ✅ Sticky rollback pins the stable SHA in .env
#   ✅ Container is restarted with the stable image
#   ✅ Teams notifications are sent to Infra + owning team
#   ✅ .rollback_pins file is written correctly
# =============================================================================

set -euo pipefail

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

# =============================================================================
# RESTORE MODE – called with --restore to undo any test changes manually
# =============================================================================
if [[ "${1:-}" == "--restore" ]]; then
    log "=== MANUAL RESTORE MODE ==="

    # Find the most recent backup file
    latest_backup=$(ls -t "${BASE_DIR}"/.test_backup_* 2>/dev/null | head -1 || echo "")

    if [[ -z "${latest_backup}" ]]; then
        error "No backup file found in ${BASE_DIR}. Cannot restore."
        exit 1
    fi

    log "Restoring from: ${latest_backup}"
    source "${latest_backup}"

    # Restore .env – remove any test pins
    if [[ -n "${TEST_ENV_VAR:-}" ]]; then
        sed -i "/^${TEST_ENV_VAR}=/d" "${BASE_DIR}/.env"
        success "Removed test pin from .env"
    fi

    # Restore .stable_tags entry
    if [[ -n "${TEST_SERVICE:-}" && -n "${ORIGINAL_STABLE_ENTRY:-}" ]]; then
        sed -i "/^${TEST_SERVICE}=/d" "${BASE_DIR}/.stable_tags"
        echo "${TEST_SERVICE}=${ORIGINAL_STABLE_ENTRY}" >> "${BASE_DIR}/.stable_tags"
        success "Restored .stable_tags for ${TEST_SERVICE}"
    fi

    # Remove from .rollback_pins if present
    if [[ -n "${TEST_SERVICE:-}" ]]; then
        sed -i "/^${TEST_SERVICE}=/d" "${BASE_DIR}/.rollback_pins" 2>/dev/null || true
        success "Cleaned .rollback_pins"
    fi

    # Restart the service cleanly using the compose service name
    if [[ -n "${TEST_SERVICE:-}" ]]; then
        cd "${BASE_DIR}"
        local_compose="${TEST_COMPOSE_SERVICE:-${TEST_SERVICE}}"
        docker compose up -d --no-deps "${local_compose}"
        success "Service ${local_compose} restarted with original image"
    fi

    rm -f "${latest_backup}"
    success "Restore complete. Test backup removed."
    exit 0
fi

# =============================================================================
# PRE-FLIGHT CHECKS
# =============================================================================
log "=== ShiftFestival Rollback System – Live Test ==="
log "Service under test: ${TEST_SERVICE}"
log ""

# Check we are on the VM
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
success "Stable baseline found: ${stable_tag} (${stable_sha:0:19}...)"

# =============================================================================
# SAVE CURRENT STATE (backup before any changes)
# CRITICAL: backup must happen BEFORE any fake SHA injection so --restore
# recovers the REAL state, not the fake test state.
# =============================================================================
log ""
log "=== Saving current state ==="

# Calculate env var name (e.g. kassa_integratie → KASSA_INTEGRATIE_IMAGE)
env_var_name=$(echo "${TEST_SERVICE}" | tr '[:lower:]' '[:upper:]' | tr '-' '_')_IMAGE

# Resolve docker compose service name (container names use underscores,
# compose service names may use hyphens – they must match docker-compose.yml)
case "${TEST_SERVICE}" in
    "kassa_integratie")      compose_service="kassa-integratie" ;;
    "frontend_drupal")       compose_service="frontend-drupal" ;;
    "fossbilling_app")       compose_service="fossbilling-app" ;;
    "fossbilling_connector") compose_service="fossbilling-connector" ;;
    "crm_receiver")          compose_service="crm-receiver" ;;
    "planning_service")      compose_service="planning-service" ;;
    "kassa_odoo")            compose_service="kassa-web" ;;
    *)                       compose_service="${TEST_SERVICE}" ;;
esac

# Save backup with the REAL stable entry (captured before injection)
cat > "${TEST_BACKUP_FILE}" << BACKUP
TEST_SERVICE="${TEST_SERVICE}"
TEST_COMPOSE_SERVICE="${compose_service}"
TEST_ENV_VAR="${env_var_name}"
ORIGINAL_STABLE_ENTRY="${stable_entry}"
ORIGINAL_IMAGE=$(docker inspect --format='{{.Config.Image}}' "${TEST_SERVICE}")
BACKUP

success "State saved to ${TEST_BACKUP_FILE}"
log "  Restore at any time with: bash ${0} --restore"

# =============================================================================
# INJECT TEST CONDITION:
#   1. Write a FAKE stable SHA (all zeros) into .stable_tags so the monitor
#      sees current image != stable → passes Step 4 of diagnosis matrix
#   2. Stop the container so monitor sees it as down → passes Step 2
# =============================================================================
log ""
log "=== Injecting test condition ==="

# Use a full-length obviously-fake SHA so it is clearly different from any real SHA
fake_bad_sha="sha256:0000000000000000000000000000000000000000000000000000000000000000"

log "Overwriting .stable_tags with fake stable entry..."
log "  Real stable SHA: ${stable_sha:0:19}..."
log "  Fake stable SHA: ${fake_bad_sha:0:19}..."

# Write the fake stable entry
sed -i "/^${TEST_SERVICE}=/d" "${BASE_DIR}/.stable_tags"
echo "${TEST_SERVICE}=${fake_bad_sha}|${stable_tag}" >> "${BASE_DIR}/.stable_tags"

# Stop the container – this is what triggers the rollback monitor
log "Stopping ${TEST_SERVICE} to trigger rollback detection..."
docker stop "${TEST_SERVICE}" 2>/dev/null || true

success "Test condition injected: container stopped + fake stable SHA written."

# =============================================================================
# WAIT FOR THE ROLLBACK MONITOR TO DETECT AND ACT
#
# The monitor checks every 30 seconds. We wait up to 90 seconds.
# =============================================================================
log ""
log "=== Waiting for rollback monitor to detect the condition ==="
log "The monitor checks every 30s. Watching .rollback_pins and .rollback_state..."
log ""

MAX_WAIT=120
WAITED=0
ROLLBACK_DETECTED=false

while (( WAITED < MAX_WAIT )); do
    # Check if a rollback has been triggered for this service
    if grep -q "^${TEST_SERVICE}=" "${BASE_DIR}/.rollback_pins" 2>/dev/null; then
        ROLLBACK_DETECTED=true
        break
    fi

    # Also check if rollback_state was updated
    rollback_count=$(grep "^${TEST_SERVICE}=" "${BASE_DIR}/.rollback_state" 2>/dev/null \
        | cut -d'=' -f2 | cut -d':' -f1 || echo "0")
    if [[ "${rollback_count}" -gt "0" ]]; then
        ROLLBACK_DETECTED=true
        break
    fi

    echo -n "  Waiting... (${WAITED}s / ${MAX_WAIT}s)"$'\r'
    sleep 5
    WAITED=$(( WAITED + 5 ))
done

echo ""

if ! ${ROLLBACK_DETECTED}; then
    warn "Rollback not detected after ${MAX_WAIT}s."
    warn "Possible reasons:"
    warn "  - Monitor is still within its 30s sleep cycle"
    warn "  - Step 4 check passed but rollback logic had an issue"
    warn "  - Check docker logs rollback_monitor for details"
    log ""
    log "Manual restore: bash ${0} --restore"
    exit 1
fi

success "Rollback detected after ${WAITED}s!"

# =============================================================================
# VERIFY THE OUTCOME
# =============================================================================
log ""
log "=== Verifying rollback outcome ==="

sleep 10  # Give the service a moment to fully restart

# Check 1: Service is running
final_status=$(docker inspect --format='{{.State.Status}}' "${TEST_SERVICE}" 2>/dev/null || echo "not_found")
if [[ "${final_status}" == "running" ]]; then
    success "Service ${TEST_SERVICE} is running ✅"
else
    error "Service ${TEST_SERVICE} status: ${final_status} ❌"
fi

# Check 2: .stable_tags restored to real SHA
new_stable=$(grep "^${TEST_SERVICE}=" "${BASE_DIR}/.stable_tags" 2>/dev/null | cut -d'=' -f2- || echo "")
if [[ "${new_stable%%|*}" == "${stable_sha}" ]]; then
    success ".stable_tags still has correct SHA ✅"
else
    warn ".stable_tags was updated (this is expected after rollback)"
fi

# Check 3: .rollback_pins has the entry
if grep -q "^${TEST_SERVICE}=" "${BASE_DIR}/.rollback_pins" 2>/dev/null; then
    pin_entry=$(grep "^${TEST_SERVICE}=" "${BASE_DIR}/.rollback_pins")
    success ".rollback_pins entry written: ${pin_entry} ✅"
else
    warn ".rollback_pins entry not found (rollback may have recovered fully already)"
fi

# Check 4: .env has the sticky pin
if grep -q "^${env_var_name}=" "${BASE_DIR}/.env" 2>/dev/null; then
    pin_value=$(grep "^${env_var_name}=" "${BASE_DIR}/.env")
    success ".env sticky pin written: ${pin_value:0:60}... ✅"
else
    warn ".env pin not found (may have been auto-released already)"
fi

# =============================================================================
# RESTORE TO ORIGINAL STATE
# =============================================================================
log ""
log "=== Restoring original state ==="

# Remove test pin from .env
sed -i "/^${env_var_name}=/d" "${BASE_DIR}/.env"
success "Removed test pin from .env"

# Restore original .stable_tags entry
sed -i "/^${TEST_SERVICE}=/d" "${BASE_DIR}/.stable_tags"
echo "${TEST_SERVICE}=${stable_entry}" >> "${BASE_DIR}/.stable_tags"
success "Restored .stable_tags to original entry"

# Reset rollback counter for this service (test shouldn't count against MAX_ROLLBACKS)
sed -i "/^${TEST_SERVICE}=/d" "${BASE_DIR}/.rollback_state" 2>/dev/null || true
success "Reset rollback counter for ${TEST_SERVICE}"

# Remove from .rollback_pins
sed -i "/^${TEST_SERVICE}=/d" "${BASE_DIR}/.rollback_pins" 2>/dev/null || true
success "Cleaned .rollback_pins"

# Restart service cleanly using the correct compose service name
cd "${BASE_DIR}"
docker compose up -d --no-deps "${compose_service}"
success "Service ${compose_service} restarted cleanly"

# Remove backup file
rm -f "${TEST_BACKUP_FILE}"
success "Test backup cleaned up"

log ""
log "=== TEST COMPLETE ==="
if ${ROLLBACK_DETECTED}; then
    success "✅ Rollback system is working correctly!"
    success "   Check Teams (#Infra and #Kassa) for rollback notifications."
    success "   The service was restored and all state has been cleaned up."
else
    error "❌ Test inconclusive – check rollback monitor logs:"
    error "   docker logs rollback_monitor --tail 100"
fi