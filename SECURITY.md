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

- **Secrets:** Credentials are managed as Kubernetes Secrets, generated via Kustomize `secretGenerator` from a local (non-committed) `.env` file.
- **Config:** Application settings are managed via ConfigMaps.
- **Zero-Secret Commits:** Git history is protected. No secrets are stored in the repository. CI pipelines run Gitleaks to enforce this.

---

## 5. Deployment & Release Security

- **Image Updates (Keel):** Automated image updates are managed by Keel, ensuring services run the latest patched versions from GHCR.
- **CI Security Gate:**
  - `yamllint`: Ensures manifest structural integrity.
  - `Trivy`: Scans Kubernetes manifests for security misconfigurations.
  - `Gitleaks`: Prevents secret leakage in Git history.
- **Deployment Strategy:** Database workloads use the `Recreate` strategy to ensure stable volume transitions and prevent "Multi-Attach" errors.

---

## 6. Audit & Findings

### Opgeloste Bevindingen (K8s Migration)
- [x] **H7 — Root InitContainers:** Root-level `fix-permissions` containers have been removed. Permissions are handled via `fsGroup`.
- [x] **H8 — Database Conflicts:** `Recreate` strategy prevents concurrent volume access errors.
- [x] **H9 — Hardcoded Passwords:** All database passwords moved to K8s Secrets.

### Open Bevindingen / Toekomstige Verbeteringen
- [ ] **M1 — NetworkPolicies:** Implement Egress/Ingress policies to restrict inter-pod communication (e.g., only the frontend can talk to the frontend-db).
- [ ] **M2 — Resource Quotas:** Enforce Namespace-level resource limits to prevent noisy neighbor issues.
- [ ] **M3 — Seccomp/AppArmor:** Further harden pods using Seccomp profiles.

---

## 7. Incident Response & Rollback

- **Manual Rollback:** `kubectl rollout undo deployment/<name> -n shift-festival`
- **State Recovery:** Re-apply the last known-good configuration from the `main` branch.
- **Logs:** Centralized logging via Logstash → Elasticsearch → Kibana for forensic analysis.
