# Security Architecture — ShiftFestival Infrastructure (Kubernetes)

**Last Updated:** 2026-05-13  
**Status:** Production (Consolidated & Hardened)  
**Scope:** Kubernetes Manifests (`base/`, `argocd/`)

---

## 1. Security Philosophy
ShiftFestival has migrated from a standalone Docker Compose stack to a hardened Kubernetes environment. Security is enforced through declarative manifests, non-root policies, and automated secret management. The architecture has been simplified to a single-overlay structure to reduce configuration drift and improve auditability.

---

## 2. Workload Security (Pod Security Contexts)

All deployments follow a "Least Privilege" principle. Most workloads implement the following constraints:

- **Non-Root Enforcement:** Workloads run with `runAsNonRoot: true`.
- **Privilege Escalation:** `allowPrivilegeEscalation: false` is set to prevent child processes from gaining more privileges than their parent.
- **Immutable Filesystem:** `readOnlyRootFilesystem: true` is enforced where possible. Temporary data is stored in `emptyDir` volumes.
- **Capability Dropping:** Containers drop all default Linux capabilities (`drop: ["ALL"]`) unless strictly necessary.
- **Volume Permissions:** Permissions for PersistentVolumes are managed via `fsGroup` in the Pod's security context, eliminating the need for root-level `chown` initContainers.

---

## 3. Network Security & Ingress

### External Access (Cloudflare Tunnel)
Production ingress is primarily managed via **Cloudflare Tunnels** (`cloudflared`). This eliminates the need to open ports 80/443 on the VM.

| Service | Access Type |
|---------|-------------|
| Frontend (Drupal) | Cloudflare Tunnel |
| Kassa (Odoo) | Cloudflare Tunnel |
| Facturatie (FossBilling) | Cloudflare Tunnel |
| RabbitMQ Management | Cloudflare Tunnel |
| Kibana | Cloudflare Tunnel |

### Direct Access (NodePorts)
Selected services use NodePorts within assigned team ranges (30000–30100). Access to these ports should be restricted via the cloud provider's Network Security Group (NSG) to trusted IP ranges or VPNs.

### Internal Isolation
- **ClusterIP Services:** Most databases and internal APIs are exposed only via `ClusterIP`, making them unreachable from outside the cluster.
- **Namespace Isolation:** All application workloads run in the `shift-festival` namespace.
- **ArgoCD Isolation:** ArgoCD components run in the `argocd` namespace.
- **Rollouts Isolation:** Argo Rollouts controller runs in the `argo-rollouts` namespace.

---

## 4. Secret & Config Management

- **Secrets:** Credentials are managed as Kubernetes Secrets, bootstrapped manually via `scripts/create-secret.sh` from a local (non-committed) `base/setup/.env` file. ArgoCD is configured to ignore the `shift-secrets` Secret so it is not managed or pruned by GitOps sync.
- **Config:** Application settings are managed via ConfigMaps.
- **Zero-Secret Commits:** Git history is protected. No secrets are stored in the repository. CI pipelines run Gitleaks to enforce this.

---

## 5. Deployment & Release Security

- **Argo Rollouts (Modern Rollback):** All core team services (Frontend, Kassa, Facturatie, Identity) have been migrated from standard `Deployments` to `Argo Rollouts`. This provides:
  - **Automated Health Checks:** New versions are monitored for 5 minutes before being fully promoted.
  - **Automatic Abort:** If a pod crashes (CrashLoopBackOff), the rollout is automatically aborted and the previous version is restored.
- **Image Updates (ArgoCD Image Updater):** Automated image updates are managed by ArgoCD Image Updater, which polls GHCR and commits new tags back to `kustomization.yaml` in Git, triggering an ArgoCD sync.
- **GitOps (ArgoCD):** All cluster state is managed from Git. ArgoCD self-healing reverts manual cluster changes.
- **CI Security Gate:**
  - `yamllint`: Ensures manifest structural integrity.
  - `Trivy`: Scans Kubernetes manifests for security misconfigurations.
  - `Gitleaks`: Prevents secret leakage in Git history.

---

## 6. Audit & Findings

### Opgeloste Bevindingen (K8s Migration & Hardening)
- [x] **H7 — Root InitContainers:** Root-level `fix-permissions` containers have been removed. Permissions are handled via `fsGroup`.
- [x] **H8 — Database Conflicts:** `Recreate` strategy (now managed via Rollout steps) prevents concurrent volume access errors.
- [x] **H9 — Hardcoded Passwords:** All database passwords moved to K8s Secrets.
- [x] **H10 — FossBilling Root:** Fixed `fossbilling-app` deployment (now Rollout) to run as user `33` (www-data) instead of root (0).
- [x] **H11 — Script Deprecation:** Imperative bash scripts (`runtime-rollback.sh`) have been replaced by declarative Argo Rollouts to ensure GitOps integrity.

### Accepted / Suppressed Findings

- [x] **A1 — Trivy: ArgoCD upstream manifest findings (KSV-0041, KSV-0044, KSV-0046, KSV-0118)**
  - **Affected file:** `argocd/install.yaml` and `argocd/image-updater/install.yaml`
  - **Findings:** Broad ClusterRole RBAC permissions and missing pod security contexts in upstream manifests.
  - **Decision:** Accepted. Required for ArgoCD to function as a cluster-wide controller.

- [x] **A2 — Trivy: Ingress-Nginx ClusterRole permissions (KSV-0041)**
  - **Decision:** Accepted. Required for monitoring TLS secrets across namespaces.

- [x] **A3 — Trivy: ExternalName services (KSV-0108)**
  - **Decision:** Accepted. Used for safe internal cross-namespace routing.

- [x] **A4 — Trivy: Argo Rollouts upstream manifest findings**
  - **Affected file:** `argocd/rollouts/kustomization.yaml` (remote install.yaml)
  - **Findings:** Similar to A1 (RBAC and security context).
  - **Decision:** Accepted. Required for the Rollouts controller to manage resources across the cluster.

### Open Bevindingen / Toekomstige Verbeteringen
- [ ] **M1 — NetworkPolicies:** Implement Egress/Ingress policies to restrict inter-pod communication.
- [ ] **M2 — Resource Quotas:** Enforce Namespace-level resource limits.
- [ ] **M3 — Seccomp/AppArmor:** Further harden pods using Seccomp profiles.

---

## 7. Incident Response & Rollback

- **Automated Rollback:** Handled by **Argo Rollouts**. If a new version fails health checks, it is automatically aborted.
- **Manual Rollback:** `kubectl argo rollouts rollback <name> -n shift-festival`
- **State Recovery:** Re-apply the last known-good configuration from the `main` branch.
- **Logs:** Centralized logging via Logstash → Elasticsearch → Kibana for forensic analysis. Logs of failed/aborted rollout pods are preserved in Elasticsearch.
