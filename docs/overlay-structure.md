# Kustomize Overlay Structure — ShiftFestival Infra

**Author:** Tom DeKoning (s) — Infra Team  
**Branch:** `refactor/kustomize-overlay-structure`  
**Last updated:** 2026-05-04  
**Reviewed by:** *(pending TL review)*

---

## Table of Contents

1. [What was changed in this PR](#1-what-was-changed-in-this-pr)
2. [Why this refactor was done](#2-why-this-refactor-was-done)
3. [What Kustomize is and how it works](#3-what-kustomize-is-and-how-it-works)
4. [Repository structure](#4-repository-structure)
5. [How the base layer works](#5-how-the-base-layer-works)
6. [How the overlays work](#6-how-the-overlays-work)
7. [How namespace override works](#7-how-namespace-override-works)
8. [How secrets work](#8-how-secrets-work)
9. [Keel (image updater) placement](#9-keel-image-updater-placement)
10. [Render and apply commands](#10-render-and-apply-commands)
11. [PR integration test pipeline](#11-pr-integration-test-pipeline)
12. [What Kainy (CI/CD) must update](#12-what-kainy-cicd-must-update)
13. [Image naming convention for prod/dev](#13-image-naming-convention-for-proddev)
14. [Adding a new service](#14-adding-a-new-service)
15. [Troubleshooting](#15-troubleshooting)

---

## 1. What was changed in this PR

This section documents every file that was created, modified, moved, or deleted and explains exactly why each change was made.

### Files moved (no content changes)

All existing manifests were moved from the repo root into a new `base/` directory using `git mv`. Git history is fully preserved — every file can still be traced back through `git log`.

| Old path | New path |
|----------|----------|
| `setup/configmaps.yaml` | `base/setup/configmaps.yaml` |
| `setup/storage.yaml` | `base/setup/storage.yaml` |
| `core/cloudflared.yaml` | `base/core/cloudflared.yaml` |
| `core/kubernetes-dashboard.yaml` | `base/core/kubernetes-dashboard.yaml` |
| `core/postgres.yaml` | `base/core/postgres.yaml` |
| `core/rabbitmq.yaml` | `base/core/rabbitmq.yaml` |
| `team-frontend/drupal.yaml` | `base/team-frontend/drupal.yaml` |
| `team-frontend/mariadb.yaml` | `base/team-frontend/mariadb.yaml` |
| `team-frontend/proxy.yaml` | `base/team-frontend/proxy.yaml` |
| `team-kassa/odoo.yaml` | `base/team-kassa/odoo.yaml` |
| `team-kassa/postgres.yaml` | `base/team-kassa/postgres.yaml` |
| `team-kassa/integration.yaml` | `base/team-kassa/integration.yaml` |
| `team-kassa/proxy.yaml` | `base/team-kassa/proxy.yaml` |
| `team-facturatie/fossbilling.yaml` | `base/team-facturatie/fossbilling.yaml` |
| `team-facturatie/mariadb.yaml` | `base/team-facturatie/mariadb.yaml` |
| `team-facturatie/mariadb-config.yaml` | `base/team-facturatie/mariadb-config.yaml` |
| `team-facturatie/proxy.yaml` | `base/team-facturatie/proxy.yaml` |
| `integrations/crm.yaml` | `base/integrations/crm.yaml` |
| `integrations/identity-service.yaml` | `base/integrations/identity-service.yaml` |
| `integrations/planning.yaml` | `base/integrations/planning.yaml` |
| `monitoring/elasticsearch.yaml` | `base/monitoring/elasticsearch.yaml` |
| `monitoring/kibana.yaml` | `base/monitoring/kibana.yaml` |
| `monitoring/logstash.yaml` | `base/monitoring/logstash.yaml` |
| `monitoring/logstash-config.yaml` | `base/monitoring/logstash-config.yaml` |
| `monitoring/logstash-settings.yaml` | `base/monitoring/logstash-settings.yaml` |
| `monitoring/monitoring-agent.yaml` | `base/monitoring/monitoring-agent.yaml` |

### Files modified

#### `base/setup/kustomization.yaml`

**Before:**
```yaml
resources:
  - namespace.yaml
  - storage.yaml
  - configmaps.yaml

generatorOptions:
    disableNameSuffixHash: true

secretGenerator:
  - name: shift-secrets
    envs:
      - .env
```

**After:**
```yaml
resources:
  - storage.yaml
  - configmaps.yaml
```

**Why:** The `namespace.yaml` was removed from the base because each overlay now creates its own namespace object (see section 7). The `secretGenerator` was removed because secrets are environment-specific — prod and dev use different `.env` files. Both are now declared in the overlays instead.

---

#### `base/setup/namespace.yaml` → deleted

This file (`kind: Namespace, name: shift-festival`) was deleted from the base because it was hardcoded to the prod namespace name. Each overlay now has its own `namespace.yaml`. See section 7 for the full explanation.

---

#### `keel/keel.yaml`

**Before:**
```yaml
- name: NAMESPACE
  value: ""
```

**After:**
```yaml
- name: NAMESPACE
  value: "shift-festival"
```

**Why:** Keel was previously watching all namespaces (`""` means no filter). With dev now running on the same cluster in its own namespace, Keel would have auto-updated dev images too. We restrict it to `shift-festival` so only prod images are auto-updated by Keel.

---

#### `kustomization.yaml` (root)

**Before:**
```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: shift-festival

labels:
  - pairs:
      project: shift-festival
      managed-by: Team-Infra
    includeSelectors: false

resources:
  - setup/
  - core/
  - team-frontend/
  - team-kassa/
  - team-facturatie/
  - integrations/
  - monitoring/
```

**After:**
```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

# Temporary passthrough to prod overlay + keel.
# Kainy will replace the CI/CD to call overlays directly.
resources:
  - overlays/prod
  - keel
```

**Why:** The old root file is no longer the entry point. Manifests now live in `base/` and overlays handle environment-specific configuration. The root file is kept as a temporary passthrough so the existing deploy workflow (`kubectl apply -k .`) keeps working until Kainy updates the CI/CD pipeline. After that, this file can be removed entirely.

---

#### `.gitignore`

**Before:** Contained `setup/.env` and `setup/.env*` as explicit entries (now stale paths).  
**After:** Replaced with `overlays/prod/.env`, `overlays/dev/.env`, and `base/setup/.env*` to match the new file locations. The catch-all `**/.env` rule was already present and covers both paths, but explicit entries make the intent clear.

---

#### `CLAUDE.md`

Updated to reflect the new folder structure, new render/apply commands (`kubectl kustomize overlays/prod` instead of `kubectl kustomize .`), the two namespaces, and the new rule that Keel is prod-only.

### Files created

#### `base/kustomization.yaml`

The entry point for the shared manifest layer. Lists all sub-directories and sets the shared `project: shift-festival` and `managed-by: Team-Infra` labels on every resource. Does **not** set a namespace — that is the overlay's responsibility.

---

#### `overlays/prod/kustomization.yaml`

Declares the production overlay. Sets `namespace: shift-festival`, includes the base layer, includes `namespace.yaml`, and generates the `shift-secrets` Secret from `overlays/prod/.env`.

---

#### `overlays/prod/namespace.yaml`

Creates the `shift-festival` Kubernetes Namespace object. Kept in the overlay rather than the base because Namespace objects are cluster-scoped and not affected by the namespace transformer (see section 7).

---

#### `overlays/prod/.env.example`

A committed template listing every environment variable required for a prod deployment, with empty values. Team members copy this to `overlays/prod/.env` and fill in the real values. The actual `.env` file is gitignored and must never be committed.

---

#### `overlays/dev/kustomization.yaml`

Declares the development overlay. Identical structure to prod but sets `namespace: shift-festival-dev`. Does not include `keel/` — dev is not auto-updated.

---

#### `overlays/dev/namespace.yaml`

Creates the `shift-festival-dev` Kubernetes Namespace object.

---

#### `overlays/dev/.env.example`

Same structure as the prod example. Dev values will typically use weaker credentials and point to dev-specific endpoints.

---

#### `.github/workflows/pr-integration.yml`

A new GitHub Actions workflow that validates every pull request by deploying to a temporary Kubernetes cluster. See section 11 for the full explanation.

---

#### `docs/overlay-structure.md`

This document.

---

## 2. Why this refactor was done

### The problem before this change

Before this refactor, the repo had a single flat layout:

```
kustomization.yaml      ← hardcoded namespace: shift-festival
setup/
core/
team-frontend/
...
```

There was only one environment: production. To test a change safely, a developer had two bad options:

- **Push directly to prod** and hope it works — risky, untested changes go straight to the live cluster.
- **Duplicate all manifests** into a separate dev copy — causes the environments to drift apart over time, and every change has to be made twice.

### The solution

We introduced a **base + overlays** pattern using Kustomize. This is the industry-standard approach for managing multiple Kubernetes environments from a single source of truth.

The same set of manifests (`base/`) is now shared between production and development. The only differences between environments — namespace name and secrets — are declared in small overlay files. There is zero duplication.

---

## 3. What Kustomize is and how it works

Kustomize is a tool built into `kubectl` that lets you customise Kubernetes YAML files without duplicating them. It uses a `kustomization.yaml` file in each directory to declare:

- Which YAML files to include (`resources`)
- Transformations to apply (`namespace`, `namePrefix`, `labels`, etc.)
- Patches that override specific fields in specific resources
- Generators that create new resources (like Secrets from `.env` files)

When you run `kubectl kustomize <directory>`, Kustomize:
1. Reads the `kustomization.yaml` in that directory
2. Loads all referenced resource files
3. Applies all declared transformations and patches
4. Outputs a single merged YAML stream

When you run `kubectl apply -k <directory>`, it does the same and also sends the result to the Kubernetes API server.

**Key principle: Kustomize never modifies your source files.** It only transforms them in memory during rendering. Your YAML files on disk stay clean.

---

## 4. Repository structure

```
.
├── base/                         ← The shared layer. Contains every manifest.
│   ├── kustomization.yaml        ← Lists all sub-directories; sets shared labels.
│   ├── setup/
│   │   ├── kustomization.yaml    ← References storage.yaml and configmaps.yaml only.
│   │   ├── storage.yaml          ← All PersistentVolumeClaims (one per service).
│   │   └── configmaps.yaml       ← RabbitMQ config, Nginx configs, Drupal settings.
│   ├── core/
│   │   ├── cloudflared.yaml      ← Cloudflare tunnel (external access via desiderius.me).
│   │   ├── kubernetes-dashboard.yaml
│   │   ├── postgres.yaml         ← Shared PostgreSQL (identity service + pgAdmin).
│   │   └── rabbitmq.yaml         ← RabbitMQ message broker (central async bus).
│   ├── team-frontend/
│   │   ├── drupal.yaml           ← Drupal CMS application.
│   │   ├── mariadb.yaml          ← Drupal's own MariaDB database.
│   │   └── proxy.yaml            ← Nginx reverse proxy in front of Drupal.
│   ├── team-kassa/
│   │   ├── odoo.yaml             ← Odoo ERP (point-of-sale system).
│   │   ├── postgres.yaml         ← Kassa's own PostgreSQL database.
│   │   ├── integration.yaml      ← Integration sidecar (RabbitMQ bridge).
│   │   └── proxy.yaml            ← Nginx reverse proxy in front of Odoo.
│   ├── team-facturatie/
│   │   ├── fossbilling.yaml      ← FossBilling invoicing application.
│   │   ├── mariadb.yaml          ← FossBilling's MariaDB database.
│   │   ├── mariadb-config.yaml   ← MariaDB runtime configuration.
│   │   └── proxy.yaml            ← Nginx reverse proxy.
│   ├── integrations/
│   │   ├── crm.yaml              ← Salesforce CRM receiver service.
│   │   ├── identity-service.yaml ← UUID identity service.
│   │   └── planning.yaml         ← Office 365 planning integration.
│   └── monitoring/
│       ├── elasticsearch.yaml    ← Elasticsearch (central log storage).
│       ├── kibana.yaml           ← Kibana (log dashboard, accessible via tunnel).
│       ├── logstash.yaml         ← Logstash (collects and ships logs to ES).
│       ├── logstash-config.yaml  ← Logstash pipeline configuration.
│       ├── logstash-settings.yaml
│       └── monitoring-agent.yaml ← Heartbeat agent (service liveness monitoring).
│
├── overlays/
│   ├── prod/
│   │   ├── kustomization.yaml    ← Sets namespace: shift-festival; generates prod secret.
│   │   ├── namespace.yaml        ← Creates the shift-festival Namespace object.
│   │   ├── .env                  ← GITIGNORED. Real prod secrets. Must exist on VM.
│   │   └── .env.example          ← Template. Safe to commit. Copy to .env, fill in values.
│   └── dev/
│       ├── kustomization.yaml    ← Sets namespace: shift-festival-dev; generates dev secret.
│       ├── namespace.yaml        ← Creates the shift-festival-dev Namespace object.
│       ├── .env                  ← GITIGNORED. Dev secrets. Must exist on VM.
│       └── .env.example          ← Template. Copy to .env, fill in dev values.
│
├── keel/
│   ├── keel.yaml                 ← Keel image updater. Now restricted to shift-festival.
│   └── kustomization.yaml
│
├── kustomization.yaml            ← Temporary root passthrough (overlays/prod + keel).
│                                   Will be removed after Kainy updates the CI/CD.
│
├── .github/
│   └── workflows/
│       ├── ci.yml                ← Existing: lint + security scan. Kainy updates paths.
│       ├── deploy.yml            ← Existing: deploy to Azure VM. Kainy updates commands.
│       └── pr-integration.yml    ← NEW: kind cluster test on every pull request.
│
├── scripts/                      ← Rollback and Teams notification scripts.
├── docs/
│   └── overlay-structure.md      ← This document.
├── CLAUDE.md                     ← AI assistant guidance (updated for new structure).
├── SECURITY.md                   ← Security audit log.
└── README.md                     ← Public project overview.
```

---

## 5. How the base layer works

`base/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

labels:
  - pairs:
      project: shift-festival
      managed-by: Team-Infra
    includeSelectors: false

resources:
  - setup/
  - core/
  - team-frontend/
  - team-kassa/
  - team-facturatie/
  - integrations/
  - monitoring/
```

There is **no `namespace:` field** in the base kustomization. This is intentional. The base layer is environment-agnostic — it does not know whether it will be deployed to production or development. The namespace is set by whichever overlay renders the base.

The `labels` block adds `project: shift-festival` and `managed-by: Team-Infra` to every resource in the tree. `includeSelectors: false` means the labels are only added to `metadata.labels`, not to `spec.selector` (changing the selector would break existing Deployments by altering their pod selection logic).

The YAML files inside `base/` still contain hardcoded `namespace: shift-festival` in many places — this is fine. The overlay's namespace transformer overwrites every namespace field at render time. The source files on disk are never modified.

`base/setup/kustomization.yaml` intentionally does **not** contain a `secretGenerator`. Secrets are environment-specific and are declared in each overlay.

---

## 6. How the overlays work

An overlay is a small Kustomize layer that sits on top of the base. It declares only what is different about a specific environment. Everything that is the same stays in the base and is not repeated.

### Prod overlay (`overlays/prod/kustomization.yaml`)

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: shift-festival

resources:
  - ../../base
  - namespace.yaml

generatorOptions:
  disableNameSuffixHash: true

secretGenerator:
  - name: shift-secrets
    envs:
      - .env
```

Line by line:

| Field | What it does |
|-------|-------------|
| `namespace: shift-festival` | Runs the namespace transformer on every resource from `../../base`. All namespaced resources will have their namespace set to `shift-festival` in the rendered output. |
| `resources: ../../base` | Pulls in the entire shared manifest layer. |
| `resources: namespace.yaml` | Creates the `shift-festival` Kubernetes Namespace object on the cluster. |
| `secretGenerator` | Reads `overlays/prod/.env` and creates a Kubernetes Secret named `shift-secrets` in the `shift-festival` namespace. |
| `disableNameSuffixHash: true` | Prevents Kustomize from appending a content hash to the secret name (e.g. `shift-secrets-8f4k2m`). Without this, the generated name would change every time the secret content changes, and existing Deployments would fail to find it. |

### Dev overlay (`overlays/dev/kustomization.yaml`)

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: shift-festival-dev

resources:
  - ../../base
  - namespace.yaml

generatorOptions:
  disableNameSuffixHash: true

secretGenerator:
  - name: shift-secrets
    envs:
      - .env
```

Identical structure to prod, but with `namespace: shift-festival-dev`. Keel is **not** listed in `resources` — dev should not be auto-updated by the image watcher.

---

## 7. How namespace override works

This is the most important concept to understand in this refactor.

When Kustomize sees a `namespace:` field in a `kustomization.yaml`, it runs a **namespace transformer** over every resource in the rendered tree. The transformer:

1. **Sets** the `namespace` field on all namespace-scoped resources (Deployments, Services, PVCs, Secrets, ConfigMaps, etc.).
2. **Does not touch** cluster-scoped resources (Namespace objects, ClusterRoles, ClusterRoleBindings, PersistentVolumes, etc.).

This means: even though `base/setup/storage.yaml` has `namespace: shift-festival` hardcoded in every PVC, when rendered through `overlays/dev`, all those PVCs will have `namespace: shift-festival-dev` in the output. The source file is never modified.

**Concrete example:**

Source file `base/setup/storage.yaml`:
```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: rabbitmq-data-pvc
  namespace: shift-festival     ← hardcoded in the source file
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 2Gi
```

Rendered output of `kubectl kustomize overlays/dev`:
```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: rabbitmq-data-pvc
  namespace: shift-festival-dev  ← replaced in memory by the overlay transformer
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 2Gi
```

**Why each overlay needs its own `namespace.yaml`:**

Namespace objects (`kind: Namespace`) are cluster-scoped. Their identifier is the `metadata.name` field, not `metadata.namespace`. The transformer does not touch the `name` field of a Namespace object. If we kept `namespace.yaml` in the base with `name: shift-festival`, both overlays would try to manage the same `shift-festival` Namespace object, and the dev overlay would never create `shift-festival-dev`.

Solution: each overlay has its own `namespace.yaml` with the correct name for that environment.

---

## 8. How secrets work

All service Deployments in `base/` reference a Secret named `shift-secrets` via `envFrom` or `secretKeyRef`. Example:

```yaml
envFrom:
  - secretRef:
      name: shift-secrets
```

### With ArgoCD (current approach)

`shift-secrets` is **not** managed by kustomize. ArgoCD runs `kustomize build` by cloning the Git repo — it never has access to `.env` files on the VM. The `secretGenerator` blocks have been removed from both overlays.

The Secret is created directly on the cluster using `scripts/create-secret.sh`:

```bash
# Run once per environment, on the VM, after ArgoCD is installed:
./scripts/create-secret.sh setup/.env shift-festival
./scripts/create-secret.sh setup/.env shift-festival-dev
```

Re-run this command whenever secrets change. ArgoCD is configured to `ignoreDifferences` on the `shift-secrets` Secret so it does not try to delete or overwrite it.

Both prod and dev create a Secret with the **same name** (`shift-secrets`) but in **different namespaces** — they do not conflict.

### Secret file locations

| File | Purpose | Committed to git? |
|------|---------|------------------|
| `setup/.env` | Master secrets file — all real credentials | **NO** — gitignored |
| `overlays/prod/.env.example` | Template with all keys, placeholder values | YES |
| `overlays/dev/.env.example` | Template with all keys, placeholder values | YES |

The `setup/.env` file lives only on the VM and is the source of truth for secrets. It is never committed.

---

## 9. Image updates — ArgoCD Image Updater (replaces Keel)

### What changed

Keel has been replaced by **ArgoCD Image Updater** (`argocd/image-updater/`). Keel watched the registry and applied image updates directly to the cluster. Image Updater works differently — it detects a new image tag, commits the tag back to Git, and ArgoCD syncs the change. This keeps Git as the single source of truth with a full audit trail.

The `keel.sh/` annotations have been removed from all Deployments in `base/`. The `keel/` directory is kept for reference but is deprecated.

### How ArgoCD Image Updater works

1. Image Updater polls GHCR every 2 minutes for new `prod`/`dev` tags.
2. When a new tag is detected, it writes a commit to the target branch (e.g. updating the `images:` block in the overlay).
3. ArgoCD detects the new commit and syncs the overlay to the cluster.

The per-team `pipelines/deploy.yml` build pipelines are unchanged — they still push `prod` and `dev` tags to GHCR.

### ArgoCD Application placement

ArgoCD Application resources live in `argocd/applications/`. They reference the Git repo and overlay path:

```yaml
# argocd/applications/prod-app.yaml
spec:
  source:
    repoURL: https://github.com/integrationproject-groep1/Infra.git
    targetRevision: main
    path: overlays/prod
  destination:
    namespace: shift-festival
```

ArgoCD itself runs in the `argocd` namespace on the same cluster. It watches the Git repo directly — no files need to be SCP'd to the VM for manifest syncing.

---

## 10. Render and apply commands

### Render (inspect output without making changes)

```bash
# See the full rendered YAML for prod
kubectl kustomize overlays/prod

# See the full rendered YAML for dev
kubectl kustomize overlays/dev

# Save to a file for review
kubectl kustomize overlays/prod > /tmp/prod-manifests.yaml
```

### Dry run (validate without applying)

```bash
# Client-side validation (checks YAML syntax and known schema locally)
kubectl apply -k overlays/prod --dry-run=client

# Server-side validation (sends to API server for full schema + webhook validation, no objects created)
kubectl apply -k overlays/prod --dry-run=server
```

### Apply (makes real changes on the cluster)

```bash
# Deploy prod + Keel
kubectl apply -k overlays/prod
kubectl apply -k keel

# Deploy dev
kubectl apply -k overlays/dev

# Deploy everything via root passthrough (temporary)
kubectl apply -k .
```

### Rollback (via ArgoCD)

```bash
# List available revisions for an application
argocd app history shift-festival-prod

# Roll back to a specific revision (shown in ArgoCD UI or the above command)
argocd app rollback shift-festival-prod <revision>

# Or use the ArgoCD UI at https://argocd.desiderius.me → select app → History and Rollback
```

ArgoCD rollback points the application to a previous Git commit. It does NOT modify Git history — the rollback is a temporary state that stays active until the next sync.

---

## 11. PR integration test pipeline

**File:** `.github/workflows/pr-integration.yml`

### What it does in plain language

Every time someone opens or updates a pull request targeting `main` or `dev`, this pipeline automatically:

1. Starts a real (but temporary) Kubernetes cluster inside the GitHub Actions runner using **kind** (Kubernetes IN Docker).
2. Writes fake/placeholder values into the `.env` files so Kustomize can generate the Secrets without real credentials.
3. Applies all three manifest trees to the cluster: `overlays/prod`, `keel/`, and `overlays/dev`.
4. Checks by name that every expected Deployment, StatefulSet, and Secret was created.
5. Prints a full snapshot of the cluster to the logs for debugging.

If any step fails, the PR is blocked. If all steps pass, the PR gets a green checkmark for this workflow.

The cluster is thrown away when the job finishes — it exists only for the duration of the pipeline run.

### Step-by-step breakdown

| Step | What actually happens | Job fails if... |
|------|----------------------|----------------|
| **Create kind cluster** | GitHub Actions starts a Docker container that runs a full Kubernetes API server, scheduler, controller-manager, and etcd | kind cannot start within 120 seconds |
| **Install kubectl** | Downloads the `kubectl` binary (v1.30.1) onto the runner | Download fails |
| **Confirm cluster** | Runs `kubectl cluster-info` and `kubectl get nodes` to verify the cluster is up | Cluster is unreachable |
| **Write placeholder .env** | Creates `overlays/prod/.env` and `overlays/dev/.env` with dummy values (e.g. `ci_placeholder`) so the `secretGenerator` can run | File write fails |
| **Apply prod overlay** | Runs `kubectl apply -k overlays/prod` — Kustomize renders the full manifest tree and sends every object to the Kubernetes API | Any manifest has an invalid field, bad `apiVersion`, or references a resource that does not exist |
| **Apply Keel** | Runs `kubectl apply -k keel` | Same as above |
| **Apply dev overlay** | Runs `kubectl apply -k overlays/dev` | Same as above |
| **Verify named resources (prod)** | Checks ~20 specific resources by name (e.g. `deployment/rabbitmq-broker`, `statefulset/elasticsearch`, `secret/shift-secrets`) | Any expected resource is missing from `shift-festival` |
| **Verify named resources (dev)** | Checks a subset of the same resources in `shift-festival-dev` | Any expected resource is missing from `shift-festival-dev` |
| **Verify Keel** | Checks that `deployment/keel` exists in the `keel` namespace | Keel Deployment missing |
| **Print cluster snapshot** | Runs `kubectl get all` for all namespaces and prints the events log | Never — this step always runs even if earlier steps failed |

### Why pods show as Pending or ImagePullBackOff — and why this is normal

`kubectl apply` is purely an API call. It creates Kubernetes objects (Deployments, Services, Secrets, etc.) in the cluster's internal database (etcd) and returns immediately. It does **not** pull container images.

Image pulling happens later, asynchronously: the Kubernetes scheduler assigns a pod to a node, the kubelet on that node contacts the container registry, and tries to download the image. If the image does not exist or requires authentication that is not configured, the pod enters `ImagePullBackOff`.

In this pipeline, the `ghcr.io/integrationproject-groep1/` images are private or simply do not yet have the tags we reference. The kubelet will fail to pull them. This is **completely expected and does not cause any pipeline step to fail** — we never run `kubectl wait --for=condition=Ready` or anything else that checks pod health. We only verify that the Kubernetes objects were created.

When the team's images are built and pushed, those same pods will start running. The pipeline tests the structure of the manifests, not the runtime health of the applications.

### What real bugs this catches

Even though images are never pulled, the pipeline stops the following mistakes from reaching `main`:

| Mistake | How it is caught |
|---------|----------------|
| Moving a folder without updating `kustomization.yaml` | Kustomize render fails — `kubectl apply` exits non-zero |
| Using a deprecated `apiVersion` (e.g. `extensions/v1beta1`) | Kubernetes API rejects the resource |
| Typo in a field name (e.g. `limtis` instead of `limits`) | Kubernetes API rejects the resource |
| Referencing a ConfigMap by a name that does not exist in the same namespace | Kubernetes API rejects the Deployment |
| Adding a Deployment to a YAML file but forgetting to add the file to `kustomization.yaml` | The named resource check fails — the Deployment is never created |
| Breaking the overlay structure (e.g. wrong relative path in `resources`) | Kustomize render fails |
| Deleting a namespace.yaml so the namespace is never created | All resources are rejected (namespace does not exist) |

---

## 12. What Kainy (CI/CD) must update

The following files were **not modified** in this PR because they belong to Kainy's task. This section documents exactly what needs to change and why, so Kainy can base his work on this PR.

### `ci.yml` — Validate step

The current CI writes a placeholder `.env` to `setup/.env` and renders with `kubectl kustomize .`. Both paths are now wrong.

**Current (broken with new structure):**
```bash
cat > setup/.env <<'EOF'
...
EOF
kubectl kustomize . > "${RUNNER_TEMP}/rendered-manifests.yaml"
rm -f setup/.env
```

**Must become:**
```bash
cat > overlays/prod/.env <<'EOF'
...
EOF
kubectl kustomize overlays/prod > "${RUNNER_TEMP}/rendered-manifests.yaml"
rm -f overlays/prod/.env
```

Optionally also validate dev:
```bash
cp overlays/prod/.env overlays/dev/.env
kubectl kustomize overlays/dev > /dev/null
rm -f overlays/prod/.env overlays/dev/.env
```

### `deploy.yml` — Apply step

**Current:**
```bash
cd shiftfestival
if [[ ! -f setup/.env ]]; then
  echo "setup/.env is missing on the VM."
  exit 1
fi
kubectl apply -k .
```

**For push to `main`:**
```bash
cd shiftfestival
if [[ ! -f overlays/prod/.env ]]; then
  echo "overlays/prod/.env is missing on the VM."
  exit 1
fi
kubectl apply -k overlays/prod
kubectl apply -k keel
```

**For push to `dev`:**
```bash
cd shiftfestival
if [[ ! -f overlays/dev/.env ]]; then
  echo "overlays/dev/.env is missing on the VM."
  exit 1
fi
kubectl apply -k overlays/dev
```

To make the deploy step branch-aware in a single workflow:
```yaml
- name: Apply manifests
  run: |
    if [[ "${GITHUB_REF_NAME}" == "main" ]]; then
      kubectl apply -k overlays/prod
      kubectl apply -k keel
    elif [[ "${GITHUB_REF_NAME}" == "dev" ]]; then
      kubectl apply -k overlays/dev
    fi
```

### `deploy.yml` — Rollout checks

All existing `kubectl rollout status ... -n shift-festival` lines remain valid for prod. For dev deploys, change every `-n shift-festival` to `-n shift-festival-dev`. Keel does not need rollout checks on dev.

### VM — `.env` file location

The VM currently has its secrets at `~/shiftfestival/setup/.env`. This file must be copied (or moved) to `~/shiftfestival/overlays/prod/.env` before Kainy's updated deploy workflow is enabled. If this is not done, the first deploy after the update will fail immediately at the `.env` existence check.

---

## 13. Image naming convention for prod/dev

Once team images are built and pushed to `ghcr.io/integrationproject-groep1/`, each overlay should use the Kustomize `images:` transformer to pin per-environment image tags.

**Add to `overlays/prod/kustomization.yaml`:**
```yaml
images:
  - name: ghcr.io/integrationproject-groep1/kassa
    newTag: prod-latest
  - name: ghcr.io/integrationproject-groep1/crm
    newTag: prod-latest
  # ... one entry per team image
```

**Add to `overlays/dev/kustomization.yaml`:**
```yaml
images:
  - name: ghcr.io/integrationproject-groep1/kassa
    newTag: dev-latest
  - name: ghcr.io/integrationproject-groep1/crm
    newTag: dev-latest
```

The CI/CD pipeline then pushes two tags per build: `prod-latest` on merge to `main`, and `dev-latest` on merge to `dev`. Keel watches `shift-festival` and picks up `prod-latest` tag changes automatically.

The `images:` transformer only changes the tag — the registry and image name stay the same. This is done in the overlay rather than the base so the base stays environment-neutral.

---

## 14. Adding a new service

Follow these steps exactly when adding a new service:

1. **Create the manifest** in the correct subfolder under `base/` (e.g. `base/team-kassa/new-service.yaml`).

2. **Register it** in that folder's `kustomization.yaml`:
   ```yaml
   resources:
     - existing-service.yaml
     - new-service.yaml
   ```

3. **Add new env vars** to both example files:
   - `overlays/prod/.env.example`
   - `overlays/dev/.env.example`

4. **Add the real values** to `overlays/prod/.env` and `overlays/dev/.env` on the VM.

5. **Add a heartbeat sidecar** if the service must report liveness to the monitoring stack.

6. **Add a NodePort Service** only if public access is needed. Stay within the port range assigned to that team.

7. **Add rollout checks** in `deploy.yml`:
   ```bash
   kubectl rollout status deployment/new-service -n shift-festival --timeout=5m &
   ```

8. **Add the resource to the PR integration test** in `pr-integration.yml`:
   ```bash
   check deployment new-service shift-festival
   check deployment new-service shift-festival-dev
   ```

9. **Validate locally** before pushing:
   ```bash
   kubectl apply -k overlays/prod --dry-run=client
   ```

---

## 15. Troubleshooting

### `error: couldn't get resource list for ...` during apply

The cluster is missing a CRD required by one of the manifests. Check if a required operator is missing from the deployment.

### `The Namespace "shift-festival-dev" is invalid`

The dev namespace object is missing or `namespace.yaml` is not listed in `overlays/dev/kustomization.yaml` under `resources`.

### `secret "shift-secrets" not found`

The `shift-secrets` Secret was never bootstrapped in this namespace. Run:
```bash
./scripts/create-secret.sh setup/.env shift-festival
# or for dev:
./scripts/create-secret.sh setup/.env shift-festival-dev
```
Note: `secretGenerator` has been removed from the overlays — kustomize no longer creates this secret automatically.

### `no matches for kind "X" in version "Y"`

The `apiVersion` in a manifest is outdated. Check `kubectl version` to see the cluster version, then look up the correct API group for that resource.

### `patch target ... not found`

A Kustomize patch is targeting a resource that no longer exists in `base/` (renamed or deleted). Update the patch's `name:` field to match the current resource name.

### Pods are `ImagePullBackOff` in production

The image tag does not exist in the registry, or the imagePullSecret is missing. Check:
```bash
kubectl describe pod <pod-name> -n shift-festival | grep -A 5 "Events:"
```

### PR integration test fails on `kubectl apply`

Read the error output — it will name the exact resource and field that was rejected. Common causes:
- Wrong `apiVersion`
- Missing required field
- A `secretKeyRef` referencing a key that does not exist in the `.env`
- A Kustomize patch targeting the wrong resource name
