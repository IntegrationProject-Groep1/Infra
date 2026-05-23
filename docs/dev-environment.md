# Dev Environment — Setup and Operations Guide

**Last updated:** 2026-05-24

---

## Stack overview

| Component | Details |
|---|---|
| Namespace | `shift-festival-dev` |
| Storage | Selective — PVCs for application installs (Drupal, FossBilling and their databases), `emptyDir` for ephemeral test data (remaining databases, RabbitMQ) |
| Logging | Shared production Elasticsearch → filter in Kibana on `kubernetes.namespace: shift-festival-dev` |
| RabbitMQ | Separate vhost `shift-festival-dev`, created automatically on `dev-on` |
| Scaling | Manual via GitHub Actions + automatic shutdown after 4 hours of inactivity |
| Images | `:dev` tags via ArgoCD Image Updater |
| Management | Manual `kubectl apply -k` — no ArgoCD auto-sync for dev |
| Monitoring | ArgoCD UI (`argocd.desiderius.me`) for read-only pod status, Kibana for logs |

---

## Why ArgoCD auto-sync is disabled for dev

The VM runs production and dev on the same node. ArgoCD auto-sync would start all dev pods simultaneously, causing CPU spikes that affect production workloads. Dev is therefore managed manually through GitHub Actions workflows with a phased startup sequence.

---

## Scaling logic

| Trigger | Action |
|---|---|
| Push to `dev` branch (any team repo) | `:dev` image built → Image Updater updates `overlays/dev/kustomization.yaml` on `main` → `dev-on` triggers automatically → phased startup |
| 4 hours of inactivity on `overlays/dev/kustomization.yaml` | GitHub Actions cron (runs every hour) → automatic `dev-off` |
| Manual trigger via GitHub Actions UI | `dev-on` or `dev-off` workflow dispatch |

> **Note:** The auto-trigger via Image Updater only works once teams push `:dev` tagged images. Until then, `dev-on` must be triggered manually.

---

## Secret handling

On every `dev-on` run, the following secrets are automatically bootstrapped from the production namespace if they don't exist yet:

- `shift-secrets` — copied from `shift-festival`, then sanitized:
  - `RABBITMQ_VHOST` and `RABBIT_VHOST` → overridden to `shift-festival-dev`
  - External service credentials zeroed out: `SENDGRID_API_KEY`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SF_CLIENT_ID`, `SF_CLIENT_SECRET`, `SF_INSTANCE_URL`, `SF_REFRESH_TOKEN`, `NVIDIA_API_KEY`, `BILLING_API_TOKEN`, all `TEAMS_WEBHOOK_*`
  - Internal credentials kept: DB passwords, RabbitMQ user passwords
- `rabbitmq-definitions` — copied from `shift-festival` (contains RabbitMQ user definitions)
- `cloudflare-tunnel-secret` — copied from `shift-festival`

> **Security:** External service credentials are intentionally disabled in dev. Services that depend on Salesforce, Sendgrid, or Teams webhooks will not function in dev — this is by design.

---

## RabbitMQ vhost

The `shift-festival-dev` vhost is created automatically during Phase 1 of `dev-on` via `rabbitmqctl`. All 9 RabbitMQ users receive full permissions on this vhost:

`crm_rabbitmq`, `facturatie_rabbitmq`, `frontend_rabbitmq`, `guest`, `identity_user`, `infra_admin`, `kassa_rabbitmq`, `monitoring_rabbitmq`, `planning_rabbitmq`

Monitor dev traffic at `dev-rabbitmq.desiderius.me` — switch to the `shift-festival-dev` vhost in the top-right dropdown.

---

## Active services in dev

| Service | Runs in dev | Notes |
|---|---|---|
| RabbitMQ | ✅ | Dev vhost `shift-festival-dev` |
| All databases | ✅ | Test data wiped on restart (emptyDir) |
| Frontend (Drupal + proxy) | ✅ | Install state persisted via PVC |
| Facturatie (FossBilling + proxy) | ✅ | Install state persisted via PVC |
| Identity service | ✅ | |
| CRM + Planning workers | ✅ | Salesforce/O365 credentials zeroed out |
| Kassa integration sidecar | ✅ | |
| Chatbot | ✅ | |
| Cloudflared | ✅ | |
| Kassa (Odoo + proxy) | ❌ | ImagePullBackOff — kassa team image issue |
| Mailing service | ❌ | Intentionally disabled (no SMTP in dev) |
| Heartbeat sidecars | ❌ | Disabled — replicas: 0 |
| MCP services | ❌ | Not required for integration testing |
| ELK stack | ❌ | Too resource-heavy for shared VM |
| pgAdmin | ❌ | |
| Monitoring agent | ❌ | |

---

## Manifest differences versus production

- **PVCs** for `frontend-db`, `facturatie-db`, `frontend-drupal`, `fossbilling-app` — installation and config state is preserved across pod restarts
- **emptyDir** for `postgredb`, `kassa-db`, `crm-db`, `planning-db`, RabbitMQ, pgAdmin — test data is wiped on pod restart (intentional)
- `postgredb` and `planning-db` have an initContainer (busybox chown) to handle emptyDir permissions
- Dev subdomains in Ingress (`dev-*.desiderius.me`)
- Namespace: `shift-festival-dev`
- 20 of 47 workloads run at `replicas: 0`
- CPU requests reduced to `50m` for heavy services to free scheduling headroom

---

## Integration testing per team

### How to test

All teams test via two channels:
1. **UI** — accessible via dev subdomains (see below)
2. **RabbitMQ** — publish/consume messages at [dev-rabbitmq.desiderius.me](https://dev-rabbitmq.desiderius.me), switch to `shift-festival-dev` vhost

Logs for all services are available in Kibana at [kibana.desiderius.me](https://kibana.desiderius.me) — filter on `kubernetes.namespace: shift-festival-dev`.

### Per team

| Team | Test method | Notes |
|---|---|---|
| **Frontend** | Open `dev.desiderius.me` — create users, enroll in sessions, trigger events | Messages visible on RabbitMQ queues |
| **Kassa** | Open `dev-kassa.desiderius.me` — create orders | Blocked — ImagePullBackOff on kassa team image |
| **Facturatie** | Open `dev-facturatie.desiderius.me` — create invoices, test billing flow | Kassa integration blocked until kassa image is fixed |
| **CRM** | No UI — publish test message to `crm.*` queue via RabbitMQ management UI | Salesforce credentials zeroed out in dev — no real Salesforce sync |
| **Planning** | No UI — publish test message to `planning.*` queue via RabbitMQ management UI | Office 365 credentials zeroed out in dev — no real O365 sync |
| **Identity** | Called internally via HTTP — other services trigger it automatically | No direct test needed |
| **Chatbot** | Via frontend or direct RabbitMQ message | |

### What can and cannot be tested in dev

| Flow | Testable in dev |
|---|---|
| Frontend ↔ RabbitMQ ↔ backend workers | ✅ |
| Facturatie invoice creation | ✅ |
| RabbitMQ message routing between services | ✅ |
| Kassa order flow | ❌ Kassa image broken |
| Salesforce CRM sync | ❌ Credentials zeroed out |
| Office 365 Planning sync | ❌ Credentials zeroed out |
| Email sending (mailing service) | ❌ SMTP disabled in dev |

---

## Dev subdomains

| Service | URL |
|---|---|
| Frontend | [dev.desiderius.me](https://dev.desiderius.me) |
| Kassa | [dev-kassa.desiderius.me](https://dev-kassa.desiderius.me) |
| Facturatie | [dev-facturatie.desiderius.me](https://dev-facturatie.desiderius.me) |
| Planning | [dev-planning.desiderius.me](https://dev-planning.desiderius.me) |
| RabbitMQ | [dev-rabbitmq.desiderius.me](https://dev-rabbitmq.desiderius.me) |

---

## Resource impact

| State | RAM usage | CPU (steady-state) |
|---|---|---|
| Dev DOWN | ~55% | ~44% |
| Dev UP | ~65% | ~50–55% |

---

## Task status

### Infrastructure
- [x] Kustomize overlay created (`overlays/dev/`)
- [x] ArgoCD Application created (`argocd/applications/dev-app.yaml`)
- [x] Dev subdomains added to Ingress patches
- [x] DNS records created via Cloudflare (`dev-*.desiderius.me`)
- [x] GitHub Actions `dev-on.yml` workflow (phased startup)
- [x] GitHub Actions `dev-off.yml` workflow (4-hour inactivity cron)
- [x] Selective PVC / emptyDir storage configuration
- [x] Secret bootstrap in `dev-on` — copy + sanitize `shift-secrets`, `rabbitmq-definitions`, `cloudflare-tunnel-secret`
- [x] RabbitMQ vhost `shift-festival-dev` — automated in `dev-on` Phase 1
- [x] Cloudflare Tunnel route for `dev-rabbitmq.desiderius.me`

### Pending (other teams)
- [ ] Kassa team — fix image (`kassa-odoo:odoo-latest` ImagePullBackOff)
- [ ] All teams — update deploy pipeline to push `:dev` tag for auto-trigger

### Monitoring
- [ ] Kibana Data View for `shift-festival-dev`
- [ ] ILM policy: purge dev logs after 1 day

---

## Testing status

- [x] `dev-on` workflow → pods start in phased sequence
- [x] `dev.desiderius.me` → frontend loads
- [x] `dev-facturatie.desiderius.me` → FossBilling loads
- [x] `dev-rabbitmq.desiderius.me` → RabbitMQ management UI accessible
- [x] RabbitMQ dev vhost `shift-festival-dev` → created with correct permissions
- [x] Secret sanitization → external credentials zeroed out on dev-on
- [x] 4-hour inactivity → auto-shutdown confirmed working
- [ ] `dev-kassa.desiderius.me` → blocked by kassa team image issue
- [ ] End-to-end integration test across services via RabbitMQ
