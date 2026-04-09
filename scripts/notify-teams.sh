#!/bin/bash
# =============================================================================
# notify-teams.sh
# ShiftFestival – Infra Team
#
# PURPOSE:
#   Standalone Teams notification helper using Power Automate webhooks.
#   Sends richly formatted Adaptive Cards to Teams channels.
#   Can be called from deploy.yml (GitHub Actions) or runtime-rollback.sh.
#
# USAGE:
#   ./notify-teams.sh \
#     --webhook  "https://default...powerplatform.com/..." \
#     --severity "CRITICAL" \
#     --title    "Deploy failed: frontend_drupal" \
#     --body     "Health check failed after 3 attempts." \
#     --logs     "$(docker logs --tail 20 frontend_drupal 2>&1)"
#
# SEVERITY LEVELS:
#   INFO     → blue header
#   WARNING  → orange header
#   CRITICAL → red header
#   SUCCESS  → green header
#
# If --webhook is empty or missing, the message is printed to stdout only.
# This allows local testing without a real Teams connector.
#
# NOTE: This script uses the Power Automate Adaptive Card format.
#       The webhook URL must come from a Power Automate flow with
#       "When a Teams webhook request is received" trigger, configured
#       with the "PostCardToConversation" action.
# =============================================================================

set -euo pipefail

# -----------------------------------------------------------------------------
# ARGUMENT PARSING
# -----------------------------------------------------------------------------
WEBHOOK_URL=""
SEVERITY="INFO"
TITLE=""
BODY=""
LOGS=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --webhook)  WEBHOOK_URL="$2";  shift 2 ;;
        --severity) SEVERITY="$2";     shift 2 ;;
        --title)    TITLE="$2";        shift 2 ;;
        --body)     BODY="$2";         shift 2 ;;
        --logs)     LOGS="$2";         shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "${TITLE}" ]]; then
    echo "ERROR: --title is required." >&2
    exit 1
fi

# -----------------------------------------------------------------------------
# SEVERITY → COLOUR + EMOJI mapping
# Adaptive Cards use named colors: Good (green), Warning (orange),
# Attention (red), Accent (blue), Default (grey)
# -----------------------------------------------------------------------------
case "${SEVERITY}" in
    CRITICAL) CARD_COLOR="Attention"; EMOJI="🔴" ;;
    WARNING)  CARD_COLOR="Warning";   EMOJI="⚠️"  ;;
    SUCCESS)  CARD_COLOR="Good";      EMOJI="✅"  ;;
    INFO)     CARD_COLOR="Accent";    EMOJI="ℹ️"  ;;
    *)        CARD_COLOR="Default";   EMOJI="📢"  ;;
esac

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# -----------------------------------------------------------------------------
# JSON ESCAPING
# Safely escapes strings for embedding inside a JSON payload.
# Handles backslashes, quotes, and newlines.
# -----------------------------------------------------------------------------
escape_json() {
    printf '%s' "$1" \
        | sed 's/\\/\\\\/g' \
        | sed 's/"/\\"/g' \
        | sed ':a;N;$!ba;s/\n/\\n/g'
}

TITLE_ESCAPED=$(escape_json "${EMOJI} ${TITLE}")
BODY_ESCAPED=$(escape_json "${BODY}")
LOGS_ESCAPED=$(escape_json "${LOGS}")

# -----------------------------------------------------------------------------
# BUILD ADAPTIVE CARD BODY ELEMENTS
# We always include: header, subtitle, body text.
# We conditionally include: log block (only when logs are provided).
# -----------------------------------------------------------------------------
CARD_BODY="[
    {
        \"type\": \"TextBlock\",
        \"text\": \"${TITLE_ESCAPED}\",
        \"weight\": \"Bolder\",
        \"size\": \"Medium\",
        \"color\": \"${CARD_COLOR}\",
        \"wrap\": true
    },
    {
        \"type\": \"TextBlock\",
        \"text\": \"ShiftFestival · ${TIMESTAMP}\",
        \"isSubtle\": true,
        \"size\": \"Small\",
        \"wrap\": true
    },
    {
        \"type\": \"TextBlock\",
        \"text\": \"${BODY_ESCAPED}\",
        \"wrap\": true,
        \"spacing\": \"Medium\"
    }"

# Add log block only when logs are provided
if [[ -n "${LOGS}" ]]; then
    CARD_BODY="${CARD_BODY},
    {
        \"type\": \"TextBlock\",
        \"text\": \"📋 Last log lines:\",
        \"weight\": \"Bolder\",
        \"spacing\": \"Medium\"
    },
    {
        \"type\": \"TextBlock\",
        \"text\": \"${LOGS_ESCAPED}\",
        \"fontType\": \"Monospace\",
        \"size\": \"Small\",
        \"wrap\": true,
        \"spacing\": \"None\"
    }"
fi

CARD_BODY="${CARD_BODY}
]"

# -----------------------------------------------------------------------------
# FULL POWER AUTOMATE PAYLOAD
# Must use the "type: message + attachments" wrapper format that Power Automate
# expects when configured with the "PostCardToConversation" action.
# -----------------------------------------------------------------------------
PAYLOAD="{
    \"type\": \"message\",
    \"attachments\": [
        {
            \"contentType\": \"application/vnd.microsoft.card.adaptive\",
            \"contentUrl\": null,
            \"content\": {
                \"type\": \"AdaptiveCard\",
                \"version\": \"1.4\",
                \"body\": ${CARD_BODY}
            }
        }
    ]
}"

# -----------------------------------------------------------------------------
# SEND OR PRINT (DRY RUN)
# If no webhook URL is configured, print to console for local testing.
# This is the expected behaviour during development before the PM sets up
# the Power Automate flows for all channels.
# -----------------------------------------------------------------------------
if [[ -z "${WEBHOOK_URL}" ]]; then
    echo "=============================================="
    echo "[TEAMS DRY-RUN] ${SEVERITY}: ${TITLE}"
    echo "----------------------------------------------"
    echo "${BODY}"
    if [[ -n "${LOGS}" ]]; then
        echo "--- LOGS ---"
        echo "${LOGS}"
    fi
    echo "=============================================="
    exit 0
fi

# Send the Adaptive Card to the Power Automate webhook
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Content-Type: application/json" \
    -d "${PAYLOAD}" \
    "${WEBHOOK_URL}")

# Power Automate returns 200 or 202 on success
if [[ "${HTTP_STATUS}" == "200" || "${HTTP_STATUS}" == "202" ]]; then
    echo "Teams notification sent successfully (HTTP ${HTTP_STATUS}): ${TITLE}"
else
    echo "ERROR: Teams webhook returned HTTP ${HTTP_STATUS} for: ${TITLE}" >&2
    exit 1
fi