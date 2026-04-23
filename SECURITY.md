# Security Audit — ShiftFestival Infrastructure

**Date:** 2026-04-23  
**Scope:** `docker-compose.yml`, `scripts/`, Nginx `.conf` bestanden, `logstash/`, `pipelines/`  
**Context:** huidige productie draait op één Azure VM met Docker Compose, Cloudflare Tunnel voor een deel van de ingress, en geplande migratie naar Kubernetes.

---

## Doel van dit document

Dit document probeert niet te doen alsof de huidige Compose-stack al een eindtoestand is.  
Het doel is:

1. Vastleggen wat vandaag echt opgelost is.
2. Duidelijk benoemen welke risico's bewust tijdelijk geaccepteerd zijn tot Cloudflare/Kubernetes.
3. Open laten wat nog steeds een echt risico is en nu nog oplosbaar is zonder de stack te breken.

---

## Huidige netwerkrealiteit

### Via Cloudflare tunnel

Alleen deze publieke entrypoints lopen via de Cloudflare tunnel (`desiderius.me`):

| URL | Docker service | Host-poort |
|-----|---------------|------------|
| `https://desiderius.me` | `frontend-proxy` | 30020 |
| `https://facturatie.desiderius.me` | `fossbilling-proxy` | 30010 |
| `https://kassa.desiderius.me` | `kassa-proxy` | 30030 |
| `https://kibana.desiderius.me` | `kibana-proxy` | 30061 |
| `https://rabbitmq.desiderius.me` | `rabbitmq` management UI | 30001 |
| `https://dozzle.desiderius.me` | `dozzle-proxy` | 30002 |

### Niet via Cloudflare tunnel

De volgende services zijn vandaag nog direct via VM-IP bereikbaar als Azure NSG dat toelaat:

- `pgadmin` op 30005
- `planning-pgadmin` op 30052
- `crm-receiver` op 30040
- `planning-service` op 30050

Dit betekent ook dat sommige `0.0.0.0` bindings vandaag functioneel nodig zijn.  
Pas wanneer Cloudflare ingress of Kubernetes ingress dit overneemt, kunnen die veilig naar `127.0.0.1` of intern-only.

---

## Samenvatting

### Opgelost

- [x] H1 — Elasticsearch niet langer extern blootgesteld
- [x] H2 — RabbitMQ management niet langer publiek op alle interfaces
- [x] H3 — PostgreSQL host-port verwijderd
- [x] H4 — CI security gate toegevoegd
- [x] H5 — Security headers toegevoegd op Nginx proxies
- [x] M3 — Dozzle authenticatie toegevoegd
- [x] M6 — `planning-migrate` en `PGPASSWORD` verwijderd
- [x] H6 — GHCR runtime credentials verwijderd uit `rollback-monitor` en `watchtower`

### Accepted Risk Tot Cloudflare/Kubernetes

- [~] M2 — pgAdmin en planning-pgAdmin luisteren nog op alle interfaces
- [~] M4 — CRM en Planning luisteren nog direct op VM-IP
- [~] M5 — Cloudflare-proxies luisteren nog op alle interfaces
- [~] V1 — Team images op floating tags zoals `latest`

### Nog Open En Nu Oplosbaar

- [ ] M1 — Docker socket nog direct gemount in `rollback-monitor` en `watchtower`
- [ ] M7 — Geen resource limits voor teamcontainers

---

## Opgeloste Bevindingen

### H1 — Elasticsearch exposed on all interfaces
**File:** `docker-compose.yml`

**Status:** Opgelost.  
Poort `9200` is gebonden aan `127.0.0.1:30060:9200` in plaats van aan alle interfaces. Elasticsearch is extern niet nodig; Kibana en Logstash verbinden intern via Docker networking.

### H2 — RabbitMQ management UI exposed without network restriction
**File:** `docker-compose.yml`

**Status:** Opgelost.  
Poort `15672` is gebonden aan `127.0.0.1:30001:15672`. De management UI loopt via Cloudflare tunnel in plaats van direct via VM-IP.

### H3 — PostgreSQL exposed directly on network port
**File:** `docker-compose.yml`

**Status:** Opgelost.  
De host-port voor PostgreSQL is verwijderd. Applicaties verbinden intern via `postgredb:5432`.

### H4 — CI security gate toegevoegd
**File:** `pipelines/ci.yml`

**Status:** Opgelost.  
De CI pipeline valideert nu `docker-compose.yml` en draait Trivy in zowel `config` als `fs` modus. HIGH/CRITICAL findings laten de pipeline falen.

### H5 — Missing HTTP security headers in Nginx proxies
**Files:** `dozzle-nginx.conf`, `fossbilling-nginx.conf`, `frontend-nginx.conf`, `kassa-nginx.conf`, `kibana-nginx.conf`

**Status:** Opgelost.  
De reverse proxies zetten nu basis security headers zoals `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` en `Strict-Transport-Security`.

### M3 — Dozzle has no authentication
**File:** `docker-compose.yml`

**Status:** Opgelost.  
Dozzle gebruikt nu `DOZZLE_AUTH_PROVIDER=simple` met een externe `users.yml`.

### M6 — planning-migrate gebruikte PGPASSWORD als environment variable
**File:** `docker-compose.yml`

**Status:** Opgelost.  
De eenmalige `planning-migrate` service is verwijderd uit de Compose stack. Daardoor bestaat ook de `PGPASSWORD` exposure in een runtime container niet meer.

### H6 — GHCR runtime credentials mounted in containers
**File:** `docker-compose.yml`

**Status:** Opgelost.  
De Docker `config.json` mounts zijn verwijderd uit zowel `rollback-monitor` als `watchtower`. Omdat de GHCR images nu publiek zijn, zijn runtime registry credentials niet meer nodig om te pullen.

---

## Accepted Risk Tot Cloudflare/Kubernetes

Deze punten zijn geen “vergeten issues”, maar bewuste tijdelijke afwijkingen zolang de stack nog Compose-based is en niet volledig achter Cloudflare of Kubernetes ingress zit.

### M2 — pgAdmin en planning-pgAdmin exposed on all interfaces
**File:** `docker-compose.yml`

**Waarom nog niet opgelost:**  
Deze services zitten vandaag niet achter Cloudflare tunnel. Een binding naar `127.0.0.1` zou de huidige externe toegang breken.

**Risico:**  
Directe bereikbaarheid via VM-IP als Azure NSG dit toelaat.

**Tijdelijke compenserende controle:**  
Beperk in Azure NSG poorten `30005` en `30052` tot vertrouwde IP-adressen zodra praktisch mogelijk.

**Toekomstige oplossing:**  
Zet deze via Cloudflare ingress of Kubernetes ingress en verwijder directe host exposure.

### M4 — CRM en Planning rechtstreeks bereikbaar via VM-IP
**File:** `docker-compose.yml`

**Waarom nog niet opgelost:**  
`crm-receiver` en `planning-service` zitten nog niet in de tunnel en moeten vandaag direct bereikbaar blijven.

**Risico:**  
Verkeer omzeilt Cloudflare WAF/DDoS-bescherming.

**Tijdelijke compenserende controle:**  
Azure NSG alleen openzetten voor strikt nodige bronnen en webhooks.

**Toekomstige oplossing:**  
Ingress via Cloudflare of Kubernetes en daarna host binding beperken.

### M5 — Cloudflare-backed proxies luisteren nog op alle interfaces
**File:** `docker-compose.yml`

**Waarom nog niet opgelost:**  
Dit is pas veilig om aan te passen nadat de Cloudflare tunnelconfig expliciet `localhost:<poort>` als origin gebruikt en getest is.

**Risico:**  
Direct verkeer naar VM-IP kan Cloudflare protections omzeilen.

**Toekomstige oplossing:**  
Bindings naar `127.0.0.1` zodra Cloudflare ingress bevestigd en getest is.

### V1 — Floating tags zoals `latest`
**Files:** `docker-compose.yml`, team images

**Waarom nog niet opgelost:**  
Voor minstens Odoo en delen van de stack is eerder gebleken dat blind pinnen of upgraden incompatibel kan zijn met bestaande data of addons.  
Watchtower ondersteunt geen policy zoals “alleen patch updates, geen majors” op basis van labels. Een floating tag blijft dus een floating tag.

**Risico:**  
Minder voorspelbare upgrades en zwakkere reproduceerbaarheid.

**Tijdelijke mitigatie:**  
Rollback monitor + sticky rollback beperkt de impact, maar lost het fundamentele versiebeheerprobleem niet op.

**Toekomstige oplossing:**  
Per service testen en pinnen op expliciete versies of digests, of via een gecontroleerde Kubernetes releaseflow.

---

## Open Bevindingen Die Nog Actie Vragen

### M1 — Docker socket fully exposed to rollback-monitor and watchtower
**File:** `docker-compose.yml`

**Risico:**  
Een compromise van een container met `/var/run/docker.sock` kan effectief leiden tot host-level controle op de VM.

**Waarom nog open:**  
De stack gebruikt nog steeds de raw Docker socket voor monitoring en updates.

**Aanbevolen fix:**  
Vervang dit op termijn door een Docker socket proxy met minimaal benodigde endpoints, of elimineer dit ontwerp volledig in Kubernetes.

### M7 — Geen resource limits op teamcontainers
**File:** `docker-compose.yml`

**Risico:**  
Een runaway container kan CPU/RAM van de hele VM opeten en zo andere teams raken.

**Waarom nog open:**  
Er zijn nog geen veilige limieten bepaald per service.

**Aanbevolen fix:**  
Voer gemeten limieten in per service in zodra usage bekend is. In Kubernetes wordt dit normaal `requests` en `limits`.

---

## Niet Als Bevinding Opgenomen, Maar Wel Relevante Opmerking

### Kubernetes zal een deel van dit document verouderen

De geplande migratie naar Kubernetes verandert een aantal fundamentele risico’s:

- Host-port exposure kan vervangen worden door Ingress/Service policies.
- Secrets kunnen weg uit compose/env-patterns en naar Kubernetes Secrets.
- Resource limits worden standaard explicieter beheerd.
- Watchtower en directe Docker socket toegang verdwijnen normaal uit het ontwerp.

Dat betekent niet dat de huidige risico’s genegeerd mogen worden, maar wel dat een deel ervan bewust tijdelijk is en niet zinvol volledig dichtgetimmerd hoeft te worden in een stack die binnenkort vervangen wordt.

---

## Conclusie

De grootste direct oplosbare credential-issue is weggehaald: runtime GHCR auth is verwijderd.  
De grootste resterende technische risico’s vandaag zijn:

- directe Docker socket toegang
- ontbreken van echte CI security enforcement
- gebrek aan resource limits

De netwerkexposure rond pgAdmin, CRM, Planning en Cloudflare-proxies is reëel, maar hangt nu samen met de huidige Compose-topologie en geplande overgang naar Cloudflare/Kubernetes. Die punten zijn daarom bewust als tijdelijke accepted risk gemarkeerd, niet als “vergeten fix”.
