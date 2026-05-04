# Kustomize Overlay Structure — ShiftFestival Infra

**Author:** Tom DeKoning (s) — Infra Team  
**Branch:** `refactor/kustomize-overlay-structure`  
**Last updated:** 2026-05-04  
**Reviewed by:** *(pending TL review)*

---

## Table of Contents

1. [Why this refactor was done](#1-why-this-refactor-was-done)
2. [What Kustomize is and how it works](#2-what-kustomize-is-and-how-it-works)
3. [Repository structure](#3-repository-structure)
4. [How the base layer works](#4-how-the-base-layer-works)
5. [How the overlays work](#5-how-the-overlays-work)
6. [How namespace override works](#6-how-namespace-override-works)
7. [How secrets work](#7-how-secrets-work)
8. [Keel (image updater) placement](#8-keel-image-updater-placement)
9. [Render and apply commands](#9-render-and-apply-commands)
10. [PR integration test pipeline](#10-pr-integration-test-pipeline)
11. [What Kainy (CI/CD) must update](#11-what-kainy-cicd-must-update)
12. [Image naming convention for prod/dev](#12-image-naming-convention-for-proddev)
13. [Adding a new service](#13-adding-a-new-service)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. Why this refactor was done

### The problem before this change

Before this refactor, the repo had a single flat layout:

```
kustomization.yaml      ← hardcoded namespace: shift-festival
setup/
core/
team-frontend/
...
```

This meant there was only one environment: production. To test a change before pushing to production, a developer had to either:
- Push directly to prod and hope it works, or
- Maintain a completely separate copy of all manifests for a dev environment

Both options are bad. Duplicating manifests means changes need to be made twice and the environments drift apart. Pushing untested changes to prod is risky.

### The solution

We introduced a **base + overlays** pattern using Kustomize. This is the industry-standard approach for managing multiple Kubernetes environments from a single source of truth.

The same set of manifests (the `base/`) is now shared between production and development. The only differences between environments (namespace name, secrets) are declared in small overlay files. There is no duplication.

---

## 2. What Kustomize is and how it works

Kustomize is a tool built into `kubectl` that lets you customize Kubernetes YAML files without duplicating them. It uses a `kustomization.yaml` file in each directory to declare:

- Which YAML files to include (`resources`)
- Transformations to apply (`namespace`, `namePrefix`, `labels`, etc.)
- Patches that override specific fields
- Generators that create new resources (like Secrets from `.env` files)

When you run `kubectl kustomize <directory>`, Kustomize:
1. Reads the `kustomization.yaml` in that directory
2. Loads all referenced resource files
3. Applies all declared transformations and patches
4. Outputs a single merged YAML stream

When you run `kubectl apply -k <directory>`, it does the same but also sends the result to the Kubernetes API server.

**Key principle: Kustomize never modifies your source files.** It only transforms them in memory during rendering. Your YAML files on disk stay clean.

---

## 3. Repository structure

```
.
├── base/                         ← The shared layer. Contains every manifest.
│   ├── kustomization.yaml        ← Lists all sub-directories; sets shared labels.
│   ├── setup/
│   │   ├── kustomization.yaml    ← References storage.yaml and configmaps.yaml.
│   │   ├── storage.yaml          ← All PersistentVolumeClaims (storage requests).
│   │   └── configmaps.yaml       ← RabbitMQ, Nginx, and Drupal ConfigMaps.
│   ├── core/
│   │   ├── cloudflared.yaml      ← Cloudflare tunnel (external access).
│   │   ├── kubernetes-dashboard.yaml
│   │   ├── postgres.yaml         ← Shared PostgreSQL (identity + pgAdmin).
│   │   └── rabbitmq.yaml         ← RabbitMQ message broker.
│   ├── team-frontend/
│   │   ├── drupal.yaml           ← Drupal CMS application.
│   │   ├── mariadb.yaml          ← Drupal's own MariaDB database.
│   │   └── proxy.yaml            ← Nginx reverse proxy for Drupal.
│   ├── team-kassa/
│   │   ├── odoo.yaml             ← Odoo ERP (POS system).
│   │   ├── postgres.yaml         ← Kassa's own PostgreSQL.
│   │   ├── integration.yaml      ← Kassa integration sidecar.
│   │   └── proxy.yaml            ← Nginx reverse proxy for Odoo.
│   ├── team-facturatie/
│   │   ├── fossbilling.yaml      ← FossBilling invoicing application.
│   │   ├── mariadb.yaml          ← FossBilling's MariaDB.
│   │   ├── mariadb-config.yaml   ← MariaDB configuration.
│   │   └── proxy.yaml            ← Nginx reverse proxy.
│   ├── integrations/
│   │   ├── crm.yaml              ← Salesforce CRM receiver.
│   │   ├── identity-service.yaml ← UUID identity service.
│   │   └── planning.yaml         ← Office 365 planning integration.
│   └── monitoring/
│       ├── elasticsearch.yaml    ← Elasticsearch (log storage).
│       ├── kibana.yaml           ← Kibana (log dashboard).
│       ├── logstash.yaml         ← Logstash (log pipeline).
│       ├── logstash-config.yaml  ← Logstash pipeline configuration.
│       ├── logstash-settings.yaml
│       └── monitoring-agent.yaml ← Heartbeat monitoring agent.
│
├── overlays/
│   ├── prod/
│   │   ├── kustomization.yaml    ← Sets namespace: shift-festival; generates prod secret.
│   │   ├── namespace.yaml        ← Creates the shift-festival Namespace object.
│   │   ├── .env                  ← GITIGNORED. Real prod secrets. Must exist on VM.
│   │   └── .env.example          ← Template. Safe to commit. Copy to .env and fill in.
│   └── dev/
│       ├── kustomization.yaml    ← Sets namespace: shift-festival-dev; generates dev secret.
│       ├── namespace.yaml        ← Creates the shift-festival-dev Namespace object.
│       ├── .env                  ← GITIGNORED. Dev secrets. Must exist on VM.
│       └── .env.example          ← Template. Copy to .env and fill in.
│
├── keel/
│   ├── keel.yaml                 ← Keel image updater. Watches shift-festival only.
│   └── kustomization.yaml
│
├── kustomization.yaml            ← Root passthrough: references overlays/prod + keel.
│                                   Temporary. Kainy will replace with branch-aware CI/CD.
│
├── .github/
│   └── workflows/
│       ├── ci.yml                ← Existing: lint, security scan (Kainy updates this).
│       ├── deploy.yml            ← Existing: deploy to Azure VM (Kainy updates this).
│       └── pr-integration.yml    ← NEW: kind cluster test on every pull request.
│
├── scripts/                      ← Rollback and Teams notification scripts.
├── docs/
│   └── overlay-structure.md      ← This document.
├── CLAUDE.md                     ← Guidance for Claude Code AI assistant.
├── SECURITY.md                   ← Security audit findings and status.
└── README.md                     ← Public-facing project overview.
```

---

## 4. How the base layer works

`base/kustomization.yaml` looks like this:

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

The labels block adds `project: shift-festival` and `managed-by: Team-Infra` to every resource in the tree. `includeSelectors: false` means the labels are only added to `metadata.labels`, not to `spec.selector` (which would break existing deployments by changing their pod selector).

The YAML files in `base/` still contain hardcoded `namespace: shift-festival` fields in many places (e.g. in `storage.yaml`, `configmaps.yaml`). This does not matter — the overlay's namespace transformer overwrites them at render time. See section 6 for how this works.

`base/setup/kustomization.yaml` intentionally does **not** contain a `secretGenerator`. Secrets are environment-specific and are declared in each overlay instead.

---

## 5. How the overlays work

An overlay is a small Kustomize layer that sits on top of the base. It declares what is different about a specific environment. Everything that is the same stays in the base.

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

What this does:
- `namespace: shift-festival` — applies the namespace transformer to all resources from `../../base`.
- `resources: ../../base` — includes the entire base layer.
- `resources: namespace.yaml` — creates the `shift-festival` Namespace Kubernetes object.
- `secretGenerator` — reads `overlays/prod/.env` and generates a Kubernetes Secret named `shift-secrets` in the `shift-festival` namespace.
- `disableNameSuffixHash: true` — prevents Kustomize from appending a content hash to the secret name (e.g. `shift-secrets-8f4k2m`). Without this, every time the secret content changes, the generated name changes and existing Deployments can't find it.

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

Identical structure to prod, but `namespace: shift-festival-dev`. Keel is **not** included in the dev overlay — Keel is an image auto-updater that should only touch the production namespace.

---

## 6. How namespace override works

This is the most important concept to understand in this refactor.

When Kustomize sees a `namespace:` field in a `kustomization.yaml`, it runs a **namespace transformer** over every resource in the rendered tree. The transformer:

1. **Sets** the `namespace` field on all resources that are namespace-scoped (Deployments, Services, PVCs, Secrets, ConfigMaps, etc.).
2. **Does not touch** cluster-scoped resources (Namespace objects, ClusterRoles, ClusterRoleBindings, PersistentVolumes, etc.).

This means: even though `base/setup/storage.yaml` has `namespace: shift-festival` hardcoded in every PVC, when rendered through `overlays/dev`, all those PVCs will have `namespace: shift-festival-dev` in the output. The source file is not modified.

**Concrete example:**

Source file `base/setup/storage.yaml`:
```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: rabbitmq-data-pvc
  namespace: shift-festival     ← hardcoded in source
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 2Gi
```

Output of `kubectl kustomize overlays/dev`:
```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: rabbitmq-data-pvc
  namespace: shift-festival-dev  ← replaced by overlay transformer
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 2Gi
```

**Why the Namespace object needs its own namespace.yaml per overlay:**

Namespace objects (`kind: Namespace`) are cluster-scoped, not namespace-scoped. Their identifier is the `metadata.name` field, not `metadata.namespace`. The transformer does not touch the `name` field of a Namespace object. If we kept `setup/namespace.yaml` in the base with `name: shift-festival`, both overlays would try to create/manage the same `shift-festival` Namespace object, and the dev overlay would never create `shift-festival-dev`.

The solution: each overlay has its own `namespace.yaml` that creates the correct Namespace object for that environment.

---

## 7. How secrets work

All service Deployments in `base/` reference a Secret named `shift-secrets` via `envFrom` or `secretKeyRef`. For example:

```yaml
envFrom:
  - secretRef:
      name: shift-secrets
```

The `shift-secrets` Secret is generated by each overlay's `secretGenerator` from the overlay's local `.env` file. Both prod and dev generate a Secret with the **same name** (`shift-secrets`), but they live in **different namespaces** (`shift-festival` vs `shift-festival-dev`) and therefore do not conflict.

This is why we use the same name in both overlays instead of `shift-secrets-dev` for dev: if we used a different name in dev, every single Deployment in `base/` would need a dev-specific patch to reference the new name — that would be 18+ patches for no real benefit. Namespace isolation already keeps them separate.

### Secret files and their locations

| File | Purpose | Committed? |
|------|---------|-----------|
| `overlays/prod/.env` | Real prod credentials | **NO** (gitignored) |
| `overlays/dev/.env` | Real dev credentials | **NO** (gitignored) |
| `overlays/prod/.env.example` | Template with empty values | YES |
| `overlays/dev/.env.example` | Template with empty values | YES |

The `.env` files must exist on the VM before a deploy runs. They are placed there manually by the infra team. The CI/CD pipeline verifies their presence before applying manifests.

---

## 8. Keel (image updater) placement

### What Keel does

Keel watches Kubernetes Deployments for image tag updates. When a new image is pushed to a registry with a matching tag pattern, Keel automatically updates the Deployment to use the new image. This enables zero-touch prod deployments when a team pushes a new image.

Keel is configured to watch only the `shift-festival` namespace via the `NAMESPACE` environment variable in `keel/keel.yaml`. Dev is deliberately excluded from auto-updates.

### Why Keel is NOT inside the overlay

Keel runs in its own `keel` Kubernetes namespace. If we included `../../keel` inside `overlays/prod/kustomization.yaml`, the prod overlay's namespace transformer (`namespace: shift-festival`) would override Keel's namespace from `keel` to `shift-festival`. This would break Keel entirely — its RBAC, ServiceAccount, and Service would all land in the wrong namespace.

### How it is included instead

Keel is referenced from the **root `kustomization.yaml`**, which is outside any overlay and applies no namespace transformer:

```yaml
# kustomization.yaml (root)
resources:
  - overlays/prod
  - keel
```

When Kustomize processes this, `overlays/prod` is rendered with its namespace transformer (affecting only its own resources), and `keel` is rendered without any namespace transformer (preserving `namespace: keel` on all Keel resources).

This root `kustomization.yaml` is a **temporary passthrough** to make `kubectl apply -k .` work until Kainy updates the CI/CD pipeline to call the overlays directly. See section 11.

---

## 9. Render and apply commands

### Render only (no changes to cluster)

```bash
# Render prod — requires overlays/prod/.env to exist
kubectl kustomize overlays/prod

# Render dev — requires overlays/dev/.env to exist
kubectl kustomize overlays/dev

# Render keel standalone
kubectl kustomize keel
```

Use these to inspect the final merged YAML before applying. Useful for reviewing what a change actually does.

### Dry run (validate against API, no changes created)

```bash
# Validate prod manifests against a real cluster's API without creating anything
kubectl apply -k overlays/prod --dry-run=client

# Server-side dry run — sends to API server for full validation but does not persist
kubectl apply -k overlays/prod --dry-run=server
```

`--dry-run=client` only validates locally. `--dry-run=server` is more thorough because the API server applies admission webhooks and CRD schema validation.

### Apply (makes real changes)

```bash
# Deploy prod
kubectl apply -k overlays/prod
kubectl apply -k keel

# Deploy dev
kubectl apply -k overlays/dev

# Deploy everything at once via root passthrough (temporary, until Kainy updates CI/CD)
kubectl apply -k .
```

### Rollback

```bash
# Roll back a specific Deployment
kubectl rollout undo deployment/<name> -n shift-festival

# Check rollout history
kubectl rollout history deployment/<name> -n shift-festival

# Roll back to a specific revision
kubectl rollout undo deployment/<name> -n shift-festival --to-revision=2
```

---

## 10. PR integration test pipeline

### File location

`.github/workflows/pr-integration.yml`

### When it runs

Only on `pull_request` events targeting `main` or `dev`. It never runs on direct pushes.

### What it does, step by step

| Step | What happens | Fails if |
|------|-------------|----------|
| Create kind cluster | Spins up a full Kubernetes cluster inside Docker on the GitHub runner | kind fails to start |
| Write placeholder .env | Creates dummy `.env` files so `secretGenerator` can render | heredoc write fails |
| `kubectl apply -k overlays/prod` | Sends all prod manifests to the kind API | Any manifest has a schema error, bad apiVersion, or invalid field |
| `kubectl apply -k keel` | Applies Keel manifests | Same |
| `kubectl apply -k overlays/dev` | Sends all dev manifests to the kind API | Same |
| Verify named resources (prod) | Checks ~20 specific Deployments/StatefulSets/Secrets by name | Any expected resource is missing |
| Verify named resources (dev) | Same subset check for dev namespace | Any expected resource is missing |
| Verify Keel | Confirms `deployment/keel` exists in `keel` namespace | Keel Deployment missing |
| Print cluster snapshot | Shows `kubectl get all` output for all namespaces | Never (always runs) |

### Why pods are Pending/ImagePullBackOff and why that is fine

`kubectl apply` is a Kubernetes API operation. It creates Deployment, Service, PVC, Secret, and other objects in the cluster's database (etcd). It does not pull container images.

Image pulling happens later, when the Kubernetes scheduler assigns a pod to a node and the kubelet on that node tries to start the containers. At that point, the kubelet contacts the container registry (`ghcr.io`) and attempts to pull the image. If the image does not exist or requires authentication that the kubelet does not have, the pod enters `ImagePullBackOff`.

In this CI pipeline there are no image pull secrets configured for `ghcr.io/integrationproject-groep1/`. As a result, almost all pods will be in `ImagePullBackOff` or `Pending` state. **This is completely expected and does not cause the pipeline to fail.** We never run `kubectl wait --for=condition=Ready` or any command that blocks on pod health. We only verify that the Kubernetes objects were created.

### What real bugs this catches

The following are examples of mistakes that would cause this pipeline to fail, even though images are never pulled:

- A developer renames a folder (`team-kassa/` → `kassa/`) without updating `base/kustomization.yaml` → Kustomize fails to render → `kubectl apply` fails
- A developer changes `apiVersion: apps/v1` to `apiVersion: apps/v1beta1` in a Deployment → Kubernetes API rejects it → `kubectl apply` fails
- A developer adds a new `envFrom` block referencing a ConfigMap that does not exist → Kubernetes API rejects the Deployment → step fails
- A developer deletes a service manifest but leaves a reference to it in another manifest's selector → Kustomize may fail or apply will show an error
- A developer adds a Deployment to `base/` but forgets to add it to `base/kustomization.yaml` → it is silently absent → the named resource check detects it is missing and fails

---

## 11. What Kainy (CI/CD) must update

The following files were **not modified** in this PR because they belong to Kainy's task. They are documented here so Kainy knows exactly what to change and why.

### `ci.yml` — Validate step

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

Also optionally validate dev:
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
  echo "setup/.env is missing on the VM. Create it before deploying."
  exit 1
fi
kubectl apply -k .
```

**Must become (push to `main`):**
```bash
cd shiftfestival
if [[ ! -f overlays/prod/.env ]]; then
  echo "overlays/prod/.env is missing on the VM. Create it before deploying."
  exit 1
fi
kubectl apply -k overlays/prod
kubectl apply -k keel
```

**Must become (push to `dev`):**
```bash
cd shiftfestival
if [[ ! -f overlays/dev/.env ]]; then
  echo "overlays/dev/.env is missing on the VM. Create it before deploying."
  exit 1
fi
kubectl apply -k overlays/dev
```

To make this branch-aware, Kainy can use:
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

The rollout check commands (`kubectl rollout status ... -n shift-festival`) remain valid for prod deploys. For a dev deploy, the same commands should target `-n shift-festival-dev`. The dev deploy workflow does not need Keel rollout checks.

### VM setup — `.env` file location

The VM currently has a file at `~/shiftfestival/setup/.env`. This file needs to be moved to (or a copy created at) `~/shiftfestival/overlays/prod/.env` before the updated deploy workflow is enabled. Otherwise the first deploy after Kainy's changes will fail at the `.env` existence check.

---

## 12. Image naming convention for prod/dev

Once team images are built and pushed to `ghcr.io/integrationproject-groep1/`, each overlay should pin or tag images differently per environment. The Kustomize way to do this is the `images:` transformer.

**Example for `overlays/prod/kustomization.yaml`:**
```yaml
images:
  - name: ghcr.io/integrationproject-groep1/kassa
    newTag: prod-latest
  - name: ghcr.io/integrationproject-groep1/crm
    newTag: prod-latest
```

**Example for `overlays/dev/kustomization.yaml`:**
```yaml
images:
  - name: ghcr.io/integrationproject-groep1/kassa
    newTag: dev-latest
  - name: ghcr.io/integrationproject-groep1/crm
    newTag: dev-latest
```

The CI/CD pipeline would then push two tags per image: `prod-latest` (on push to main) and `dev-latest` (on push to dev). Keel watches the `shift-festival` namespace and picks up `prod-latest` tag updates automatically.

The `images:` transformer only overrides the tag — the image name and registry stay the same. You add one entry per image that needs environment-specific tagging.

---

## 13. Adding a new service

Follow these steps when adding a new team service to the platform:

1. **Create the manifest(s)** in the correct folder under `base/` (e.g. `base/team-kassa/my-new-service.yaml`).

2. **Update the folder's `kustomization.yaml`** to include the new file:
   ```yaml
   resources:
     - existing-service.yaml
     - my-new-service.yaml     # add this line
   ```

3. **Add any new environment variables** to both `.env.example` files:
   - `overlays/prod/.env.example`
   - `overlays/dev/.env.example`
   
   And document what each variable does with a comment.

4. **Place the real values** in the actual `.env` files on the VM (`overlays/prod/.env` and `overlays/dev/.env`).

5. **Add a heartbeat sidecar** if the service needs to report liveness to the monitoring stack.

6. **Add a NodePort Service** only if the service needs to be publicly accessible. Stay within the assigned port range for that team.

7. **Add rollout checks in `deploy.yml`**:
   ```bash
   kubectl rollout status deployment/my-new-service -n shift-festival --timeout=5m &
   ```

8. **Add the service to the named resource check in `pr-integration.yml`**:
   ```bash
   check deployment my-new-service shift-festival
   check deployment my-new-service shift-festival-dev
   ```

9. **Validate locally** before opening a PR:
   ```bash
   kubectl kustomize overlays/prod | head -100   # spot check the rendered output
   kubectl apply -k overlays/prod --dry-run=client
   ```

---

## 14. Troubleshooting

### `error: couldn't get resource list for ...` during apply

The cluster does not have the CRD for that resource. Check if a required operator or CRD installer is missing from the manifests.

### `The Namespace "shift-festival-dev" is invalid`

The dev namespace object is missing or the overlay's `namespace.yaml` was not included in `resources`. Check `overlays/dev/kustomization.yaml`.

### `secret "shift-secrets" not found`

The overlay's `secretGenerator` did not run, usually because the `.env` file was missing at render time. Make sure `overlays/prod/.env` (or `overlays/dev/.env`) exists before running `kubectl apply`.

### `no matches for kind "X" in version "Y"`

You are using an outdated `apiVersion`. Check the current Kubernetes version on the cluster (`kubectl version`) and look up the correct group/version for that resource kind.

### `patch target ... not found`

A Kustomize patch in an overlay is targeting a resource that no longer exists in `base/`. The resource was probably renamed or deleted. Update the patch's `name:` to match the current resource name.

### Pods are `ImagePullBackOff` in production

Either the image tag does not exist in `ghcr.io/integrationproject-groep1/`, or the imagePullSecret is missing/expired. Check:
```bash
kubectl describe pod <pod-name> -n shift-festival | grep -A 5 "Events:"
```

### PR integration test fails on `kubectl apply`

Read the error message carefully. It will say exactly which resource was rejected and why. Common causes:
- Wrong `apiVersion`
- Missing required field
- A `secretKeyRef` referencing a key that does not exist in `shift-secrets`
- A Kustomize patch targeting the wrong resource name
