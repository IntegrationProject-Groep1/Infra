#!/bin/bash
# =============================================================================
# DEPRECATED — DO NOT RUN
#
# This script was a systemd-based health-monitor that polled containers every
# 30 seconds and triggered rollbacks by restarting Docker containers directly.
# It pre-dates the current Kubernetes/Argo Rollouts architecture and is
# incompatible with it.
#
# Replacement: Argo Rollouts handles automatic rollback via progressDeadlineSeconds.
# For manual rollbacks use:
#   kubectl argo rollouts undo <rollout-name> -n shift-festival
#
# See CLAUDE.md → "Rollback and Recovery" for the full rollback runbook.
# Kept for historical reference only.
# =============================================================================
# runtime-rollback.sh
# ShiftFestival – Infra Team
#
# PURPOSE:
#   Runs as a systemd-managed service on the Azure VM.
#   Every 30 seconds it checks all monitored containers. If a container is
#   unhealthy or stopped, it works through a 6-step diagnosis matrix before
#   deciding whether to roll back, alert the team, or do nothing.
# ... (rest of the script content from turn 6)
