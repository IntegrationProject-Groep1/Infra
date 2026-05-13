# Shift Festival — Kubernetes Infrastructure Documentation

This document is the team reference for the Shift Festival Kubernetes
infrastructure. It is meant to be read alongside `README.md`.

---

## 1. System overview

Shift Festival is a multi-team application platform deployed on a single
Kubernetes cluster. Three product teams (Frontend, Kassa, Facturatie) each run
an application + database, glued together by a central message broker
(RabbitMQ) and a shared monitoring stack.

### High-level Dataflow

```mermaid
graph TD
    subgraph Users
        User((End User))
    end

    subgraph "External Access (Cloudflare)"
        CF[Cloudflare Tunnel / Ingress]
    end

    subgraph "Team Workloads (shift-festival namespace)"
        Frontend[Frontend - Drupal]
        Kassa[Kassa - Odoo]
        Facturatie[Facturatie - FossBilling]
        
        FDB[(MariaDB)]
        KDB[(PostgreSQL)]
        BDB[(MariaDB)]
        
        Frontend <--> FDB
        Kassa <--> KDB
        Facturatie <--> BDB
    end

    subgraph "Messaging & Logic"
        MQ{RabbitMQ Broker}
        CRM[CRM Receiver]
        Plan[Planning Service]
        Ident[Identity Service]
    end

    subgraph "Monitoring"
        ELK[(Elasticsearch + Kibana)]
        HB[Heartbeat Sidecars]
    end

    User --> CF
    CF --> Frontend
    CF --> Kassa
    CF --> Facturatie

    Frontend -- Async Events --> MQ
    Kassa -- Async Events --> MQ
    Facturatie -- Async Events --> MQ

    MQ <--> CRM
    MQ <--> Plan
    MQ <--> Ident

    HB -- Metrics/Logs --> ELK
    Frontend -.-> HB
    Kassa -.-> HB
    Facturatie -.-> HB
```

---

## 2. Layered architecture

The deployment is managed with **Kustomize** using a flattened root structure for maximum transparency and GitOps stability.

### GitOps Flow

```mermaid
sequenceDiagram
    participant Dev as Developer
    participant Git as GitHub (Main Branch)
    participant GHCR as GitHub Container Registry
    participant Updater as ArgoCD Image Updater
    participant Argo as ArgoCD Controller
    participant Cluster as Kubernetes Cluster

    Dev->>Git: Git Push (Code)
    Git->>GHCR: CI/CD Build & Push (:latest)
    Updater->>GHCR: Poll for new Digest
    Updater->>Git: Commit new Digest to kustomization.yaml
    Argo->>Git: Poll for changes
    Argo->>Cluster: Sync Manifests
    Cluster->>Cluster: Argo Rollout (Canary/Health Check)
    Note over Cluster: Automatic Rollback if Health Fails
```

---

## 3. Conventions

### Namespace
- `shift-festival` — Application workloads.
- `argocd` — GitOps management.
- `argo-rollouts` — Rollout controller.

### Common labels
- `project: shift-festival`
- `managed-by: Team-Infra`
- `app: <service-name>`

---

## 4. Component reference

Refer to `README.md` for the full port-allocation and service list.
