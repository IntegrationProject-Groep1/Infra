# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Does

This is the central infrastructure repository for **ShiftFestival**, an event management platform. It orchestrates a full microservices stack on an Azure VM using Docker Compose, with automated CI/CD via GitHub Actions, health monitoring, and a two-layer rollback system.

## Common Commands

```bash
# Validate docker-compose syntax locally (requires a .env with dummy values)
cp .env.example .env
docker compose config --quiet

# Lint YAML
yamllint docker-compose.yml

# Lint bash scripts
shellcheck scripts/*.sh

# Run CI checks manually
# → Use GitHub Actions "Run workflow" button on ci.yml

# Emergency deploy (bypasses waiting for a push to main)
# → GitHub Actions → "Deploy Infra to VM" → Run workflow → main
```

## Architecture

The stack runs on a single Azure VM (`/opt/shiftfestival`) and consists of:

**Core Infrastructure:**
- **RabbitMQ** — Central async message broker; all inter-service communication goes through it using team-prefixed queues (e.g., `kassa.orders`, `crm.customer.created`)
- **PostgreSQL 16** — Shared DB (primarily for Identity Service); no external port — connect via `postgredb:5432` on `shift_net` or SSH tunnel
- **ELK Stack** (Elasticsearch + Logstash + Kibana) — Centralized logging; Logstash consumes heartbeat events from RabbitMQ; Elasticsearch bound to localhost only
- **Dozzle** — Docker log viewer
- **Cloudflare Tunnel** — Secure external access; management/admin ports (RabbitMQ UI, pgAdmin, Elasticsearch) are bound to `127.0.0.1` and only reachable via this tunnel
- **Watchtower** — Auto-pulls updated images from GHCR every 60s; only updates containers labeled `com.centurylinklabs.watchtower.enable: true`; ignores SHA-pinned containers
- **docker-proxy** (`tecnativa/docker-socket-proxy`) — Restricted Docker API proxy on isolated `docker-proxy-net`; rollback-monitor connects here instead of the raw socket; blocks BUILD, EXEC, SWARM, SECRETS operations

**Team Services** (6 independent teams, each with their own port range in `300XX`):
- Frontend (Drupal, ports 30020–30029)
- Facturatie (FOSSBilling, 30010–30019)
- Kassa (Odoo POS, 30030–30039)
- CRM (Salesforce receiver, 30040–30049)
- Planning (Office 365 integration, 30050–30059)
- Identity (UUID service, 30070–30100)

Each team service has: main app container + Nginx reverse proxy + heartbeat sidecar.

**Docker Networks:**
- `shift_net` — Main network; all services must be on this
- `elk-net` — Isolated network for ELK components only

## CI/CD Pipeline

**CI (runs on every push/PR):**
1. `docker compose config --quiet` — syntax validation
2. `yamllint` — YAML linting
3. `shellcheck` — bash linting (SC2034 and SC1091 are suppressed)
4. `gitleaks` + manual grep — secret scanning across full git history
5. `trivy` — security scan (HIGH/CRITICAL only, config mode)
6. Best-practice checks: all infra images must use pinned versions (not `:latest`), all services must have explicit Watchtower labels, all services must be on `shift_net`

**Deploy (runs only after CI passes, only on `main`):**
1. SCP repo files to VM (excludes `.github/`, `assets/`, `*.md`)
2. Set deploy lock → pauses rollback monitor
3. Clear sticky rollback pins from `.env`
4. Snapshot current image SHAs → `.previous_tags`
5. `docker compose pull && up -d --remove-orphans`
6. Health checks per service (up to 3 attempts × 30s)
7. On success: update `.stable_tags`, reset rollback counters, notify Teams
8. On failure: roll back to `.previous_tags` SHAs, send CRITICAL Teams alert

## Rollback System

**Layer 1 — Deploy-time:** If health checks fail after a deploy, the pipeline restores exact previous image SHAs (from `.previous_tags`) for each failed service.

**Layer 2 — Runtime (`scripts/runtime-rollback.sh`):** A daemon runs every 30s and applies a 6-step diagnosis before rolling back:
1. Multiple services down? → Infrastructure outage, alert only
2. Container running but heartbeat failing? → Credential/env issue, alert only
3. Service database down? → Alert team, no rollback
4. SHA unchanged from stable? → Config/env issue, no rollback
5. Rollback count ≥ 2? → Hard stop, alert #Infra for manual investigation
6. All checks pass → Execute sticky rollback (writes `SERVICENAME_IMAGE=ghcr.io/…@sha256:…` to `.env`)

**Sticky pins** survive until the next successful deploy, which clears all `_IMAGE=` lines from `.env`.

## Runtime State Files (Not Git-Tracked)

These files are generated at runtime on the VM and are in `.gitignore`:
- `.stable_tags` — last known-good image SHAs
- `.previous_tags` — pre-deploy snapshot
- `.rollback_state` — crash counter per service
- `.deploy_in_progress` — lock file during deploys
- `rollback.lock` — prevents race conditions in rollback daemon
- `.rollback_pins` — active sticky pins
- `pgdata/` — Postgres data directory

## Key Constraints

- **Never commit `.env`** — only `.env.example` goes in git; real secrets live only on the VM
- **All infra images must use pinned versions** (e.g., `postgres:16`, not `postgres:latest`) — CI enforces this
- **All services must have explicit Watchtower labels** — CI enforces `com.centurylinklabs.watchtower.enable: true/false`
- **All services must be on `shift_net`** — CI enforces this (exceptions: kibana-only ELK services)
- **Team-prefixed RabbitMQ queues** — queues must be prefixed with the team name (e.g., `kassa.orders` not `orders`); shared `heartbeat` exchange has no prefix
- **Don't manually edit the VM** — the deploy pipeline overwrites files on each deploy

## Documentation Requirements

All significant changes to this repository **must** be documented. This applies to Claude Code and human contributors alike:

- **New service added** → update `CLAUDE.md` (architecture section) and the port table in `README.md`
- **Security finding identified or fixed** → document it in `SECURITY.md` (severity, affected file with line number, exploit scenario, fix applied)
- **CI/CD pipeline changed** → update the pipeline steps in `CLAUDE.md`
- **Rollback or monitoring logic changed** → update the rollback system description in `CLAUDE.md` and `README.md`
- **New environment variable added** → add it to `.env.example` with a comment explaining its purpose

Documentation must be written in **English**, must be **detailed enough that a new team member can act on it without asking follow-up questions**, and must be kept in sync with the code — stale docs are treated as bugs.

`SECURITY.md` tracks all audit findings and their remediation status. Keep the checklist in that file up to date when issues are fixed.

## Adding a New Team Service

1. Add the service definition to `docker-compose.yml` in the appropriate port range
2. Add Nginx config file (`<team>-nginx.conf`) and reference it in docker-compose
3. Add required env vars to `.env.example` and `.env` on the VM
4. Ensure the service has a heartbeat sidecar publishing to the `heartbeat` RabbitMQ exchange
5. Add Watchtower label (`com.centurylinklabs.watchtower.enable: true` if auto-updating)
6. Add the service to health checks in `.github/workflows/deploy.yml`
