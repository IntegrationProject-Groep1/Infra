# Synthesedocument — Team Infra
**IntegrationProject 2025-26 – Groep 1**
*May 2026*

---

## 1. Teamoverzicht

| Naam | Rol |
|---|---|
| Enwin Dang | Team Lead — architectuur, ArgoCD, Kustomize, CI/CD, security |
| Tom Dekoning | Developer — Kubernetes manifests, monitoring stack, rollouts |
| Kainy Zafari | Developer — RabbitMQ, integrations, service configuratie |

---

## 2. Opgeleverde Features

### Infrastructuur & Orchestratie
- **Kubernetes (k3s)** — volledige cluster setup op Azure VM, namespace structuur (`shift-festival`)
- **Kustomize overlay structuur** — basis manifests + overlays voor prod en dev, geen duplicatie
- **Argo Rollouts** — canary deployment strategie voor alle stateless services, automated rollback na 3 crashes
- **ArgoCD** — GitOps controller, auto-sync op elke push naar main, UI beschikbaar via `argocd.desiderius.me`
- **ArgoCD Image Updater** — detecteert nieuwe images in ghcr.io, commit digest naar repo, ArgoCD synct automatisch
- **Horizontal Pod Autoscaling (HPA)** — CPU-gebaseerde autoscaling (70% threshold) voor 7 services
- **Metrics Server** — geïnstalleerd in kube-system, vereist voor HPA op k3s met self-signed kubelet certs

### Netwerk & Toegang
- **ingress-nginx** — centrale ingress controller, alle externe traffic via één punt
- **Cloudflare Tunnel (cloudflared)** — veilige externe toegang zonder open poorten, TLS via Cloudflare
- **ClusterIP services** — alle services intern, geen NodePort exposure meer
- **NetworkPolicies** — targeted allow-rules per service (databases, Elasticsearch, RabbitMQ)

### CI/CD Pipeline
- **CI workflow** — YAML lint, shellcheck, Gitleaks secret scanning, Trivy security scanning
- **Deploy workflow** — kubectl apply -k op VM via SSH, triggered na succesvolle CI
- **PR Integration test** — Kind cluster in GitHub Actions, volledige manifest validatie op elke PR
- **Branch protection** — CI moet slagen voor merge naar main

### Monitoring & Observability
- **ELK Stack** — Elasticsearch, Logstash, Kibana gedeployed in `shift-festival` namespace
- **Elastic Agent** — standalone DaemonSet, node metrics (CPU, RAM, disk) + Kubernetes pod metrics
- **Heartbeat sidecar pattern** — elke service stuurt liveness signalen via RabbitMQ naar Kibana
- **Kibana dashboard** — beschikbaar via `kibana.desiderius.me`

### Messaging
- **RabbitMQ** — centrale message broker, event-driven architectuur voor alle teams
- **Dedicated users per service** — minimale permissions per RabbitMQ gebruiker
- **Vhost structuur** — `/` voor prod, `shift-festival-dev` voor dev (gepland)

### Security
- **Pod Security Standards** — `runAsNonRoot`, `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem`, `capabilities: drop: ALL` op alle containers
- **SHA digest pinning** — geen floating `:latest` tags in productie
- **Secrets management** — alle credentials in `shift-secrets` Kubernetes Secret, nooit in repo
- **Trivy + Gitleaks** — geautomatiseerde security scanning in CI pipeline

### Dev Environment (`shift-festival-dev`)
- **Kustomize overlay** — lightweight dev namespace, `emptyDir` storage, monitoring op 0 replicas
- **ArgoCD Application** — aparte ArgoCD app voor dev namespace
- **GitHub Actions workflows** — `dev-on.yml` en `dev-off.yml` voor opstarten/afsluiten
- **Auto-shutdown** — na 4u inactiviteit op de dev branch
- **Dev subdomains** — `dev.desiderius.me`, `dev-kassa.desiderius.me`, `dev-facturatie.desiderius.me`
- **Cloudflare Tunnel routes** — aangemaakt voor alle dev subdomains

---

## 3. Niet Afgeronde Features

| Feature | Reden |
|---|---|
| Dev environment volledig getest | Vereist `setup/.env.dev` met sandbox credentials van andere teams (Salesforce, Office 365). Dit is een cross-team afhankelijkheid buiten scope van infra. |
| Teams webhook `/dev on` via Power Automate | Power Automate integratie met GitHub Actions workflow dispatch is niet geïmplementeerd. Dev environment wordt momenteel manueel getriggerd via GitHub Actions UI. |
| Kibana alert rules met email notificaties | Vereist `XPACK_ENCRYPTEDSAVEDOBJECTS_ENCRYPTIONKEY` in Kibana deployment. Bewust uitgesteld tot na de demo om onnodige pod restarts te vermijden. |
| Elasticsearch ILM policy voor dev namespace | Monitoring team taak — infra heeft de technische basis gelegd (1 dag retention gepland), implementatie ligt bij monitoring team. |
| RabbitMQ vhost `shift-festival-dev` | Manuele configuratie vereist op de VM. Niet geautomatiseerd omdat RabbitMQ definitions beheerd worden via een secret. |
| FOSSBilling init container volledig non-root | Vereist testen of FOSSBilling correct opstart met `fsGroup` ipv root `chown`. Risico op service outage in prod — bewust geaccepteerd als risico met mitigaties (`allowPrivilegeEscalation: false`). |
| Backup automatisering (Velero) | Vereist Azure Blob Storage — geen Azure subscription rechten buiten de VM. Manuele backup procedure gedocumenteerd als alternatief. |
| Multi-node cluster | Vereist een tweede Azure VM — niet beschikbaar binnen de projectallocatie. |

---

## 4. Links

### Repositories
- **Infra repo:** https://github.com/IntegrationProject-Groep1/Infra

### Documentatie
- **ClickUp:** https://app.clickup.com/90152408976/v/b/6-901521809422-2
- **Kubernetes Infrastructure Overview:** zie `/docs/` in de repo
- **Team Infra Guidelines:** zie `/docs/` in de repo
- **Security Overview:** zie `/docs/` in de repo
- **Backup & Disaster Recovery:** zie `/docs/` in de repo
- **Dev Environment Guide:** zie `/docs/` in de repo

---

## 5. Aanmeldgegevens

> **Let op:** Alle gevoelige credentials staan nooit in de repo. Hieronder staan de toegangspunten — credentials worden apart aangeleverd of zijn beschikbaar via de `shift-secrets` Kubernetes Secret op de VM.

### Azure VM (SSH)
- **Host:** `integrationproject-2526s2-dag01.westeurope.cloudapp.azure.com`
- **Port:** `60022`
- **User:** `ehbstudent`
- **SSH key / password:** _______________

### Kubernetes Dashboard
- **URL:** https://k8s.desiderius.me
- **Token:** _______________

### ArgoCD
- **URL:** https://argocd.desiderius.me
- **Username:** `admin`
- **Password:** _______________

### Kibana
- **URL:** https://kibana.desiderius.me
- **Username:** `elastic`
- **Password:** _______________

### RabbitMQ Management UI
- **URL:** https://rabbitmq.desiderius.me
- **Username:** `infra_admin`
- **Password:** _______________

### pgAdmin
- **URL:** https://pgadmin.desiderius.me
- **Email:** _______________
- **Password:** _______________

### GitHub
- **Organisation:** https://github.com/IntegrationProject-Groep1
- **Access:** alle docenten moeten toegang hebben of repo is public

### ghcr.io (Container Registry)
- **Registry:** `ghcr.io/integrationproject-groep1/`
- **Access:** public images, geen credentials vereist

---

## 6. Timesheets

Zie bijgevoegd exportbestand per teamlid voor de volledige timesheet van het project.
