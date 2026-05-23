# Project Summary — Team Infra
**IntegrationProject 2025-26 – Group 1**
*May 2026*

---

## 1. Team Overview

| Name | Role |
|---|---|
| Enwin Dang | Team Lead — architecture, ArgoCD, Kustomize, CI/CD, security |
| Tom Dekoning | Developer — Kubernetes manifests, monitoring stack, rollouts |
| Kainy Zafari | Developer — RabbitMQ, integrations, service configuration |

---

## 2. Delivered Features

### Infrastructure & Orchestration
- **Kubernetes (k3s)** — full cluster setup on Azure VM, namespace structure (`shift-festival`)
- **Kustomize overlay structure** — base manifests + overlays for prod and dev, no duplication
- **Argo Rollouts** — canary deployment strategy for all stateless services, automated rollback after 3 crashes
- **ArgoCD** — GitOps controller, auto-sync on every push to main, UI available at `argocd.desiderius.me`
- **ArgoCD Image Updater** — detects new images in ghcr.io, commits digest to repo, ArgoCD syncs automatically
- **Horizontal Pod Autoscaling (HPA)** — CPU-based autoscaling (70% threshold) for 7 services
- **Metrics Server** — installed in kube-system, required for HPA on k3s with self-signed kubelet certs

### Networking & Access
- **ingress-nginx** — central ingress controller, all external traffic through a single entry point
- **Cloudflare Tunnel (cloudflared)** — secure external access without open ports, TLS via Cloudflare
- **ClusterIP services** — all services exposed internally only, no NodePort exposure
- **NetworkPolicies** — targeted allow-rules per service (databases, Elasticsearch, RabbitMQ)

### CI/CD Pipeline
- **CI workflow** — YAML lint, shellcheck, Gitleaks secret scanning, Trivy security scanning
- **Deploy workflow** — `kubectl apply -k` on VM via SSH, triggered after successful CI
- **PR integration test** — Kind cluster in GitHub Actions, full manifest validation on every pull request
- **Branch protection** — CI must pass before merging to main

### Monitoring & Observability
- **ELK Stack** — Elasticsearch, Logstash, Kibana deployed in the `shift-festival` namespace
- **Elastic Agent** — standalone DaemonSet collecting node metrics (CPU, RAM, disk) + Kubernetes pod metrics
- **Heartbeat sidecar pattern** — each service sends liveness signals via RabbitMQ to Kibana
- **Kibana dashboard** — available at `kibana.desiderius.me`

### Messaging
- **RabbitMQ** — central message broker, event-driven architecture for all teams
- **Dedicated users per service** — minimal permissions per RabbitMQ user
- **Vhost structure** — `/` for prod, `shift-festival-dev` for dev (planned)

### Security
- **Pod Security Standards** — `runAsNonRoot`, `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem`, `capabilities: drop: ALL` on all containers
- **SHA digest pinning** — no floating `:latest` tags in production
- **Secrets management** — all credentials in `shift-secrets` Kubernetes Secret, never in the repository
- **Trivy + Gitleaks** — automated security scanning in the CI pipeline

### Dev Environment (`shift-festival-dev`)
- **Kustomize overlay** — lightweight dev namespace, `emptyDir` storage, monitoring at 0 replicas
- **ArgoCD Application** — separate ArgoCD app for the dev namespace
- **GitHub Actions workflows** — `dev-on.yml` and `dev-off.yml` for startup and shutdown
- **Auto-shutdown** — after 4 hours of inactivity on the dev branch
- **Dev subdomains** — `dev.desiderius.me`, `dev-kassa.desiderius.me`, `dev-facturatie.desiderius.me`
- **Cloudflare Tunnel routes** — created for all dev subdomains

### Backup & Disaster Recovery
- **Automated daily backups** — all 6 databases (3× PostgreSQL, 2× MariaDB, 1× MySQL) dumped twice daily via GitHub Actions and transferred to a dedicated backup VM
- **Git mirror** — bare mirror of the Infra repository on the backup VM, kept in sync daily
- **Encrypted secrets backup** — `.env` file re-encrypted with GPG and synced to backup VM on every backup run
- **Disaster recovery documentation** — full step-by-step recovery guide in `docs/disaster-recovery.md`
- **Estimated RTO** — 30–60 minutes for a full primary VM loss

---

## 3. Incomplete Features

| Feature | Reason |
|---|---|
| Dev environment fully tested | Requires `setup/.env.dev` with sandbox credentials from other teams (Salesforce, Office 365). Cross-team dependency outside infra scope. |
| Teams webhook `/dev on` via Power Automate | Power Automate integration with GitHub Actions workflow dispatch not implemented. Dev environment is currently triggered manually via GitHub Actions UI. |
| Kibana alert rules with email notifications | Requires `XPACK_ENCRYPTEDSAVEDOBJECTS_ENCRYPTIONKEY` in the Kibana deployment. Deliberately deferred until after the demo to avoid unnecessary pod restarts. |
| Elasticsearch ILM policy for dev namespace | Monitoring team task — infra has laid the technical foundation (1-day retention planned), implementation rests with the monitoring team. |
| RabbitMQ vhost `shift-festival-dev` | Requires manual configuration on the VM. Not automated because RabbitMQ definitions are managed via a Kubernetes secret. |
| FossBilling init container fully non-root | Requires testing whether FossBilling starts correctly with `fsGroup` instead of root-level `chown`. Risk of production outage — deliberately accepted as a risk with mitigations (`allowPrivilegeEscalation: false`). |
| Multi-node cluster | Requires a second Azure VM — not available within the project allocation. |

---

## 4. Links

### Repositories
- **Infra repo:** https://github.com/IntegrationProject-Groep1/Infra

### Documentation
- **ClickUp:** https://app.clickup.com/90152408976/v/b/6-901521809422-2
- **Kubernetes Infrastructure Overview:** see `/docs/` in the repository
- **Team Infra Guidelines:** see `/docs/` in the repository
- **Security Overview:** `SECURITY.md`
- **Backup & Disaster Recovery:** `BACKUP.md` and `docs/disaster-recovery.md`
- **Dev Environment Guide:** `docs/dev-environment.md`

---

## 5. Access Credentials

> **Note:** All sensitive credentials are never stored in the repository. The access points are listed below — credentials are provided separately or are available via the `shift-secrets` Kubernetes Secret on the VM.

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
- **Access:** all lecturers must have access or repository must be public

### ghcr.io (Container Registry)
- **Registry:** `ghcr.io/integrationproject-groep1/`
- **Access:** public images, no credentials required

---

## 6. Timesheets

See the attached export file per team member for the complete project timesheet.
