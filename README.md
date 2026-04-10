# ShiftFestival – Team Infra

![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?style=for-the-badge&logo=docker&logoColor=white)
![GitHub Actions](https://img.shields.io/badge/github%20actions-%232671E5.svg?style=for-the-badge&logo=githubactions&logoColor=white)
![Ubuntu](https://img.shields.io/badge/Ubuntu-E95420?style=for-the-badge&logo=ubuntu&logoColor=white)
![Nginx](https://img.shields.io/badge/nginx-%23009639.svg?style=for-the-badge&logo=nginx&logoColor=white)
![RabbitMQ](https://img.shields.io/badge/Rabbitmq-FF6600?style=for-the-badge&logo=rabbitmq&logoColor=white)
![Elasticsearch](https://img.shields.io/badge/Elastic_Stack-005571?style=for-the-badge&logo=elasticsearch&logoColor=white)

> Central infrastructure repository for the ShiftFestival integration project.
> Manages the Azure VM, Docker Compose stack, CI/CD pipelines, and automated rollback system for all teams.

---

## 📋 Table of Contents

- [Architecture Overview](#architecture-overview)
- [Live Dashboards](#live-dashboards)
- [Repository Structure](#repository-structure)
- [CI/CD Pipeline](#cicd-pipeline)
- [Rollback System](#rollback-system)
- [Port Allocation](#port-allocation)
- [Instructions for Development Teams](#instructions-for-development-teams)
- [Maintenance](#maintenance)

---

## Architecture Overview

The ShiftFestival platform runs as a microservices stack on a single Azure VM (Ubuntu) orchestrated via Docker Compose. All services communicate over an internal Docker network (`shift_net`) with SSL termination handled by per-service Nginx proxies.

```
┌─────────────────────────────────────────────────────────────────┐
│  Azure VM – integrationproject-2526s2-dag01.westeurope.azure... │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │   RabbitMQ   │  │  PostgreSQL  │  │      ELK Stack       │   │
│  │ (msg broker) │  │ (identity)   │  │ (logs & monitoring)  │   │
│  └──────┬───────┘  └──────┬───────┘  └──────────────────────┘   │
│         │                 │                                     │
│  ┌──────┴─────────────────┴─────────────────────────────────┐   │
│  │                    shift_net (Docker network)            │   │
│  └──────┬────────┬────────┬────────┬────────┬───────────────┘   │
│         │        │        │        │        │                   │
│    Frontend  Facturatie  Kassa    CRM   Planning  Identity      │
│    (Drupal) (FOSSBill.) (Odoo)         (O365)   (UUID svc)      │
└─────────────────────────────────────────────────────────────────┘
```

**Key components:**
- **RabbitMQ** — Central AMQP message broker. All inter-service communication flows through here.
- **PostgreSQL 16** — Shared database used by the Identity Service.
- **ELK Stack** — Centralized logging (Elasticsearch + Logstash + Kibana). Heartbeats from all services are collected here.
- **Watchtower** — Automatically pulls and restarts containers when a new image is pushed to GHCR.
- **Rollback Monitor** — Custom daemon that detects container crashes and performs sticky rollbacks.

---

## Live Dashboards

| Dashboard | URL |
| :--- | :--- |
| 📋 **Log Viewer (Dozzle)** | https://integrationproject-2526s2-dag01.westeurope.cloudapp.azure.com:30002 |
| 🐰 **RabbitMQ Management** | https://integrationproject-2526s2-dag01.westeurope.cloudapp.azure.com:30001 |
| 📊 **Kibana** | https://integrationproject-2526s2-dag01.westeurope.cloudapp.azure.com:30061 |

> All dashboards use self-signed SSL certificates. Accept the browser warning to proceed.

---

## Repository Structure

```
.
├── .github/
│   └── workflows/
│       ├── ci.yml              # Validation, linting, security scanning
│       └── deploy.yml          # Deploy to VM + health checks + rollback
│
├── scripts/
│   ├── runtime-rollback.sh     # Runtime crash detection & sticky rollback daemon
│   └── notify-teams.sh         # Teams notification helper (Power Automate)
│
├── assets/                     # README images (not deployed to VM)
│
├── docker-compose.yml          # Full stack definition for the Azure VM
├── rabbitmq.conf               # RabbitMQ broker configuration (SSL, policies)
├── dozzle-nginx.conf           # Nginx reverse proxy config for Dozzle
├── frontend-nginx.conf         # Nginx reverse proxy config for Drupal
├── fossbilling-nginx.conf      # Nginx reverse proxy config for FOSSBilling
├── kassa-nginx.conf            # Nginx reverse proxy config for Odoo
├── kibana-nginx.conf           # Nginx reverse proxy config for Kibana
├── odoo.conf                   # Odoo application configuration
└── .env.example                # Template for required environment variables
```

> **Note:** The `.env` file containing real credentials lives **only** on the VM at `/opt/shiftfestival/.env` and is never committed to git.

---

## CI/CD Pipeline

Every push to `main` triggers the full pipeline:

```
Push to main
    │
    ▼
┌─────────────────────────────────────────┐
│           CI Pipeline (ci.yml)          │
│                                         │
│  1. Validate docker-compose.yml syntax  │
│  2. Lint YAML files                     │
│  3. Lint bash scripts (shellcheck)      │
│  4. Security scan (gitleaks + trivy)    │
│  5. Check image pinning & labels        │
└──────────────────┬──────────────────────┘
                   │ CI passes
                   ▼
┌─────────────────────────────────────────┐
│          Deploy Pipeline (deploy.yml)   │
│                                         │
│  1. Copy repo files to VM via SCP       │
│  2. Clear sticky rollback pins          │
│  3. Snapshot current image SHAs         │
│  4. docker compose pull && up -d        │
│  5. Health checks (3 attempts × 30s)    │
│                                         │
│  ✅ All healthy → update .stable_tags  │
│  ❌ Any failed  → rollback + alert     │
└─────────────────────────────────────────┘
```

**Teams is notified** on both success and failure via Power Automate webhooks (Adaptive Card format).

---

## Rollback System

The infra stack has a **two-layer rollback system**:

### Layer 1 – Deploy-time rollback (`deploy.yml`)
If a health check fails after a deploy, the pipeline automatically restores every service to the exact image SHA it was running before the deploy started.

### Layer 2 – Runtime rollback (`runtime-rollback.sh`)
A daemon running as a Docker container (`rollback_monitor`) checks all services every 30 seconds. If a container crashes, it runs a **6-step diagnosis matrix** before acting:

| Step | Check | Action if failed |
| :--: | :--- | :--- |
| 1 | Multiple services down simultaneously? | Infra outage → alert, no rollback |
| 2 | Container still running? | Config/env issue → alert, no rollback |
| 3 | Service database healthy? | DB issue → alert owning team, no rollback |
| 4 | Image different from stable baseline? | Same image → config issue, no rollback |
| 5 | Max rollback attempts reached? | Hard stop → alert infra |
| 6 | Execute **sticky rollback** | Pull stable SHA, pin in `.env`, restart |

### Sticky Rollback
When a rollback is executed, the stable image SHA is written into `.env`:
```
FRONTEND_DRUPAL_IMAGE=ghcr.io/org/frontend@sha256:abc123...
```
Because `docker-compose.yml` reads this variable (`${FRONTEND_DRUPAL_IMAGE:-ghcr.io/.../frontend:latest}`), the service stays pinned to the exact stable version. **Watchtower ignores containers running on a SHA digest**, so it cannot overwrite the rollback.

The pin is automatically cleared on the next successful deploy.

### State Files
These files are managed at runtime on the VM and are **never committed to git**:

| File | Purpose |
| :--- | :--- |
| `/opt/shiftfestival/.stable_tags` | Last known-good image SHA per service |
| `/opt/shiftfestival/.previous_tags` | Pre-deploy snapshot (for deploy-time rollback) |
| `/opt/shiftfestival/.rollback_state` | Rollback attempt counter per service |
| `/opt/shiftfestival/rollback.lock` | Anti-race lock during rollback execution |

---

## Port Allocation

Each team has a dedicated port range on the Azure VM:

| Team / Service | Port Range | Current Service |
| :--- | :---: | :--- |
| **Team Infra** | 30000–30009 | RabbitMQ (30000, 30001), Dozzle (30002) |
| **Team Facturatie** | 30010–30019 | FOSSBilling (30010) |
| **Team Frontend** | 30020–30029 | Drupal (30020) |
| **Team Kassa** | 30030–30039 | Odoo POS (30030) |
| **Team CRM** | 30040–30049 | CRM Receiver (30040) |
| **Team Planning** | 30050–30059 | Planning Service (30050) |
| **Team Monitoring** | 30060–30069 | Elasticsearch (30060), Kibana (30061) |
| **Shared Infra** | Various | PostgreSQL (30004), pgAdmin (30005) |
| **Buffer** | 30070–30100 | Identity Service (reserved) |

> Only use ports within your assigned range. Contact Team Infra before reserving a new port.

---

## Instructions for Development Teams

### Getting your service onto the VM

Your repository must meet the following requirements before Team Infra can add your service to the stack:

**Required files:**
- [ ] `Dockerfile` in the repository root — must build successfully
- [ ] `.env.example` — all required environment variables listed (no real secrets)
- [ ] `.gitignore` / `.dockerignore` — excludes `.env`, `node_modules`, build artifacts
- [ ] `EXPOSE <port>` in your Dockerfile — so we know which port to route to

**Your CI/CD pipeline:**

Copy the pipeline template from our repository to your `.github/workflows/deploy.yml`. This pipeline builds your Docker image and pushes it to GHCR (`ghcr.io/integrationproject-groep1/<your-service-name>`). Watchtower on the VM will detect the new image and restart your container automatically within 60 seconds.


### How to create a Tagged Release

To trigger the deploy pipeline for your service, create a new Tag/Release in GitHub:

![Demo: How to create a Tagged Release](assets/tag-release.gif)

### RabbitMQ Naming Convention

All teams share the default `/` virtual host. To prevent conflicts, **always prefix your queue and exchange names with your team name**:

```
✅  kassa.orders
✅  crm.customer.created
✅  planning.session.updated
❌  orders              (no prefix – causes conflicts)
❌  customer_queue      (no prefix – causes conflicts)
```

**Exception:** The `heartbeat` exchange used by Team Monitoring is shared across all teams without a prefix.

**Connection details (use inside Docker network):**
```
Host:     rabbitmq_broker
Port:     5672
VHost:    /
```

Your RabbitMQ credentials are either assigned by Team Infra **or chosen by your team** — in that case, pass them to Team Infra so we can add them to the VM `.env` file.

### Heartbeat requirement

Every service must include a heartbeat sidecar container that sends a status signal to RabbitMQ every second. Team Monitoring uses these signals to display the live status of all services in the Kibana dashboard.

Use the shared heartbeat image:
```yaml
image: ghcr.io/integrationproject-groep1/heartbeat:latest
environment:
  - SYSTEM_NAME=<your-team-name>
  - TARGETS=<your-container-name>:<your-port>
  - RABBITMQ_HOST=rabbitmq_broker
  - RABBITMQ_USER=${RABBITMQ<YOURTEAM>_USER}
  - RABBITMQ_PASS=${RABBITMQ<YOURTEAM>_PASS}
  - RABBITMQ_VHOST=/
```

---

---

## Maintenance

> ⚠️ **This section is for Team Infra only.** Other teams do not have VM access.

### Making changes to the infrastructure

1. Create a feature branch from `main`
2. Make your changes
3. Open a Pull Request — CI will validate automatically
4. Merge to `main` — deploy pipeline runs automatically after CI passes

> **Never make manual changes directly on the VM** without first discussing with Team Infra. Manual changes will be overwritten by the next deploy.

### Emergency manual deploy

If you need to deploy immediately without waiting for CI:

1. Go to **Actions** → **Deploy Infra to VM**
2. Click **Run workflow** → select `main` → **Run workflow**

### VM Commands

```bash
# Check all containers
docker ps

# Check rollback monitor logs
docker logs rollback_monitor --tail 50

# Check stable image baselines
cat /opt/shiftfestival/.stable_tags

# Check rollback counters
cat /opt/shiftfestival/.rollback_state
```

---

*IntegrationProject-Groep1 · ShiftFestival 2026 · Erasmushogeschool Brussel*
