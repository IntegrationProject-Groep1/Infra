# PowerShell script to check which images are running and their SHAs
# Usage: .\scripts\check-image-versions.ps1 [-Namespace shift-festival]

param(
    [string]$Namespace = "shift-festival"
)

Write-Host "===================================" -ForegroundColor Cyan
Write-Host "Image versions in namespace: $Namespace" -ForegroundColor Cyan
Write-Host "===================================" -ForegroundColor Cyan
Write-Host ""

# Get all pods and their images
Write-Host "Running Pods & Images:" -ForegroundColor Yellow
$pods = kubectl get pods -n $Namespace -o json | ConvertFrom-Json
$pods.items | Where-Object { $_.status.containerStatuses } | ForEach-Object {
    $podName = $_.metadata.name
    $image = $_.spec.containers[0].image
    Write-Host "$podName -> $image"
} | Sort-Object

Write-Host ""
Write-Host "===================================" -ForegroundColor Cyan
Write-Host "Current Image SHAs in use:" -ForegroundColor Cyan
Write-Host "===================================" -ForegroundColor Cyan
Write-Host ""

# Show the actual running image with digest
$pods.items | Where-Object { $_.status.containerStatuses } | ForEach-Object {
    $podName = $_.metadata.name
    $imageId = $_.status.containerStatuses[0].imageID
    Write-Host "$podName`:"
    Write-Host "  $imageId" -ForegroundColor Green
} | Sort-Object

Write-Host ""
Write-Host "===================================" -ForegroundColor Cyan
Write-Host "Expected images from ArgoCD:" -ForegroundColor Cyan
Write-Host "===================================" -ForegroundColor Cyan
Write-Host ""

# Show what's in the ArgoCD source file
$argocdFile = "overlays\prod\.argocd-source-shift-festival-prod.yaml"
if (Test-Path $argocdFile) {
    Get-Content $argocdFile | Select-String "sha256" | ForEach-Object {
        Write-Host $_.Line.Trim() -ForegroundColor Green
    }
} else {
    Write-Host "⚠️  .argocd-source-shift-festival-prod.yaml not found" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "===================================" -ForegroundColor Cyan
Write-Host "Comparison:" -ForegroundColor Cyan
Write-Host "===================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "✅ If running SHAs match the expected SHAs, images are correct!" -ForegroundColor Green
