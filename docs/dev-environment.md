# Dev Environment — Setup and Operations Guide

**Last updated:** 2026-05-24

---

## Stack overview

| Component | Details |
|---|---|
| Namespace | `shift-festival-dev` |
| Storage | Selective — PVCs for application installs (Drupal, FossBilling, Kassa/Odoo and their databases), `emptyDir` for ephemeral test data (remaining databases, RabbitMQ) |
| Logging | Shared production Elasticsearch → filter in Kibana on `kubernetes.namespace: shift-festival-dev` |
| RabbitMQ | Separate vhost `shift-festival-dev`, created automatically on `dev-on` |
| Scaling | Manual via GitHub Actions + automatic shutdown after 4 hours of inactivity |
| Images | `:latest-dev` tags via ArgoCD Image Updater |
| Management | Manual `kubectl apply -k` — no ArgoCD auto-sync for dev |
| Monitoring | ArgoCD UI (`argocd.desiderius.me`) for read-only pod status, Kibana for logs |

---

## Why ArgoCD auto-sync is disabled for dev

The VM runs production and dev on the same node. ArgoCD auto-sync would start all dev pods simultaneously, causing CPU spikes that affect production workloads. Dev is therefore managed manually through GitHub Actions workflows with a phased startup sequence.

> **ArgoCD OutOfSync is expected for dev.** After `dev-off` shuts the environment down, ArgoCD will show the dev app as OutOfSync. This is by design — the app is suspended, not broken. Use ArgoCD only to check pod status, not sync state.

---

## Scaling logic

| Trigger | Action |
|---|---|
| Push to `dev` branch (any team repo) | `:latest-dev` image built → Image Updater updates `overlays/dev/kustomization.yaml` on `main` → `dev-on` triggers automatically → phased startup |
| 4 hours since last `dev-on` (tracked via ConfigMap on cluster) | GitHub Actions cron (runs every 2 hours) → automatic `dev-off` |
| Manual trigger via GitHub Actions UI | `dev-on` or `dev-off` workflow dispatch |

---

## Secret handling

On every `dev-on` run, `shift-secrets` is always re-copied in full from the production namespace and then sanitized. This ensures new keys added to prod are never missing in dev.

- `shift-secrets` — always re-copied from `shift-festival`, then sanitized:
  - `RABBITMQ_VHOST` and `RABBIT_VHOST` → overridden to `shift-festival-dev`
  - External service credentials zeroed out: `SENDGRID_API_KEY`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SF_CLIENT_ID`, `SF_CLIENT_SECRET`, `SF_INSTANCE_URL`, `SF_REFRESH_TOKEN`, all `TEAMS_WEBHOOK_*`
  - `NVIDIA_API_KEY` and `BILLING_API_TOKEN` kept from prod — shared with dev for testing (usage is negligible)
  - Internal credentials kept: DB passwords, RabbitMQ user passwords
- `rabbitmq-definitions` — copied from `shift-festival` if not yet present (contains RabbitMQ user definitions)
- `cloudflare-tunnel-secret` — copied from `shift-festival` if not yet present

> **Security:** External service credentials are intentionally disabled in dev. Services that depend on Salesforce, Sendgrid, or Teams webhooks will not function in dev — this is by design.

---

## RabbitMQ vhost

The `shift-festival-dev` vhost is created automatically during Phase 1 of `dev-on` via `rabbitmqctl`. All 10 RabbitMQ users receive full permissions on this vhost:

`chatbot_rabbitmq`, `crm_rabbitmq`, `facturatie_rabbitmq`, `frontend_rabbitmq`, `guest`, `identity_user`, `infra_admin`, `kassa_rabbitmq`, `monitoring_rabbitmq`, `planning_rabbitmq`

**Queue cleanup:** On every `dev-on`, all queues on the default `/` vhost are deleted before services start. This prevents AMQP 406 PRECONDITION_FAILED errors caused by the `rabbitmq-definitions` secret pre-creating queues with different arguments than what the service code expects.

Monitor dev traffic at `dev-rabbitmq.desiderius.me` — switch to the `shift-festival-dev` vhost in the top-right dropdown.

---

## First-time setup (per team)

Three services require a one-time manual setup after their first `dev-on`:

| Service | URL | Action |
|---|---|---|
| Frontend (Drupal) | `dev.desiderius.me` | Run the Drupal installer — use the same DB credentials as configured in `shift-secrets` |
| Facturatie (FossBilling) | `dev-facturatie.desiderius.me` | Run the FossBilling installer — same DB credentials |
| Kassa (Odoo) | `dev-kassa.desiderius.me` | Select or create the database in the Odoo DB manager — same DB credentials |

The installation state for these three services is persisted via PVC and survives `dev-off`/`dev-on` cycles. Setup is only needed once.

> All credentials (DB passwords, etc.) are the same as in production since the secrets are copied from `shift-festival`.

---

## Active services in dev

| Service | Runs in dev | Notes |
|---|---|---|
| RabbitMQ | ✅ | Dev vhost `shift-festival-dev` |
| All databases | ✅ | Test data wiped on restart for most DBs (emptyDir) — except kassa-db (PVC) |
| Frontend (Drupal + proxy) | ✅ | Install state persisted via PVC |
| Facturatie (FossBilling + proxy) | ✅ | Install state persisted via PVC |
| Kassa (Odoo + proxy) | ✅ | DB state persisted via PVC — one-time DB initialization required |
| Identity service | ✅ | |
| CRM + Planning workers | ✅ | Salesforce/O365 credentials zeroed out |
| Kassa integration sidecar | ✅ | |
| Chatbot | ✅ | |
| Cloudflared | ✅ | |
| Mailing service | ❌ | Intentionally disabled (no SMTP in dev) |
| Heartbeat sidecars | ❌ | Disabled — replicas: 0 |
| MCP services | ❌ | Not required for integration testing |
| ELK stack | ❌ | Too resource-heavy for shared VM |
| pgAdmin | ❌ | |
| Monitoring agent | ❌ | |

---

## Manifest differences versus production

- **PVCs** for `frontend-db`, `facturatie-db`, `kassa-db`, `frontend-drupal`, `fossbilling-app` — installation and config state is preserved across pod restarts
- **emptyDir** for `postgredb`, `crm-db`, `planning-db`, RabbitMQ, pgAdmin — test data is wiped on pod restart (intentional)
- `postgredb` and `planning-db` have an initContainer (busybox chown) to handle emptyDir permissions
- Dev subdomains in Ingress (`dev-*.desiderius.me`)
- Namespace: `shift-festival-dev`
- 20 of 47 workloads run at `replicas: 0`
- CPU requests reduced to `50m` for heavy services to free scheduling headroom

> **kassa-db persistence note:** Kassa-db uses a PVC so Odoo's database schema survives restarts. Test data written during dev sessions also persists — teams should be aware of this and purge manually if needed. Monitoring ILM only covers logs, not DB contents.

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
| **Frontend** | Open `dev.desiderius.me` — create users, enroll in sessions, trigger events | Messages visible on RabbitMQ queues. One-time Drupal install on first use. |
| **Kassa** | Open `dev-kassa.desiderius.me` — create orders | One-time Odoo DB initialization on first use. |
| **Facturatie** | Open `dev-facturatie.desiderius.me` — create invoices, test billing flow | One-time FossBilling install on first use. |
| **CRM** | No UI — publish test message to `crm.*` queue via RabbitMQ management UI | Salesforce credentials zeroed out in dev — no real Salesforce sync |
| **Planning** | No UI — publish test message to `planning.*` queue via RabbitMQ management UI | Office 365 credentials zeroed out in dev — no real O365 sync |
| **Identity** | Called internally via HTTP — other services trigger it automatically | No direct test needed |
| **Chatbot** | Open `dev-chatbot.desiderius.me` or via frontend / direct RabbitMQ message | |

### What can and cannot be tested in dev

| Flow | Testable in dev |
|---|---|
| Frontend ↔ RabbitMQ ↔ backend workers | ✅ |
| Facturatie invoice creation | ✅ |
| RabbitMQ message routing between services | ✅ |
| Kassa order flow | ✅ |
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
| RabbitMQ | [dev-rabbitmq.desiderius.me](https://dev-rabbitmq.desiderius.me) |
| Chatbot | [dev-chatbot.desiderius.me](https://dev-chatbot.desiderius.me) |

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
- [x] PVC for kassa-db (`kassa-db-data-pvc-dev`) — Odoo DB survives restarts
- [x] Secret bootstrap in `dev-on` — always re-copy + sanitize `shift-secrets` from prod; copy `rabbitmq-definitions` and `cloudflare-tunnel-secret` if not present
- [x] RabbitMQ vhost `shift-festival-dev` — automated in `dev-on` Phase 1
- [x] RabbitMQ default vhost queue cleanup — automated in `dev-on` Phase 1 to prevent AMQP 406 conflicts
- [x] Cloudflare Tunnel route for `dev-rabbitmq.desiderius.me`
- [x] Kassa image alias (`kassa-odoo`) — separates Odoo image updates from kassa integration image updates in Image Updater

### Pending (other teams)
- [ ] All teams — initialize app on first `dev-on` (Drupal, FossBilling, Odoo DB)

### Monitoring
- [ ] Kibana Data View for `shift-festival-dev`
- [ ] ILM policy: purge dev logs after 1 day

---

## Testing status

- [x] `dev-on` workflow → pods start in phased sequence
- [x] `dev.desiderius.me` → frontend loads
- [x] `dev-facturatie.desiderius.me` → FossBilling loads
- [x] `dev-kassa.desiderius.me` → Odoo loads (DB initialization required on first use)
- [x] `dev-rabbitmq.desiderius.me` → RabbitMQ management UI accessible
- [x] RabbitMQ dev vhost `shift-festival-dev` → created with correct permissions
- [x] Secret sanitization → external credentials zeroed out on dev-on
- [x] Secret sync → full re-copy from prod on every dev-on (no missing keys)
- [x] RabbitMQ queue cleanup → default vhost queues cleared on dev-on
- [x] 4-hour inactivity → auto-shutdown confirmed working
- [ ] End-to-end integration test across all services via RabbitMQ
