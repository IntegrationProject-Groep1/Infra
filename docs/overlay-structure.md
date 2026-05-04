# Kustomize Overlay Structure

## Why this change was made

Previously, all Kubernetes manifests lived at the repo root with a single `kustomization.yaml` hardcoded to the `shift-festival` namespace. This made it impossible to run a separate development environment without duplicating all manifests.

This refactor introduces a **base + overlays** pattern so prod and dev share one set of manifests, with only environment-specific differences (namespace, secrets) declared in each overlay.

---

## New Directory Layout

```
base/                         Shared manifests (no namespace set here)
  kustomization.yaml
  setup/
    kustomization.yaml        Only storage + configmaps (no secrets, no namespace object)
    configmaps.yaml
    storage.yaml
  core/                       RabbitMQ, PostgreSQL, Cloudflared, Dashboard
  team-frontend/              Drupal + MariaDB + Nginx proxy
  team-kassa/                 Odoo + PostgreSQL + Nginx proxy + integration sidecar
  team-facturatie/            FossBilling + MariaDB + Nginx proxy
  integrations/               CRM, Planning, Identity Service
  monitoring/                 ELK stack + monitoring agent

overlays/
  prod/
    kustomization.yaml        namespace: shift-festival
    namespace.yaml            Creates the shift-festival Namespace
    .env.example              Template — copy to .env and fill in prod values
    .env                      GITIGNORED — real prod secrets live here (and on the VM)

  dev/
    kustomization.yaml        namespace: shift-festival-dev
    namespace.yaml            Creates the shift-festival-dev Namespace
    .env.example              Template — copy to .env and fill in dev values
    .env                      GITIGNORED — real dev secrets live here

keel/
  keel.yaml                   Keel image updater (watches shift-festival namespace only)
  kustomization.yaml
```

---

## How Kustomize Resolves Namespaces

The `namespace:` field in an overlay `kustomization.yaml` acts as a transformer: Kustomize overwrites the `namespace` field on every namespaced resource in the tree. This means:

- All Deployments, Services, PVCs, ConfigMaps, and Secrets in `base/` get their namespace set to `shift-festival` when rendered through `overlays/prod`, and to `shift-festival-dev` through `overlays/dev`.
- Namespace *objects* (`kind: Namespace`) are cluster-scoped and are **not** affected by the transformer — each overlay declares its own `namespace.yaml` with the correct name.

---

## Secrets

The `shift-secrets` Kubernetes Secret is generated from the overlay's `.env` file via Kustomize's `secretGenerator`. Because prod and dev live in different namespaces, both can use the same secret name (`shift-secrets`) without conflict.

| File | Purpose |
|------|---------|
| `overlays/prod/.env` | Prod secrets — gitignored, must exist on the VM at deploy time |
| `overlays/dev/.env` | Dev secrets — gitignored, must exist on the VM at deploy time |
| `overlays/prod/.env.example` | Template committed to the repo |
| `overlays/dev/.env.example` | Template committed to the repo |

---

## Keel (Image Updater)

Keel lives in its own `keel/` directory and its own `keel` namespace. It is **not** included inside the overlays — if it were, the overlay's `namespace:` transformer would incorrectly override keel's namespace to `shift-festival`.

Instead, keel is referenced from the **root `kustomization.yaml`** alongside `overlays/prod`:

```yaml
# kustomization.yaml (root)
resources:
  - overlays/prod
  - keel
```

This means `kubectl apply -k .` deploys both prod and keel. The dev overlay does not include keel — dev images are not auto-updated.

Keel watches the `shift-festival` namespace only, configured via the `NAMESPACE` environment variable in `keel/keel.yaml`.

---

## Render Commands

```bash
# Render prod (requires overlays/prod/.env to exist)
kubectl kustomize overlays/prod

# Render dev (requires overlays/dev/.env to exist)
kubectl kustomize overlays/dev

# Dry-run prod
kubectl apply -k overlays/prod --dry-run=client

# Dry-run dev
kubectl apply -k overlays/dev --dry-run=client
```

---

## What Kainy Needs to Update (CI/CD)

The following changes are outside the scope of this PR and are owned by Zafari Kainy:

### 1. Deploy workflow (`deploy.yml`)

| Before | After |
|--------|-------|
| `kubectl apply -k .` | `kubectl apply -k overlays/prod && kubectl apply -k keel` (push to `main`) |
| — | `kubectl apply -k overlays/dev` (push to `dev`) |
| `if [[ ! -f setup/.env ]]` | `if [[ ! -f overlays/prod/.env ]]` |
| SCP copies `setup/.env` convention | SCP or VM must have `overlays/prod/.env` in place |

### 2. CI validate step (`ci.yml`)

| Before | After |
|--------|-------|
| `cat > setup/.env` then `kubectl kustomize .` | Write `.env` to `overlays/prod/.env`, then `kubectl kustomize overlays/prod` |
| — | Optionally also validate `overlays/dev` with a dev placeholder `.env` |

### 3. Rollout checks in `deploy.yml`

All `kubectl rollout status ... -n shift-festival` lines remain valid for prod. No changes needed for the namespace itself — only the apply command changes.

---

## Image Naming Convention (for Kainy's CI/CD)

When the CI/CD builds and pushes images to GHCR, the image tag should identify the environment. Recommended approach using Kustomize's `images:` transformer in each overlay:

**`overlays/prod/kustomization.yaml`** — add:
```yaml
images:
  - name: ghcr.io/integrationproject-groep1/kassa
    newTag: prod-latest
```

**`overlays/dev/kustomization.yaml`** — add:
```yaml
images:
  - name: ghcr.io/integrationproject-groep1/kassa
    newTag: dev-latest
```

The CI pipeline then pushes two tags per image (`prod-latest` / `dev-latest`) and Keel picks up the `prod-latest` tag in the prod namespace automatically.
