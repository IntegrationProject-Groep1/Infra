# Operational Scripts

This directory contains utility and automation scripts used for managing the ShiftFestival infrastructure.

## Key Scripts

| Script | Purpose |
|---|---|
| `create-secret.sh` | Bootstraps the `shift-secrets` Kubernetes Secret from a local `.env` file. |
| `notify-teams.sh` | Sends formatted notifications to Microsoft Teams via webhooks. |
| `check-image-versions.sh` | Compares running image versions with what is defined in Git. |
| `migrate_identity_service.py` | Helper script for database migrations (Identity Service). |

## Usage

Most scripts should be run from the root of the repository.

### Creating Secrets
```bash
./scripts/create-secret.sh setup/.env shift-festival
```

### Sending a Notification
```bash
./scripts/notify-teams.sh --severity INFO --title "Test" --body "This is a test message"
```

## Legacy Scripts

The following scripts are kept for reference or legacy purposes and should be used with caution:
- `runtime-rollback.sh`: Deprecated in favor of ArgoCD's built-in rollback and self-healing features.
- `test-rollback.sh`: Used for testing the deprecated rollback logic.

## Safety & Style

- All bash scripts should use `set -euo pipefail` for robust error handling.
- Shell scripts are linted via `shellcheck` in the CI pipeline.
- Python scripts should follow PEP 8 and include basic documentation.
