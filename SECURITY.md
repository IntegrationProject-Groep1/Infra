# Security Audit — ShiftFestival Infrastructure

**Date:** 2026-04-22  
**Scope:** Full infrastructure review — `docker-compose.yml`, all scripts in `scripts/`, all Nginx `.conf` files, `logstash/pipeline/logstash.conf`, `.github/workflows/`  
**Reviewer:** Claude Code (automated assisted review)

---

## Summary

| Severity | Count |
|----------|-------|
| HIGH     | 4     |
| MEDIUM   | 7     |
| LOW      | 3     |

---

## HIGH Severity

### H1 — Elasticsearch Exposed on All Interfaces
**File:** `docker-compose.yml`  
**Issue:** Port 9200 is mapped as `"30060:9200"`, which binds to `0.0.0.0` by default. Any host on the VM's network can reach Elasticsearch directly, bypassing Nginx and Cloudflare.  
**Exploit:** `curl http://<vm-ip>:30060/_cat/indices` — even with xpack.security enabled, a brute-force or credential-leak allows full log access.  
**Fix:** Change to `"127.0.0.1:30060:9200"` to restrict to localhost, and enforce all access through the Kibana UI behind its Nginx proxy.

---

### H2 — RabbitMQ Management UI Exposed Without TLS
**File:** `docker-compose.yml`, `rabbitmq.conf`  
**Issue:** Port 15672 (management UI) is exposed as `"30001:15672"` on all interfaces. Traffic between the client and port 30001 is plain HTTP — Cloudflare only terminates TLS at the tunnel level, not on direct port access.  
**Exploit:** An attacker on the same network or with access to a misconfigured Azure NSG can intercept HTTP traffic to `:30001` and capture AMQP credentials in cleartext.  
**Fix:** Bind the port to `"127.0.0.1:30001:15672"` so it is only accessible via Cloudflare tunnel, not direct TCP. Alternatively, disable port binding entirely if access is only via tunnel.

---

### H3 — PostgreSQL Exposed Directly on Network Port
**File:** `docker-compose.yml`  
**Issue:** PostgreSQL binds to port `${EXTERNAL_DB_PORT:-30004}` on all interfaces. There is no authentication gateway or Nginx proxy in front. Direct database access is exposed to the network.  
**Exploit:** An attacker can run `psql -h <vm-ip> -p 30004 -U <user>` and attempt brute-force or use stolen credentials to access all stored data directly.  
**Fix:** Remove the external port mapping entirely (internal Docker network is sufficient for all services that need DB access). If pgAdmin is used for external access, restrict it to a VPN or authenticated proxy.

---

### H4 — Trivy Security Scan Does Not Block CI on Findings
**File:** `.github/workflows/ci.yml`  
**Issue:** `exit-code: 0` is set for the Trivy scan step, meaning HIGH and CRITICAL security misconfigurations are reported as warnings but never fail the pipeline. A misconfigured container can be deployed to production while the scan log shows issues.  
**Exploit:** A developer commits a privileged container or a hardcoded secret in a config — Trivy detects it, logs it, and the deployment continues.  
**Fix:** Change to `exit-code: 1` so that any HIGH or CRITICAL finding blocks the merge.

---

## MEDIUM Severity

### M1 — Missing HTTP Security Headers in All Nginx Configs
**Files:** `dozzle-nginx.conf`, `fossbilling-nginx.conf`, `frontend-nginx.conf`, `kassa-nginx.conf`, `kibana-nginx.conf`  
**Issue:** None of the Nginx reverse proxy configs include standard HTTP security headers. Missing: `X-Content-Type-Options`, `X-Frame-Options`, `Content-Security-Policy`, `Strict-Transport-Security`, `Referrer-Policy`.  
**Exploit:** Users of Drupal, FOSSBilling, and Odoo UIs are exposed to MIME-sniffing attacks and clickjacking. An attacker can embed the Kibana UI in an iframe on a malicious site.  
**Fix:** Add to every `server` block (ideally in a shared snippet):
```nginx
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "SAMEORIGIN" always;
add_header X-XSS-Protection "1; mode=block" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
```

---

### M2 — CRM Service Exposed Without Nginx Proxy
**File:** `docker-compose.yml`  
**Issue:** The CRM receiver service is exposed on port 30040 without an Nginx reverse proxy. The Node.js application handles HTTP directly with no security headers, no rate limiting, and no SSL offloading layer.  
**Exploit:** Any HTTP-based attack (header injection, request smuggling) targets the application directly.  
**Fix:** Add a `crm-proxy` Nginx service (same pattern as other teams) with security headers and the standard reverse proxy config.

---

### M3 — Planning Service Exposed Without Nginx Proxy
**File:** `docker-compose.yml`  
**Issue:** Same issue as M2. Planning service is exposed on port 30050 without a proxy layer.  
**Fix:** Add a `planning-proxy` Nginx service.

---

### M4 — Dozzle Exposes Docker Socket (All Container Logs)
**File:** `docker-compose.yml`  
**Issue:** Dozzle mounts `/var/run/docker.sock:ro`, giving it read access to logs of all containers including those containing secrets in environment output, error traces, or debug logs.  
**Exploit:** If the Dozzle UI is compromised (e.g., XSS in the web UI or weak/absent authentication), an attacker can read logs of all services and extract credentials or sensitive business data.  
**Fix:** Enable Dozzle's built-in authentication (`DOZZLE_USERNAME` / `DOZZLE_PASSWORD` env vars, or use `--no-analytics` with a filter). Restrict which containers Dozzle can access using `DOZZLE_FILTER`.

---

### M5 — GitHub Actions: Trivy Uses `@master` Branch
**File:** `.github/workflows/ci.yml`  
**Issue:** `uses: aquasecurity/trivy-action@master` is pinned to the master branch, not a specific commit SHA. This is a supply-chain risk — the action can change without notice.  
**Exploit:** If Aqua's repository is compromised or the action is silently updated with malicious code, it runs with access to all CI secrets.  
**Fix:** Pin to a specific commit SHA:
```yaml
uses: aquasecurity/trivy-action@d43c1f16c00cfd3978dde6c07f4bbcf9eb6993ca  # v0.16.1
```

---

### M6 — Rollback Script Writes to `.env` Without Atomic File Operations
**File:** `scripts/runtime-rollback.sh`  
**Issue:** The script uses `sed -i` directly on `.env` and then appends lines with `echo >> .env`. These are non-atomic operations. If the process is interrupted mid-write (e.g., OOM kill, SIGKILL), `.env` can be left in a partially written or corrupted state.  
**Exploit:** A partial `.env` write during rollback leaves the stack in an inconsistent state on the next `docker compose up`, potentially starting services with blank or missing credentials.  
**Fix:** Write to a temp file (`mktemp`), then `mv` (atomic rename) to replace `.env`. This guarantees either the old or new `.env` is active, never a partial file.

---

### M7 — psql Migration Command Uses Unquoted Variables
**File:** `docker-compose.yml` (planning-migrate service)  
**Issue:** The migration command passes `${PLANNING_DB_USER}` and `${PLANNING_DB_NAME}` to `psql` without quoting inside the shell command string. If a variable contains spaces or shell metacharacters, it can break the command or alter its behavior.  
**Fix:** Quote all variables in the command:
```yaml
command: >
  sh -c "psql -h planning_db -U \"$$PLANNING_DB_USER\" -d \"$$PLANNING_DB_NAME\" -f /migrations/001_initial.sql"
```

---

## LOW Severity

### L1 — Containers Run Without `no-new-privileges`
**File:** `docker-compose.yml` (all services)  
**Issue:** No service sets `security_opt: ["no-new-privileges:true"]`. Without this, a process inside a container can gain new privileges via setuid binaries.  
**Fix:** Add to all services that don't require privilege escalation:
```yaml
security_opt:
  - "no-new-privileges:true"
```

---

### L2 — Logstash Parses XML Without Size Validation
**File:** `logstash/pipeline/logstash.conf`  
**Issue:** The XML filter processes all heartbeat messages without a size limit. A malicious or buggy heartbeat publisher could send a very large XML payload, causing Logstash memory pressure.  
**Fix:** Add a `length` check filter before the XML filter to drop messages exceeding a reasonable size (e.g., 64 KB).

---

### L3 — pgAdmin Exposed Without Network Restriction
**File:** `docker-compose.yml`  
**Issue:** pgAdmin is exposed on port 30005 with no additional authentication layer beyond its own login. If its credentials are weak or default, it provides full SQL access to all databases.  
**Fix:** Bind the port to `"127.0.0.1:30005:80"` and access only via Cloudflare tunnel or a VPN. Ensure the default admin email and password are changed via `PGADMIN_DEFAULT_EMAIL` and `PGADMIN_DEFAULT_PASSWORD` environment variables.

---

## Checklist for Remediation

- [ ] Bind Elasticsearch to `127.0.0.1` only (`docker-compose.yml`)
- [ ] Bind RabbitMQ management port to `127.0.0.1` only (`docker-compose.yml`)
- [ ] Remove or restrict external PostgreSQL port binding (`docker-compose.yml`)
- [ ] Set `exit-code: 1` in Trivy CI step (`.github/workflows/ci.yml`)
- [ ] Add HTTP security headers to all Nginx configs
- [ ] Add Nginx proxy for CRM service (`docker-compose.yml`)
- [ ] Add Nginx proxy for Planning service (`docker-compose.yml`)
- [ ] Enable Dozzle authentication and container filtering (`docker-compose.yml`)
- [ ] Pin Trivy GitHub Action to a specific commit SHA (`.github/workflows/ci.yml`)
- [ ] Rewrite `.env` updates in rollback script to use atomic `mv` (`scripts/runtime-rollback.sh`)
- [ ] Quote variables in psql migration command (`docker-compose.yml`)
- [ ] Add `no-new-privileges` security option to all services (`docker-compose.yml`)
- [ ] Bind pgAdmin to `127.0.0.1` only (`docker-compose.yml`)
