# Dev Environment — Volledige Setup

## Stack

| Component | Details |
|---|---|
| Namespace | `shift-festival-dev` |
| Storage | `emptyDir` — geen persistentie, data weg bij pod restart |
| Logging | Elastic Agent → prod Elasticsearch → Kibana filter op `kubernetes.namespace: shift-festival-dev`, retention 1 dag via ILM policy |
| RabbitMQ | Aparte vhost `shift-festival-dev` |
| Scaling | GitHub Actions `workflow_dispatch` via Teams + auto-shutdown |
| Images | `:dev` tags via ArgoCD Image Updater |

---

## Scaling logica

| Trigger | Actie |
|---|---|
| `/dev on` in Teams | → GitHub Actions `workflow_dispatch` → `syncPolicy.automated` aan → ArgoCD synct → pods up (~30-60s) |
| `/dev off` in Teams | → GitHub Actions `workflow_dispatch` → `syncPolicy.automated` uit → scale to zero → pods down |
| Merge naar main | GitHub Actions trigger → automatisch `/dev off` |
| 4u geen activiteit op dev branch | GitHub Actions cron → automatisch `/dev off` |

**Waarom GitHub Actions ipv custom webhook:**
- Geen aparte webhook service bouwen of hosten
- Alles traceerbaar in Git en Actions logs
- Teams kan `workflow_dispatch` triggeren via Incoming Webhook of Power Automate
- GitHub Actions heeft al toegang tot de cluster via kubeconfig secret

---

## Manifests t.o.v. prod

- `emptyDir` in plaats van PVCs (PVCs aangemaakt maar niet geprovisioneerd)
- Dev subdomains in Ingress (`dev-*.desiderius.me`)
- Namespace: `shift-festival-dev`
- Geen ELK pods (replicas: 0)
- `:dev` images via ArgoCD Image Updater

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

| State | RAM | CPU |
|---|---|---|
| Dev DOWN | ~55% | ~6% |
| Dev UP | ~62% | ~9% |

---

## Taken

### Infra
- [x] Kustomize overlay aanmaken (`overlays/dev/`)
- [x] ArgoCD Application aanmaken (`argocd/applications/dev-app.yaml`)
- [x] Dev subdomains in Ingress patches
- [ ] DNS records aanmaken via Cloudflare (`dev-*.desiderius.me`)
- [ ] GitHub Actions `dev-on.yml` workflow
- [ ] GitHub Actions `dev-off.yml` workflow (incl. merge naar main trigger + 4u cron)

### Monitoring
- [ ] Kibana Data View voor `shift-festival-dev`
- [ ] ILM policy: dev logs na 1 dag wissen
- [ ] RabbitMQ vhost `shift-festival-dev` aanmaken

---

## Testen

- [ ] GitHub Actions `dev-on` → pods up in K8s Dashboard
- [ ] `dev.desiderius.me` → frontend laadt
- [ ] Test user registreren → RabbitMQ dev vhost check
- [ ] Kibana → filter op `shift-festival-dev` → logs zichtbaar
- [ ] 4u inactiviteit → auto-shutdown check
- [ ] Merge naar main → auto-shutdown check
