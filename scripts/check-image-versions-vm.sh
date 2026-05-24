#!/bin/bash
# =============================================================================
# check-image-versions-vm.sh
# ShiftFestival — Infra Team
#
# PURPOSE:
#   Terminal-friendly version of check-image-versions.sh, designed to be run
#   directly on the Azure VM where column-aligned output is readable in the
#   SSH session. Shows pod status, running image digests, and a quick summary.
#
#   Use check-image-versions.sh from a local machine for the three-layer
#   comparison against kustomization.yaml. Use this script on the VM for a
#   quick visual overview of what is actually running.
#
# USAGE:
#   ssh ehbstudent@<vm-host> -p 60022
#   bash ~/shiftfestival/scripts/check-image-versions-vm.sh
# =============================================================================

NAMESPACE="shift-festival"

echo "==================================="
echo "Image versions in namespace: $NAMESPACE"
echo "==================================="
echo ""

# awk '{print $1, $2}' extracts pod name and STATUS columns from kubectl wide output.
# column -t aligns the output into readable fixed-width columns in the terminal.
echo "📦 Running Pods & their Images:"
echo ""
kubectl get pods -n $NAMESPACE -o wide | awk '{print $1, $2}' | column -t

echo ""
echo "==================================="
echo "Current Image SHAs in use:"
echo "==================================="
echo ""

# --no-headers -o custom-columns=NAME:.metadata.name: lists only pod names with
# no header row, suitable for iteration in a for loop.
# jsonpath='{.status.containerStatuses[*].imageID}' returns the full pulled digest
# (e.g. docker.io/library/nginx@sha256:abc123...) for every container in the pod.
# This is what is actually executing — not the tag name, which can change.
for pod in $(kubectl get pods -n $NAMESPACE --no-headers -o custom-columns=NAME:.metadata.name); do
    echo "🔍 Pod: $pod"
    kubectl get pod $pod -n $NAMESPACE -o jsonpath='{.status.containerStatuses[*].imageID}' | sed 's/^/  └─ /'
    echo ""
done

echo "==================================="
echo "Quick Summary:"
echo "==================================="
echo ""
# column -t -s $'\t' formats tab-separated jsonpath output into aligned columns.
kubectl get pods -n $NAMESPACE -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[0].image}{"\n"}{end}' | column -t -s $'\t'

echo ""
echo "✅ All pods running with their current images shown above"
