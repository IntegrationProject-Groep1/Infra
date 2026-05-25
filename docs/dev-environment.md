# Dev Environment — Setup and Operations Guide

**Last updated:** 2026-05-25

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
| Management | ArgoCD reconciliation triggered by `dev-on` workflow — no automated sync; Image Updater commits to Git trigger `dev-on` via GitHub Actions |
| Monitoring | ArgoCD UI (`argocd.desiderius.me`) for read-only pod status, Kibana for logs |

---

## ArgoCD sync behaviour for dev

The dev ArgoCD Application has **no `automated:` sync policy** — ArgoCD does not watch Git and sync on its own. Reconciliation is triggered explicitly:
- When `dev-on` runs, it applies `dev-app.yaml` and removes the `skip-reconcile` annotation → ArgoCD reconciles once against the current state of `overlays/dev/kustomization.yaml` (which already contains the latest image digest committed by Image Updater)
- When dev is already running and Image Updater commits a new image, `dev-on` triggers via GitHub Actions and applies the updated overlay with `kubectl apply -k` directly — ArgoCD sees it on the next reconciliation

`prune: false` and `selfHeal: false` are set as safeguards:
- `prune: false` — ArgoCD will not delete cluster resources that are removed from Git
- `selfHeal: false` — ArgoCD will not override manual replica scaling done by `dev-on`/`dev-off`

**When dev is OFF**, the ArgoCD Application is annotated with `argocd.argoproj.io/skip-reconcile: "true"`. This tells the ArgoCD controller to skip the reconciliation loop for that Application entirely — no sync attempts, no OutOfSync evaluation, no CPU overhead. The Application object stays alive so ArgoCD Image Updater can still read its annotations and watch for new `:latest-dev` image digests. When `dev-on` runs, it removes the annotation and reconciliation resumes.

> **Why not delete the Application when dev is off?** Deleting it stops reconciliation CPU (good) but also removes the Image Updater annotations, so Image Updater no longer knows which images to watch. Team pushes to the dev branch would stop triggering automatic dev-on. The skip-reconcile annotation gives the same CPU benefit without breaking Image Updater.

> **Note:** Scaling pods to 0 via `dev-off` does **not** cause OutOfSync — replica counts are excluded via `ignoreDifferences`. OutOfSync only occurs when Image Updater commits a new image digest to Git and ArgoCD has not yet applied it.

### ignoreDifferences

The dev ArgoCD Application ignores the following fields to prevent false sync conflicts:

| Resource | Field | Reason |
|---|---|---|
| `Secret/shift-secrets` | `/data` | Managed via `kubectl patch` outside ArgoCD |
| All PVCs | `/spec/storageClassName`, `/spec/volumeName` | Set by cluster provisioner — immutable after creation |
| All Rollouts | `/spec/replicas` | Owned by HPA — ArgoCD must not reset this |
| All HPAs | `/spec/minReplicas` | Live HPA state drifts constantly — prevents permanent OutOfSync |
| `StatefulSet/elasticsearch` | `/spec/replicas` | Patched to 0 by dev overlay — ArgoCD must not fight this |

### Application CRD management

The dev ArgoCD Application (`argocd/applications/dev/dev-app.yaml`) is **not** managed by the prod ArgoCD app. It is managed entirely by the GitHub Actions workflows:
- `dev-on` SCPs `dev-app.yaml` to the VM and runs `kubectl apply -f dev-app.yaml` after Phase 2 completes. The `skip-reconcile` annotation is then removed so ArgoCD starts reconciling.
- `dev-off` adds `argocd.argoproj.io/skip-reconcile: "true"` to pause reconciliation.

When making changes to `dev-app.yaml`, the updated file is SCPd and applied automatically on the next `dev-on` run.

### NodePort conflicts with prod

5 services use NodePorts that are already occupied by prod on the same cluster node:
`crm-mcp-service`, `kassa-mcp-service`, `frontend-mcp-service`, `facturatie-mcp-service`, `frontend-phpmyadmin-service`

The dev overlay patches these to `ClusterIP` to avoid the conflict. However, `kubectl apply` cannot change a Service type in-place — `dev-on` explicitly deletes these 5 services before applying the overlay so they are recreated as ClusterIP. Without this deletion step, `kubectl apply -k overlays/dev` returns a SyncFailed error for these services and ArgoCD enters a permanent OutOfSync loop.

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
  - `NVIDIA_API_KEY`, `BILLING_API_TOKEN`, and `BILLING_WEB_URL` kept from prod — shared with dev for testing (usage is negligible)
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
| MCP services | ✅ | Started in Phase 2 — ClusterIP only (no NodePort in dev) |
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
| Dev DOWN | ~55% | ~40–50% |
| Dev UP (startup) | ~65% | ~75–85% (temporary peak) |
| Dev UP (stable) | ~65% | ~80–90% |

> **Single-node VM constraint:** The VM has 4 CPU cores (EPYC). At 80–90% utilisation the node is functional but saturated — do not leave dev running without active use. The 4-hour auto-off enforces this. The peak during startup is temporary; ArgoCD registers the Application after Phase 2 completes so it sees resources already in correct state and does minimal reconciliation work.

---

## Task status

### Infrastructure
- [x] Kustomize overlay created (`overlays/dev/`)
- [x] ArgoCD Application created (`argocd/applications/dev-app.yaml`)
- [x] Dev subdomains added to Ingress patches
- [x] DNS records created via Cloudflare (`dev-*.desiderius.me`)
- [x] GitHub Actions `dev-on.yml` workflow (phased startup)
- [x] GitHub Actions `dev-off.yml` workflow (4-hour inactivity cron)
- [x] `dev-off` deletes `dev-last-active` ConfigMap — prevents `dev-on` from skipping phased startup after shutdown
- [x] Selective PVC / emptyDir storage configuration
- [x] PVC for kassa-db (`kassa-db-data-pvc-dev`) — Odoo DB survives restarts
- [x] Secret bootstrap in `dev-on` — always re-copy + sanitize `shift-secrets` from prod; copy `rabbitmq-definitions` and `cloudflare-tunnel-secret` if not present
- [x] RabbitMQ vhost `shift-festival-dev` — automated in `dev-on` Phase 1
- [x] RabbitMQ default vhost queue cleanup — automated in `dev-on` Phase 1 to prevent AMQP 406 conflicts
- [x] Cloudflare Tunnel route for `dev-rabbitmq.desiderius.me`
- [x] Kassa image alias (`kassa-odoo`) — separates Odoo image updates from kassa integration image updates in Image Updater
- [x] Chatbot RabbitMQ vhost override — JSON patch at env index 2 (`RABBITMQ_VHOST: shift-festival-dev`). Index-based because Kustomize strategic merge does not work for Argo Rollout CRDs — if env var order changes in the base manifest, this index must be updated accordingly.
- [x] Kassa RabbitMQ vhost override — JSON patch at env index 13 (`RABBIT_VHOST: shift-festival-dev`). Same caveat as above.
- [x] ArgoCD `ignoreDifferences` for PVC storageClassName/volumeName, shift-secrets data, Rollout replicas, HPA minReplicas, StatefulSet elasticsearch replicas
- [x] `RespectIgnoreDifferences: true` in syncOptions — prevents ArgoCD from patching ignored fields during sync
- [x] `prune: false` + `selfHeal: false` — ArgoCD only syncs image updates, never deletes or overrides manual scaling
- [x] `dev-on` deletes 5 NodePort-conflicting services before overlay apply — prevents SyncFailed and OutOfSync loop
- [x] `dev-off` uses `skip-reconcile` annotation instead of deleting Application — ArgoCD stops reconciling but Image Updater keeps working
- [x] `dev-on` removes `skip-reconcile` annotation after Phase 2 + ArgoCD registration
- [x] `dev-on` registers ArgoCD Application after Phase 2 (not before) — resources already in correct state → no reconciliation burst on startup
- [x] Image Updater `allow-tags` corrected to `latest-dev` — was incorrectly set to `dev`, causing all dev images to be skipped

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
- [x] Chatbot RabbitMQ 403 error fixed — vhost override in overlay
- [x] ArgoCD PVC sync errors fixed — `ignoreDifferences` + `RespectIgnoreDifferences`
- [x] NodePort SyncFailed errors fixed — 5 conflicting services deleted before overlay apply in `dev-on`
- [x] HPA and Elasticsearch OutOfSync fixed — added to `ignoreDifferences`
- [x] ArgoCD reconciliation CPU spike fixed — skip-reconcile annotation when dev is off, Application registered after Phase 2
- [x] Image Updater `allow-tags` fixed — was `^dev$`, now `^latest-dev$`
- [ ] End-to-end Image Updater test — team pushes to dev → `dev-on` auto-triggers
- [ ] End-to-end integration test across all services via RabbitMQ
