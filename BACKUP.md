# Backup Strategy — ShiftFestival

For the full step-by-step setup and recovery procedures, see **[docs/disaster-recovery.md](docs/disaster-recovery.md)**.

---

## Summary

| What | How | When | Where |
|---|---|---|---|
| 6 databases (3× PostgreSQL, 2× MariaDB, 1× MySQL) | `pg_dump` / `mysqldump` via `kubectl exec` | Daily 02:00 UTC | Backup VM `~/backups/databases/` |
| Infra Git repo | Bare `git mirror` + daily `git fetch` | Daily 03:00 UTC | Backup VM `~/git-mirrors/infra.git` |
| Secrets (`.env`) | GPG-encrypted copy | On every `.env` change | Backup VM `~/secrets/` |

Backups are kept for **14 days**. The backup VM is a **warm standby** — it stores data but does not run Kubernetes simultaneously with the primary VM.

---

## Automation

The `backup.yml` GitHub Actions workflow runs daily and handles both the database dump and the Git mirror sync automatically. No cron job on the VM is needed.

**Required GitHub Secrets** (add in Settings → Secrets → Actions):

| Secret | Value |
|---|---|
| `BACKUP_VM_HOST` | `integration.switzerlandnorth.cloudapp.azure.com` |
| `BACKUP_VM_USER` | `groep1` |
| `BACKUP_SSH_KEY` | Contents of `~/.ssh/backup_key` on the primary VM |

---

## Quick commands

```bash
# Run a manual backup right now (on primary VM)
export BACKUP_VM_USER=groep1
export BACKUP_VM_HOST=integration.switzerlandnorth.cloudapp.azure.com
bash ~/shiftfestival/scripts/backup-databases.sh

# Restore from a specific date
bash ~/shiftfestival/scripts/restore-databases.sh 2026-05-22

# List available backups (on backup VM)
ssh groep1@integration.switzerlandnorth.cloudapp.azure.com "ls ~/backups/databases/"
```

---

## Recovery time

Estimated **30–60 minutes** to restore the full platform on the backup VM after a complete primary VM loss. See `docs/disaster-recovery.md` for the exact steps.
