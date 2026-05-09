# Security Architecture — ShiftFestival Infrastructure (Kubernetes)

**Last Updated:** 2026-04-30  
**Status:** Production (Migrated from Docker Compose)  
**Scope:** Kubernetes Manifests (`setup/`, `core/`, `team-*`, `integrations/`, `monitoring/`)

---

## 1. Security Philosophy
ShiftFestival has migrated from a standalone Docker Compose stack to a hardened Kubernetes environment. Security is enforced through declarative manifests, non-root policies, and automated secret management.

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
- **Namespace Isolation:** All application workloads run in the `shift-festival` namespace. Release automation runs in the `keel` namespace.

---

## 4. Secret & Config Management

- **Secrets:** Credentials are managed as Kubernetes Secrets, bootstrapped manually via `scripts/create-secret.sh` from a local (non-committed) `setup/.env` file. ArgoCD is configured to ignore the `shift-secrets` Secret so it is not managed or pruned by GitOps sync.
- **Config:** Application settings are managed via ConfigMaps.
- **Zero-Secret Commits:** Git history is protected. No secrets are stored in the repository. CI pipelines run Gitleaks to enforce this.

---

## 5. Deployment & Release Security

- **Image Updates (ArgoCD Image Updater):** Automated image updates are managed by ArgoCD Image Updater, which polls GHCR and commits new tags back to Git, triggering an ArgoCD sync. Replaces Keel.
- **GitOps (ArgoCD):** All cluster state is managed from Git. ArgoCD self-healing reverts manual cluster changes. The `argocd/` directory contains pinned upstream manifests (v2.11.0).
- **CI Security Gate:**
  - `yamllint`: Ensures manifest structural integrity.
  - `Trivy`: Scans Kubernetes manifests for security misconfigurations. The `argocd/` directory is excluded from Trivy scanning — see finding A1 below.
  - `Gitleaks`: Prevents secret leakage in Git history.
- **Deployment Strategy:** Database workloads use the `Recreate` strategy to ensure stable volume transitions and prevent "Multi-Attach" errors.

---

## 6. Audit & Findings

### Opgeloste Bevindingen (K8s Migration)
- [x] **H7 — Root InitContainers:** Root-level `fix-permissions` containers have been removed. Permissions are handled via `fsGroup`.
- [x] **H8 — Database Conflicts:** `Recreate` strategy prevents concurrent volume access errors.
- [x] **H9 — Hardcoded Passwords:** All database passwords moved to K8s Secrets.

### Accepted / Suppressed Findings

- [x] **A1 — Trivy: ArgoCD upstream manifest findings (KSV-0041, KSV-0044, KSV-0046, KSV-0118)**
  - **Affected file:** `argocd/install.yaml` and `argocd/image-updater/install.yaml` (upstream vendor manifests, pinned at v2.11.0 / v0.15.1)
  - **Findings:** Broad ClusterRole RBAC permissions (KSV-0041, KSV-0044, KSV-0046) and missing pod security contexts (KSV-0118) in ArgoCD's own deployments.
  - **Decision:** Accepted. ArgoCD requires cluster-wide RBAC to function as a GitOps controller — these permissions are intentional and documented by the ArgoCD project. The security context findings are in upstream code we do not modify. The `argocd/` directory is excluded from Trivy scanning (`skip-dirs: argocd` in `ci.yml`). The pinned version is reviewed on each upgrade.

- [x] **A2 — Trivy: Ingress-Nginx ClusterRole permissions (KSV-0041)**
  - **Affected file:** `base/core/ingress/ingress-nginx.yaml`
  - **Findings:** ClusterRole 'ingress-nginx' has access to manage (list/watch) resource 'secrets'.
  - **Decision:** Accepted. The NGINX Ingress Controller requires `list` and `watch` permissions on secrets at the cluster scope to monitor TLS certificate changes across namespaces. This is a standard requirement for the component. Finding is suppressed via inline comment `# trivy:ignore:KSV-0041`.

- [x] **A3 — Trivy: ExternalName services for cross-namespace routing (KSV-0108)**
  - **Affected file:** `base/core/external-ingress.yaml`
  - **Findings:** Services 'argocd-server-alias' and 'kubernetes-dashboard-alias' use `externalName`.
  - **Decision:** Accepted. These aliases are used to allow the NGINX Ingress Controller (running in the `shift-festival` namespace) to route traffic to the ArgoCD and Kubernetes Dashboard services in their respective namespaces. Since these aliases point to internal `.svc.cluster.local` addresses and not external internet IPs, the risk associated with CVE-2020-8554 is mitigated. Finding is suppressed via `.trivyignore`.

### Open Bevindingen / Toekomstige Verbeteringen
- [ ] **M1 — NetworkPolicies:** Implement Egress/Ingress policies to restrict inter-pod communication (e.g., only the frontend can talk to the frontend-db).
- [ ] **M2 — Resource Quotas:** Enforce Namespace-level resource limits to prevent noisy neighbor issues.
- [ ] **M3 — Seccomp/AppArmor:** Further harden pods using Seccomp profiles.

---

## 7. Incident Response & Rollback

- **Manual Rollback:** `kubectl rollout undo deployment/<name> -n shift-festival`
- **State Recovery:** Re-apply the last known-good configuration from the `main` branch.
- **Logs:** Centralized logging via Logstash → Elasticsearch → Kibana for forensic analysis.
