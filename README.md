<!-- =================================================================== -->
<!--  ShiftFestival · Team Infra · README                                -->
<!-- =================================================================== -->

<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=0:326CE5,50:1A73E8,100:0A7EA4&height=220&section=header&text=ShiftFestival%20Infra&fontSize=58&fontAlignY=38&fontColor=ffffff&desc=Kubernetes%20Infrastructure%20&%20GitOps%20Control%20Plane&descAlignY=62&descSize=18&animation=fadeIn" alt="ShiftFestival Infra banner" />
</p>

<p align="center">
  <img alt="Kubernetes" src="https://img.shields.io/badge/Kubernetes-326CE5?style=flat-square&logo=kubernetes&logoColor=white">
  <img alt="ArgoCD" src="https://img.shields.io/badge/ArgoCD-EF7B4D?style=flat-square&logo=argo&logoColor=white">
  <img alt="Argo Rollouts" src="https://img.shields.io/badge/Argo_Rollouts-6C63FF?style=flat-square&logo=argo&logoColor=white">
  <img alt="GitHub Actions" src="https://img.shields.io/badge/GitHub%20Actions-2671E5?style=flat-square&logo=githubactions&logoColor=white">
  <img alt="RabbitMQ" src="https://img.shields.io/badge/RabbitMQ-FF6600?style=flat-square&logo=rabbitmq&logoColor=white">
  <img alt="Elastic Stack" src="https://img.shields.io/badge/Elastic_Stack-005571?style=flat-square&logo=elasticsearch&logoColor=white">
</p>

<p align="center">
  <img alt="namespace" src="assets/badges/namespace.svg">
  <img alt="teams" src="assets/badges/teams.svg">
  <img alt="strategy" src="assets/badges/strategy.svg">
  <img alt="rollback" src="assets/badges/rollback.svg">
</p>

---

## Technical Overview

This repository serves as the central control plane for the **ShiftFestival** infrastructure. It manages a multi-tenant Kubernetes cluster designed for high reliability, security, and automated delivery.

### Key Architectural Pillars
- **Declarative Configuration:** Managed entirely through Kustomize with a zero-overlay strategy for maximum predictability.
- **GitOps Delivery:** ArgoCD acts as the single source of truth, ensuring the cluster state matches the repository.
- **Automated Lifecycle:** ArgoCD Image Updater handles automated image promotion from GHCR to Git.
- **Progressive Delivery:** Argo Rollouts provides automated health-based rollbacks and canary deployments.

---

## Operational Procedures

### Local Validation
Before pushing changes, it is recommended to validate the manifests locally:

```bash
# Render the complete Kustomize tree
kubectl kustomize .

# Perform a dry-run apply to check for schema errors
kubectl apply -k . --dry-run=client
```

### Deployment Flow
The deployment follows a GitOps pattern. Manual intervention in the cluster is discouraged.

1. Commit changes to the `main` branch.
2. ArgoCD detects the change and synchronizes the cluster state.
3. For core services, an **Argo Rollout** is initiated.
4. The controller monitors the new pods for a 5-minute health window before finalizing the promotion.

---

## Service Inventory & Connectivity

The infrastructure maps services to specific NodePort ranges and external subdomains.

| Department | Service Name | Internal Port | NodePort | Production URL |
| :--- | :--- | :--- | :--- | :--- |
| **Facturatie** | `facturatie-app-service` | 80 | `30010` | [facturatie.desiderius.me](https://facturatie.desiderius.me) |
| | `facturatie-db-service` | 3306 | — | — |
| **Frontend** | `frontend-drupal-service` | 80 | `30020` | [desiderius.me](https://desiderius.me) |
| | `frontend-db-service` | 3306 | — | — |
| **Kassa** | `kassa-web-service` | 8069 | `30030` | [kassa.desiderius.me](https://kassa.desiderius.me) |
| | `kassa-db-service` | 5432 | — | — |
| **Integratie** | `crm-receiver-service` | 80 | `30040` | [crm.desiderius.me](https://crm.desiderius.me) |
| | `planning-service` | 80 | `30050` | [planning.desiderius.me](https://planning.desiderius.me) |
| | `identity-service` | 8000 | `30070` | [id.desiderius.me](https://id.desiderius.me) |
| **Infra** | `rabbitmq-service` | 5672, 15672 | `30001` | [mq.desiderius.me](https://mq.desiderius.me) |
| | `kibana-service` | 5601 | `30060` | [kibana.desiderius.me](https://kibana.desiderius.me) |
| | `argocd-server` | 80, 443 | — | [argocd.desiderius.me](https://argocd.desiderius.me) |

---

## Project Structure

```text
Infra/
├── kustomization.yaml          # Root entry point & automated image overrides
├── base/                       # Core Kubernetes resource manifests
│   ├── setup/                  # Namespace, Persistent Volumes, Secret templates
│   ├── core/                   # RabbitMQ, PostgreSQL, Cloudflared
│   ├── team-frontend/          # Drupal application stack (Argo Rollout)
│   ├── team-kassa/             # Odoo application stack (Argo Rollout)
│   ├── team-facturatie/        # FossBilling application stack (Argo Rollout)
│   └── monitoring/             # Elasticsearch, Logstash, Kibana
├── argocd/                     # GitOps Controller configuration
│   ├── applications/           # ArgoCD Application definitions
│   └── rollouts/               # Argo Rollouts controller manifests
└── scripts/                    # Operational utilities (Legacy/Deprecated)
```

---

## Resilience and Recovery

### Automated Rollbacks
This cluster implements **Argo Rollouts** for critical workloads. If a pod enters a `CrashLoopBackOff` state or fails readiness checks during a deployment:
- The rollout is automatically aborted.
- Traffic is diverted back to the previous stable version.
- Detailed logs are preserved in the Elasticsearch cluster for forensic analysis.

### Manual Recovery
In the event of a cluster-wide issue, the following steps are available:
- **Git Revert:** Revert the offending commit on the `main` branch to trigger an automated sync to the last known stable state.
- **Rollout Rollback:** Execute `kubectl argo rollouts rollback <name> -n shift-festival` for immediate targeted recovery.

---

## Security Governance
- **Least Privilege:** Containers drop all Linux capabilities and run as non-root users.
- **Secrets Management:** Credentials are bootstrapped via `scripts/create-secret.sh` and injected at runtime.
- **Audit Trails:** All cluster modifications are tracked via Git history and ArgoCD sync logs.
