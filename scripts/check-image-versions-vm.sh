#!/bin/bash
# Script to check image versions on the VM itself
# Run this ON your Azure VM (inside the shift-festival namespace)

NAMESPACE="shift-festival"

echo "==================================="
echo "Image versions in namespace: $NAMESPACE"
echo "==================================="
echo ""

# Get all pods and their images
echo "📦 Running Pods & their Images:"
echo ""
kubectl get pods -n $NAMESPACE -o wide | awk '{print $1, $2}' | column -t

echo ""
echo "==================================="
echo "Current Image SHAs in use:"
echo "==================================="
echo ""

# Show the actual running image with digest for each pod
for pod in $(kubectl get pods -n $NAMESPACE --no-headers -o custom-columns=NAME:.metadata.name); do
    echo "🔍 Pod: $pod"
    kubectl get pod $pod -n $NAMESPACE -o jsonpath='{.status.containerStatuses[*].imageID}' | sed 's/^/  └─ /'
    echo ""
done

echo "==================================="
echo "Quick Summary:"
echo "==================================="
echo ""
kubectl get pods -n $NAMESPACE -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[0].image}{"\n"}{end}' | column -t -s $'\t'

echo ""
echo "✅ All pods running with their current images shown above"
