# Kustomize Overlays

This directory contains the environment-specific configurations for the ShiftFestival platform using Kustomize overlays.

## Environments

- `prod/`: Production environment. Deploys to the `shift-festival` namespace.
- `dev/`: Development environment. Deploys to the `shift-festival-dev` namespace.

## Structure of an Overlay

Each overlay typically contains:
- `kustomization.yaml`: The entry point that references the `base/` manifests and applies environment-specific changes (namespace, labels, patches).
- `namespace.yaml`: Defines the Namespace object for the environment.
- `.env.example`: A template for the required environment variables (secrets).

## Usage

To render an overlay:
```bash
# Production
kubectl kustomize overlays/prod

# Development
kubectl kustomize overlays/dev
```

To apply an overlay (Note: Primary deployment is handled by ArgoCD):
```bash
kubectl apply -k overlays/prod
```

## Secret Management

Secrets are NOT managed by Kustomize in this repository to prevent accidental commits of sensitive data. Instead, they are bootstrapped manually on the cluster using the `scripts/create-secret.sh` script from a local `.env` file.

Each overlay directory contains a `.env.example` file listing the required keys.
