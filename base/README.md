# Base Manifests

This directory contains the base Kubernetes manifests for the ShiftFestival platform. These manifests define the core resources (Deployments, Services, PVCs, etc.) without environment-specific configurations like namespaces.

## Directory Structure

- `setup/`: Foundation resources like Namespaces, Storage (PVCs), and ConfigMaps.
- `core/`: Platform-wide shared services (RabbitMQ, PostgreSQL, Cloudflared, Dashboard).
- `team-frontend/`: Drupal stack for the Frontend team.
- `team-kassa/`: Odoo stack for the Kassa team.
- `team-facturatie/`: FossBilling stack for the Facturatie team.
- `integrations/`: Background workers and integration services (CRM, Planning, Identity).
- `monitoring/`: Observability stack (ELK, monitoring agents).

## Usage

These manifests are intended to be used as a base for Kustomize overlays. They should not be applied directly to a cluster.

To render the base manifests (for debugging purposes):
```bash
kubectl kustomize base/
```

To add a new service:
1. Create a new subdirectory or add to an existing one.
2. Add your YAML manifests.
3. Update the `kustomization.yaml` in that subdirectory to include the new files.
4. Ensure the root `base/kustomization.yaml` (if you added a new directory) or the relevant parent directory's `kustomization.yaml` is updated.

## Conventions

- **Labels**: All resources should have the `app` label set to the service name. Common labels like `project: shift-festival` are applied automatically by the overlay layer.
- **Namespacing**: Do NOT set a `namespace` field in these manifests. Namespacing is handled by the overlays.
- **Secrets**: Reference secrets via the `shift-secrets` name. Do not include sensitive data here.
