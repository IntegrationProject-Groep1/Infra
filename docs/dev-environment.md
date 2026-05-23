# Dev Environment — Setup and Operations Guide

**Last updated:** 2026-05-22

---

## Stack overview

| Component | Details |
|---|---|
| Namespace | `shift-festival-dev` |
| Storage | Selective — PVCs for application installs (Drupal, FossBilling and their databases), `emptyDir` for ephemeral test data (remaining databases, RabbitMQ) |
| Logging | Shared production Elasticsearch → filter in Kibana on `kubernetes.namespace: shift-festival-dev` |
| RabbitMQ | Separate vhost `shift-festival-dev` |
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
| 4 hours of inactivity on `overlays/dev/kustomization.yaml` | GitHub Actions cron → automatic `dev-off` |
| Manual trigger via GitHub Actions UI | `dev-on` or `dev-off` workflow dispatch |

---

## Active services in dev

| Service | Runs in dev |
|---|---|
| RabbitMQ | ✅ |
| All databases | ✅ |
| Frontend (Drupal + proxy) | ✅ |
| Kassa (Odoo + proxy) | ✅ |
| Facturatie (FossBilling + proxy) | ✅ |
| Identity service | ✅ |
| CRM + Planning workers | ✅ |
| Kassa integration | ✅ |
| Chatbot | ✅ |
| Cloudflared | ✅ |
| Heartbeat sidecars | ❌ (disabled — replicas: 0) |
| MCP services | ❌ (not required for integration testing) |
| ELK stack | ❌ (too resource-heavy for shared VM) |
| pgAdmin | ❌ |
| Monitoring agent | ❌ |

---

## Manifest differences versus production

- **PVCs** for `frontend-db`, `facturatie-db`, `frontend-drupal`, `fossbilling-app` — installation and config state is preserved across pod restarts
- **emptyDir** for `postgredb`, `kassa-db`, `crm-db`, `planning-db`, RabbitMQ, pgAdmin — test data is wiped on pod restart (intentional)
- `postgredb` and `planning-db` have an initContainer (busybox chown) to handle emptyDir permissions
- `rabbitmq-definitions` secret is automatically copied from `shift-festival` during `dev-on`
- Dev subdomains in Ingress (`dev-*.desiderius.me`)
- Namespace: `shift-festival-dev`
- 20 of 47 workloads run at `replicas: 0`

---

## Dev subdomains

| Service | URL |
|---|---|
| Frontend | `dev.desiderius.me` |
| Kassa | `dev-kassa.desiderius.me` |
| Facturatie | `dev-facturatie.desiderius.me` |
| Planning | `dev-planning.desiderius.me` |
| RabbitMQ | `dev-rabbitmq.desiderius.me` |

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
- [x] `rabbitmq-definitions` secret bootstrap in `dev-on` workflow

### Monitoring
- [ ] Kibana Data View for `shift-festival-dev`
- [ ] ILM policy: purge dev logs after 1 day
- [ ] RabbitMQ vhost `shift-festival-dev` provisioning

---

## Testing status

- [x] `dev-on` workflow → pods start in phased sequence
- [x] `dev.desiderius.me` → frontend loads successfully
- [ ] `dev-kassa.desiderius.me` → Kassa loads
- [ ] RabbitMQ dev vhost verification
- [ ] 4-hour inactivity → auto-shutdown verification
