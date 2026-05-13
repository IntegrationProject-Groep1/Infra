#!/bin/bash
# Script to check which images are running and their SHAs
# Usage: ./scripts/check-image-versions.sh [namespace]

NAMESPACE="${1:-shift-festival}"

echo "==================================="
echo "Image versions in namespace: $NAMESPACE"
echo "==================================="
echo ""

# Get all pods and their images
kubectl get pods -n $NAMESPACE -o json | jq -r '.items[] | 
    select(.status.containerStatuses != null) |
    "\(.metadata.name) -> \(.spec.containers[0].image)"' | sort | uniq

echo ""
echo "==================================="
echo "Current Image SHAs in use:"
echo "==================================="
echo ""

# Show the actual running image with digest
kubectl get pods -n $NAMESPACE -o json | jq -r '.items[] |
    select(.status.containerStatuses != null) |
    "\(.metadata.name): \(.status.containerStatuses[0].imageID)"' | sort

echo ""
echo "==================================="
echo "Expected images from ArgoCD:"
echo "==================================="
echo ""

# Show what's in the root kustomization.yaml
if [ -f "kustomization.yaml" ]; then
    grep -A 100 "images:" kustomization.yaml | grep -E "name:|newTag:" | sed 's/^[[:space:]]*//'
else
    echo "⚠️  kustomization.yaml not found"
fi
