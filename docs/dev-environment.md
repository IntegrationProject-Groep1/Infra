# Dev Environment — Volledige Setup

## Stack

| Component | Details |
|---|---|
| Namespace | `shift-festival-dev` |
| Storage | `emptyDir` — geen persistentie, data weg bij pod restart |
| Logging | Elastic Agent → prod Elasticsearch → Kibana filter op `kubernetes.namespace: shift-festival-dev`, retention 1 dag via ILM policy |
| RabbitMQ | Aparte vhost `shift-festival-dev` |
| Scaling | Automatisch via ArgoCD Image Updater + GitHub Actions auto-shutdown |
| Images | `:dev` tags via ArgoCD Image Updater |

---

## Scaling logica

| Trigger | Actie |
|---|---|
| Push naar `dev` branch (eender welk team) | `:dev` image gebouwd → Image Updater update `overlays/dev/kustomization.yaml` op `dev` branch → `dev-on` triggert automatisch → ArgoCD synct → pods up |
| 4u geen activiteit op `dev` branch | GitHub Actions cron → automatisch `dev-off` → ArgoCD suspended → pods down |
| Manueel via GitHub Actions UI | `dev-on` of `dev-off` workflow dispatch |

**Waarom volledig automatisch:**
- Geen manuele actie nodig van andere teams
- Alles traceerbaar in GitHub Actions logs
- 4u inactiviteit timer is gedeeld — zolang één team pusht blijft de env actief
- Data is sowieso weg bij elke restart (`emptyDir`)

---

## Manifests t.o.v. prod

- `emptyDir` in plaats van PVCs (PVCs aangemaakt maar niet geprovisioneerd via `dev-no-provision` storageClass)
- Dev subdomains in Ingress (`dev-*.desiderius.me`)
- Namespace: `shift-festival-dev`
- Geen ELK pods (replicas: 0)
- `:dev` images via ArgoCD Image Updater
- ArgoCD leest van `dev` branch, Image Updater schrijft naar `dev` branch

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
- [x] DNS records aanmaken via Cloudflare (`dev-*.desiderius.me`)
- [x] GitHub Actions `dev-on.yml` workflow
- [x] GitHub Actions `dev-off.yml` workflow (4u inactiviteit cron)

### Monitoring
- [ ] Kibana Data View voor `shift-festival-dev`
- [ ] ILM policy: dev logs na 1 dag wissen
- [ ] RabbitMQ vhost `shift-festival-dev` aanmaken

---

## Testen

- [ ] Push naar `dev` branch → Image Updater update → `dev-on` triggert → pods up in K8s Dashboard
- [ ] `dev.desiderius.me` → frontend laadt
- [ ] Test user registreren → RabbitMQ dev vhost check
- [ ] Kibana → filter op `shift-festival-dev` → logs zichtbaar
- [ ] 4u inactiviteit → auto-shutdown check
