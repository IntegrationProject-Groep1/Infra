#!/bin/bash
# =============================================================================
# check-image-versions.sh
# ShiftFestival — Infra Team
#
# PURPOSE:
#   Diagnostic tool to verify that an image update has landed as expected.
#   Compares three layers of truth:
#     1. spec.containers[].image   — the tag ArgoCD scheduled (from kustomization.yaml)
#     2. status.containerStatuses[].imageID — the actual SHA currently running
#     3. kustomization.yaml images block    — what Image Updater last committed to Git
#
#   If layer 1 and 3 match but layer 2 shows a different digest, the new image
#   has been committed to Git but the pod has not restarted yet.
#   If all three match, the update is complete and running.
#
# USAGE:
#   ./scripts/check-image-versions.sh [namespace]
#   Default namespace: shift-festival
# =============================================================================

NAMESPACE="${1:-shift-festival}"

echo "==================================="
echo "Image versions in namespace: $NAMESPACE"
echo "==================================="
echo ""

# Layer 1: scheduled image tag from the pod spec (what ArgoCD applied).
# spec.containers[0].image contains the tag or digest that was set when the
# pod was last created — this reflects the last ArgoCD sync, not the running SHA.
kubectl get pods -n $NAMESPACE -o json | jq -r '.items[] |
    select(.status.containerStatuses != null) |
    "\(.metadata.name) -> \(.spec.containers[0].image)"' | sort | uniq

echo ""
echo "==================================="
echo "Current Image SHAs in use:"
echo "==================================="
echo ""

# Layer 2: actual running image digest (the real pulled SHA).
# status.containerStatuses[].imageID is populated by the kubelet after the
# image is pulled and contains the full sha256 digest — this is what is
# literally executing in the container, regardless of what the tag resolves to.
kubectl get pods -n $NAMESPACE -o json | jq -r '.items[] |
    select(.status.containerStatuses != null) |
    "\(.metadata.name): \(.status.containerStatuses[0].imageID)"' | sort

echo ""
echo "==================================="
echo "Expected images from ArgoCD:"
echo "==================================="
echo ""

# Layer 3: what ArgoCD Image Updater last committed to Git.
# Image Updater writes newName/newTag overrides into the root kustomization.yaml.
# This is the source of truth for what ArgoCD will deploy on the next sync.
if [ -f "kustomization.yaml" ]; then
    grep -A 100 "images:" kustomization.yaml | grep -E "name:|newTag:" | sed 's/^[[:space:]]*//'
else
    echo "⚠️  kustomization.yaml not found — run this script from the repo root"
fi
