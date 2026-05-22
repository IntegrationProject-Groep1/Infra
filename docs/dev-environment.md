# Dev Environment — Volledige Setup

## Stack

| Component | Details |
|---|---|
| Namespace | `shift-festival-dev` |
| Storage | Selectief — PVC voor app installs (Drupal, FOSSBilling + hun databases), emptyDir voor test data (overige databases, RabbitMQ) |
| Logging | Prod Elasticsearch → Kibana filter op `kubernetes.namespace: shift-festival-dev` |
| RabbitMQ | Aparte vhost `shift-festival-dev` |
| Scaling | Manueel via GitHub Actions + auto-shutdown na 4u |
| Images | `:dev` tags via ArgoCD Image Updater |
| Beheer | Manueel `kubectl apply -k` — geen ArgoCD auto-sync voor dev |
| Monitoring | ArgoCD UI (`argocd.desiderius.me`) — read-only pod status, Kibana voor logs |

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

- **PVC** voor `frontend-db`, `facturatie-db`, `frontend-drupal`, `fossbilling-app` — installatie en config blijft bewaard
- **emptyDir** voor `postgredb`, `kassa-db`, `crm-db`, `planning-db`, RabbitMQ, pgAdmin — test data weg bij pod restart
- `postgredb` en `planning-db` hebben een initContainer (busybox chown) voor emptyDir permissies
- `rabbitmq-definitions` secret wordt automatisch gekopieerd van `shift-festival` bij `dev-on`
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
- [x] Selectieve PVC/emptyDir storage configuratie
- [x] `rabbitmq-definitions` secret bootstrap in `dev-on` workflow

### Monitoring
- [ ] Kibana Data View voor `shift-festival-dev`
- [ ] ILM policy: dev logs na 1 dag wissen
- [ ] RabbitMQ vhost `shift-festival-dev` aanmaken

---

## Testen

- [x] `dev-on` workflow → pods up gefaseerd
- [x] `dev.desiderius.me` → frontend laadt
- [ ] `dev-kassa.desiderius.me` → kassa laadt
- [ ] RabbitMQ dev vhost check
- [ ] 4u inactiviteit → auto-shutdown check
