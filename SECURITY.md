# Security Audit — ShiftFestival Infrastructure

**Date:** 2026-04-22
**Scope:** `docker-compose.yml`, `scripts/`, alle Nginx `.conf` bestanden, `logstash/`, `.github/workflows/`
**Sources:** Internal review (Claude Code) + Gemini analysis

---

## Status overview

- [x] H1 — Elasticsearch exposed on all interfaces
- [x] H2 — RabbitMQ management UI exposed without network restriction
- [x] H3 — PostgreSQL exposed directly on network port
- [x] H4 — Trivy security scan did not block CI on findings
- [x] H5 — Missing HTTP security headers in all Nginx configs
- [x] M1 — Docker socket fully exposed to rollback-monitor
- [x] M2 — pgAdmin exposed on all interfaces
- [x] M3 — Dozzle has no authentication
- [x] M4 — CRM and Planning ports not restricted to localhost

---

## Resolved

### H1 — Elasticsearch exposed on all interfaces
**File:** `docker-compose.yml`
**Was:** Port 9200 bound to `0.0.0.0:30060` — anyone on the network could query Elasticsearch directly, bypassing Kibana.
**Fix:** Changed to `127.0.0.1:30060:9200`. Only accessible via localhost; external access goes through the Kibana Nginx proxy only.

---

### H2 — RabbitMQ management UI exposed without network restriction
**File:** `docker-compose.yml`
**Was:** Port 15672 bound to `0.0.0.0:30001` over plain HTTP — credentials could be intercepted on the same network segment.
**Fix:** Changed to `127.0.0.1:30001:15672`. Only reachable via Cloudflare tunnel.

---

### H3 — PostgreSQL exposed directly on network port
**File:** `docker-compose.yml`
**Was:** Port 5432 exposed as `30004:5432` on all interfaces — direct brute-force or credential attacks possible.
**Fix:** External port mapping removed entirely. All services connect via `shift_net` using `postgredb:5432`. For direct access: `ssh -L 5432:localhost:5432 ehbstudent@<vm>`.

---

### H4 — Trivy security scan did not block CI on findings
**File:** `.github/workflows/ci.yml`
**Was:** `exit-code: 0` — HIGH/CRITICAL misconfigurations were logged but never failed the pipeline.
**Fix:** Changed to `exit-code: 1`. HIGH/CRITICAL Trivy findings now block the pipeline.

---

### H5 — Missing HTTP security headers in all Nginx configs
**Files:** `dozzle-nginx.conf`, `fossbilling-nginx.conf`, `frontend-nginx.conf`, `kassa-nginx.conf`, `kibana-nginx.conf`
**Was:** No security headers set — clickjacking, MIME-sniffing, and information disclosure possible.
**Fix:** Added to all server blocks:
```nginx
server_tokens off;
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "SAMEORIGIN" always;
add_header X-XSS-Protection "1; mode=block" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
```

---

### M1 — Docker socket fully exposed to rollback-monitor
**File:** `docker-compose.yml`
**Was:** `/var/run/docker.sock` mounted directly into rollback-monitor — a compromised script would have full root-level control over the Docker daemon and the host.
**Fix:** Added `docker-proxy` service (`tecnativa/docker-socket-proxy:0.2.2`) on isolated `docker-proxy-net`. rollback-monitor now connects via `DOCKER_HOST=tcp://docker-proxy:2375`. Blocked: BUILD, EXEC, SWARM, SECRETS, CONFIGS, TASKS, SERVICES, DISTRIBUTION, AUTH.

---

### M2 — pgAdmin exposed on all interfaces
**File:** `docker-compose.yml`
**Was:** `30005:80` on all interfaces.
**Fix:** Changed to `127.0.0.1:30005:80`. Only accessible via Cloudflare tunnel.

---

### M3 — Dozzle has no authentication
**File:** `docker-compose.yml`, `dozzle-users.yml`
**Was:** Dozzle accessible without login — anyone via the tunnel could read all container logs.
**Fix:** Added `DOZZLE_AUTH_PROVIDER=simple` and mounted `dozzle-users.yml` at `/data/users.yml`.

`dozzle-users.yml` is NOT committed to git (in `.gitignore`, same as `.env`). The deploy pipeline only copies git-tracked files, so the file persists on the VM between deploys once placed there manually.

One-time setup on the VM:
```bash
sudo docker run --rm amir20/dozzle:v8 generate admin --password yourpassword
# Copy the output to /opt/shiftfestival/dozzle-users.yml
# Then: docker compose restart dozzle
```
See `dozzle-users.yml.example` for the expected file format.

---

### M4 — CRM and Planning ports not restricted to localhost
**File:** `docker-compose.yml`
**Was:** `crm-receiver` on `0.0.0.0:30040` and `planning-service` on `0.0.0.0:30050`.
**Fix:** Changed to `127.0.0.1:30040:3000` and `127.0.0.1:30050:30050`. Internal container communication via `shift_net` is unaffected.
