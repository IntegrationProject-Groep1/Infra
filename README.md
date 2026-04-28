# ShiftFestival - Team Infra

![Kubernetes](https://img.shields.io/badge/kubernetes-326CE5?style=for-the-badge&logo=kubernetes&logoColor=white)
![Kustomize](https://img.shields.io/badge/kustomize-1A73E8?style=for-the-badge&logo=kubernetes&logoColor=white)
![GitHub Actions](https://img.shields.io/badge/github%20actions-%232671E5.svg?style=for-the-badge&logo=githubactions&logoColor=white)
![RabbitMQ](https://img.shields.io/badge/Rabbitmq-FF6600?style=for-the-badge&logo=rabbitmq&logoColor=white)
![Elasticsearch](https://img.shields.io/badge/Elastic_Stack-005571?style=for-the-badge&logo=elasticsearch&logoColor=white)

Central infrastructure documentation for the ShiftFestival integration project.

This README is the official entry point for Team Infra. The platform now runs on Kubernetes manifests managed with Kustomize.

## Table of Contents

- [Current State](#current-state)
- [Architecture Overview](#architecture-overview)
- [Repository Roles](#repository-roles)
- [Kubernetes Structure](#kubernetes-structure)
- [Deployment and Operations](#deployment-and-operations)
- [NodePort Allocation](#nodeport-allocation)
- [Conventions for Teams](#conventions-for-teams)

## Current State

ShiftFestival has moved from Docker Compose to Kubernetes.

- Main repository: Infra
- Runtime manifests: `setup/`, `core/`, `team-*`, `integrations/`, `monitoring/`
- Supporting automation: `.github/workflows/` and `scripts/`
- Release automation: `keel/` is kept as a separate namespace-based manifest set

## Architecture Overview

The stack is deployed with Kustomize layers into one Kubernetes namespace: shift-festival.

High-level runtime blocks:

- Setup: namespace, storage, configmaps
- Core: RabbitMQ, Postgres, cloudflared, Kubernetes dashboard
- Team services: frontend, kassa, facturatie
- Integrations: CRM, planning, identity-service
- Monitoring: Elasticsearch, Kibana, Logstash, monitoring-agent
- Release automation: Keel

## Kubernetes Structure

Current structure in Infra:

```text
.
├── kustomization.yaml
├── README.md
├── DOCUMENTATION.md
│
├── setup/
│   ├── namespace.yaml
│   ├── storage.yaml
│   ├── configmaps.yaml
│   └── kustomization.yaml
│
├── core/
│   ├── rabbitmq.yaml
│   ├── postgres.yaml
│   ├── cloudflared.yaml
│   ├── kubernetes-dashboard.yaml
│   └── kustomization.yaml
│
├── team-frontend/
│   ├── drupal.yaml
│   ├── mariadb.yaml
│   ├── proxy.yaml
│   └── kustomization.yaml
│
├── team-kassa/
│   ├── odoo.yaml
│   ├── postgres.yaml
│   ├── proxy.yaml
│   ├── integration.yaml
│   └── kustomization.yaml
│
├── team-facturatie/
│   ├── fossbilling.yaml
│   ├── mariadb.yaml
│   ├── proxy.yaml
│   └── kustomization.yaml
│
├── integrations/
│   ├── crm.yaml
│   ├── planning.yaml
│   ├── identity-service.yaml
│   └── kustomization.yaml
│
├── monitoring/
│   ├── elasticsearch.yaml
│   ├── kibana.yaml
│   ├── logstash.yaml
│   ├── logstash-config.yaml
│   ├── monitoring-agent.yaml
│   └── kustomization.yaml
│
└── keel/
    ├── keel.yaml
    └── kustomization.yaml
```

## Deployment and Operations

Run from the Infra repository root.

Before running a local render, create `setup/.env` with dummy or real values. The Kustomize secretGenerator in `setup/kustomization.yaml` requires that file.

```bash
# Validate rendered manifests
kubectl kustomize .

# Validate without applying
kubectl apply -k . --dry-run=client

# Deploy all layers
kubectl apply -k .

# Verify workloads
kubectl get pods -n shift-festival
kubectl get svc -n shift-festival

# Remove all resources from this stack
kubectl delete -k .
```

## NodePort Allocation

Keep existing team ranges:

- Team Infra: 30000-30009
- Team Facturatie: 30010-30019
- Team Frontend: 30020-30029
- Team Kassa: 30030-30039
- Team CRM: 30040-30049
- Team Planning: 30050-30059
- Team Monitoring: 30060-30069
- Identity and reserved range: 30070-30100

Always coordinate new public NodePorts with Team Infra before merging.

## Conventions for Teams

### RabbitMQ naming

Always use team prefixes:

- kassa.orders
- crm.customer.created
- planning.session.updated

Do not use unprefixed names such as orders or customer_queue.

Exception: heartbeat exchange remains shared.

### Service onboarding checklist

When adding a service to Infra:

1. Add manifest(s) in the correct folder.
2. Add or update that folder's `kustomization.yaml`.
3. Ensure service selectors, labels, and namespaces are consistent.
4. Add required secrets/config keys to `setup/.env` and document them.
5. Verify end-to-end with `kubectl kustomize .` and `kubectl apply -k . --dry-run=client`.

### Documentation rule

Any significant infra change must update:

- this README (official overview)
- CLAUDE.md (process, architecture, CI/CD expectations)
- SECURITY.md when the change has security impact

IntegrationProject-Groep1 · ShiftFestival 2026 · Erasmushogeschool Brussel
