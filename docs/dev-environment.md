# Dev Environment — Volledige Setup

## Stack

| Component | Details |
|---|---|
| Namespace | `shift-festival-dev` |
| Storage | `emptyDir` — geen persistentie, data weg bij pod restart |
| Logging | Elastic Agent → prod Elasticsearch → Kibana filter op `kubernetes.namespace: shift-festival-dev`, retention 1 dag via ILM policy |
| RabbitMQ | Aparte vhost `shift-festival-dev` |
| Scaling | Manueel via GitHub Actions + auto-shutdown na 4u |
| Images | `:dev` tags via ArgoCD Image Updater |
| Beheer | Manueel `kubectl apply -k` — geen ArgoCD auto-sync voor dev |

---

## Waarom geen ArgoCD auto-sync

De VM draait prod en dev op dezelfde node. ArgoCD auto-sync start alle pods tegelijk op, wat CPU pieken veroorzaakt die prod beïnvloeden. Daarom wordt dev manueel beheerd via GitHub Actions workflows met gefaseerde startup.

---

## Scaling logica

| Trigger | Actie |
|---|---|
| Push naar `dev` branch (eender welk team) | `:dev` image gebouwd → Image Updater update `overlays/dev/kustomization.yaml` op `main` → `dev-on` triggert automatisch → gefaseerde startup |
| 4u geen activiteit op `overlays/dev/kustomization.yaml` | GitHub Actions cron → automatisch `dev-off` |
| Manueel via GitHub Actions UI | `dev-on` of `dev-off` workflow dispatch |

---

## Actieve services in dev

| Service | Draait in dev |
|---|---|
| RabbitMQ | ✅ |
| Databases (alle) | ✅ |
| Frontend (Drupal + proxy) | ✅ |
| Kassa (Odoo + proxy) | ✅ |
| Facturatie (FOSSBilling + proxy) | ✅ |
| Identity service | ✅ |
| CRM + Planning workers | ✅ |
| Kassa integration | ✅ |
| Chatbot | ✅ |
| Cloudflared | ✅ |
| Heartbeats | ❌ (Gedeactiveerd — replicas: 0) |
| MCP services | ❌ (niet nodig voor integration testing) |
| ELK stack | ❌ (te zwaar) |
| pgAdmin | ❌ |
| Monitoring agent | ❌ |

---

## Manifests t.o.v. prod

- `emptyDir` in plaats van PVCs (PVCs aangemaakt maar niet geprovisioneerd via `dev-no-provision` storageClass)
- `fsGroup: 999` voor PostgreSQL databases
- Dev subdomains in Ingress (`dev-*.desiderius.me`)
- Namespace: `shift-festival-dev`
- 20 van de 47 workloads op `replicas: 0`

---

## Subdomains

| Service | Subdomain |
|---|---|
| Frontend | `dev.desiderius.me` |
| Kassa | `dev-kassa.desiderius.me` |
| Facturatie | `dev-facturatie.desiderius.me` |
| Planning | `dev-planning.desiderius.me` |
| RabbitMQ | `dev-rabbitmq.desiderius.me` |

---

## Resource impact

| State | RAM | CPU (stabiel) |
|---|---|---|
| Dev DOWN | ~55% | ~44% |
| Dev UP | ~65% | ~50-55% |

---

## Taken

### Infra
- [x] Kustomize overlay aanmaken (`overlays/dev/`)
- [x] ArgoCD Application aanmaken (`argocd/applications/dev-app.yaml`)
- [x] Dev subdomains in Ingress patches
- [x] DNS records aanmaken via Cloudflare (`dev-*.desiderius.me`)
- [x] GitHub Actions `dev-on.yml` workflow (gefaseerde startup)
- [x] GitHub Actions `dev-off.yml` workflow (4u inactiviteit cron)
- [x] fsGroup patches voor PostgreSQL databases

### Monitoring
- [ ] Kibana Data View voor `shift-festival-dev`
- [ ] ILM policy: dev logs na 1 dag wissen
- [ ] RabbitMQ vhost `shift-festival-dev` aanmaken

---

## Testen

- [ ] `dev-on` workflow → pods up gefaseerd
- [ ] `dev.desiderius.me` → frontend laadt
- [ ] `dev-kassa.desiderius.me` → kassa laadt
- [ ] RabbitMQ dev vhost check
- [ ] 4u inactiviteit → auto-shutdown check
