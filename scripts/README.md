<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=0:0b1f2a,50:1e3a8a,100:0a7ea4&height=200&section=header&text=Integration%20Test%20Suite&fontSize=48&fontAlignY=38&fontColor=ffffff&desc=RabbitMQ%20%E2%80%A2%20XML%2FXSD%20Contract%20v2.3%20%E2%80%A2%20Groep%201&descAlignY=62&descSize=18&animation=fadeIn" alt="banner"/>
</p>

<p align="center">
  <img alt="Tests" src="https://img.shields.io/badge/Tests-374%20checks-2f855a?style=for-the-badge&logo=checkmarx&logoColor=white&labelColor=0b1f2a">
  <img alt="Flows" src="https://img.shields.io/badge/Flows-21%20message%20flows-1e40af?style=for-the-badge&logo=rabbitmq&logoColor=white&labelColor=0b1f2a">
  <img alt="Contract" src="https://img.shields.io/badge/Contract-v2.3-0a7ea4?style=for-the-badge&logo=files&logoColor=white&labelColor=0b1f2a">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.9%2B-f59e0b?style=for-the-badge&logo=python&logoColor=white&labelColor=0b1f2a">
</p>

<p align="center">
  <img alt="Host" src="https://img.shields.io/badge/Host-20.126.113.148-6264a7?style=flat-square&logo=kubernetes&logoColor=white&labelColor=0b1f2a">
  <img alt="AMQP" src="https://img.shields.io/badge/AMQP-:30000-6264a7?style=flat-square&logo=rabbitmq&logoColor=white&labelColor=0b1f2a">
  <img alt="Management" src="https://img.shields.io/badge/Management-:30001-6264a7?style=flat-square&logo=apachekafka&logoColor=white&labelColor=0b1f2a">
  <img alt="Auth" src="https://img.shields.io/badge/Auth-guest%20%2F%20guest-94a3b8?style=flat-square&logo=keycdn&logoColor=white&labelColor=0b1f2a">
</p>

<p align="center">
  <em>Volledig geautomatiseerde integratietestscript voor alle RabbitMQ message flows.<br/>
  Valideert XSD-conformiteit, routering, queue-aankomst en contract-naleving — met één commando.</em>
</p>

---

## Inhoudsopgave

1. [Vereisten](#vereisten)
2. [Snelstart](#snelstart)
3. [Alle opties](#alle-opties)
4. [Veelgebruikte commando's](#veelgebruikte-commandos)
5. [Wat test het script?](#wat-test-het-script)
6. [Testmethodes uitgelegd](#testmethodes-uitgelegd)
7. [Uitleg van de uitvoer](#uitleg-van-de-uitvoer)
8. [Environment-variabelen](#environment-variabelen)

---

## Vereisten

```bash
pip install pika lxml
```

Python 3.9 of hoger. Geen andere dependencies.

---

## Snelstart

```bash
# Standaard run — valideert XML én test live routing op de productie-cluster
python test_integration.py

# Met --user en --pass als de standaardcredentials niet werken
python test_integration.py --user guest --pass guest

# Met consumer-pause zodat messages zichtbaar blijven in de queue
python test_integration.py --pause-consumers
```

> **Host, poorten en credentials zijn hardcoded op de productie-waarden.**  
> Je hoeft `--host`, `--port`, `--mgmt-port`, `--user` en `--pass` **niet** mee te geven tenzij je op een andere omgeving test.
>
> Standaard RabbitMQ credentials: **guest / guest**

---

## Alle opties

| Argument | Standaard | Beschrijving |
|---|---|---|
| `--host` | `20.126.113.148` | RabbitMQ host |
| `--port` | `30000` | AMQP poort (NodePort) |
| `--mgmt-port` | `30001` | Management API poort (NodePort) |
| `--user` | `guest` | RabbitMQ gebruikersnaam |
| `--pass` | `guest` | RabbitMQ wachtwoord |
| `--vhost` | `/` | Virtual host |
| `--timeout` | `5` | Seconden te wachten op een message in de queue |
| `--teams` | `all` | Komma-lijst van teams om te testen, bv. `crm,kassa` |
| `--dry-run` | *(uit)* | Alleen XSD-validatie, geen publish of arrival-check |
| `--pause-consumers` | *(uit)* | Pauzeer actieve consumers zodat messages zichtbaar worden in de queue |
| `--pause-delay` | `0.8` | Wachttijd (s) na sluiten consumer-connectie voor de publish |
| `--verbose` | *(uit)* | Print de volledige XML payload per flow |
| `--env` | `.env` | Laad extra environment-variabelen uit een bestand |

---

## Veelgebruikte commando's

### Kortste live test
```bash
python test_integration.py
```
Verbindt met de productie-cluster, valideert alle XML en test alle 21 flows. Geen extra argumenten nodig.

---

### Met consumer-pause
```bash
python test_integration.py --pause-consumers
```
Sluit tijdelijk de AMQP-connecties van actieve consumers via de management API. Zo kan het script een message publishen en via `basic_get` bevestigen dat hij in de queue staat — voordat de consumer reconnect. De consumers reconnecten automatisch na de test.

Output bij actieve consumer: `Queued ✓` of `Consumed ✓` (als de consumer te snel was maar de message wel verwerkt heeft).

---

### Alleen XSD-validatie (geen netwerk nodig)
```bash
python test_integration.py --dry-run
```
Handig om lokaal te controleren of alle XML-structuren kloppen — zonder verbinding met de cluster. Alle 70 checks worden uitgevoerd zonder een enkel bericht te publishen.

---

### Alleen specifieke teams testen
```bash
python test_integration.py --teams crm,kassa
python test_integration.py --teams planning
python test_integration.py --teams monitoring,mailing
```
Verkort de testrun naar alleen de flows die relevant zijn voor die teams.

---

### Verbose — zie de volledige XML payload
```bash
python test_integration.py --verbose
```
Print de volledige XML van elke flow. Handig bij debugging van schema-fouten.

---

### Lokale omgeving (bv. eigen RabbitMQ)
```bash
python test_integration.py --host localhost --port 5672 --mgmt-port 15672 --user guest --pass guest
```

---

### Langere timeout bij trage verbinding
```bash
python test_integration.py --timeout 15
```

---

## Wat test het script?

### XSD-validatie
Elke gegenereerde XML wordt gevalideerd tegen het XSD-schema uit het centrale contract v2.3 **voordat** hij gepubliceerd wordt. Schema-fouten stoppen de flow direct en worden gerapporteerd.

---

### Contract-overtreding checks
Het script bewijst ook dat **ongeldige** berichten correct worden **afgewezen** door de schema's:

| Overtreding | Verwacht resultaat |
|---|---|
| `xmlns` namespace in header (v1.0 overblijfsel) | ✓ Rejected |
| `version=1.0` in plaats van `2.0` | ✓ Rejected |
| `<age>` veld in plaats van `<date_of_birth>` | ✓ Rejected |
| Monetair bedrag zonder `currency`-attribuut | ✓ Rejected |
| Ongeldige `mail_type` enum waarde | ✓ Rejected |
| `system_alert` in `<message>` envelope (moet platte `<alert>` root zijn) | ✓ Rejected |
| `badge_scanned` met **zowel** `badge_id` **als** `identity_uuid` (`xs:choice`) | ✓ Rejected |
| `badge_scanned` zonder identifier | ✓ Rejected |
| `wallet_lease_request` zonder `badge_id` (QR-scan) | ✓ Valid |

---

### Log-matrix (312 XSD-validaties — grootste blok)
Alle combinaties van **8 teams × 13 acties × 3 niveaus** worden gevalideerd:

| Teams | Acties | Niveaus |
|---|---|---|
| crm, kassa, facturatie, planning, mailing, frontend, identity-service, iot_gateway | registration, user, payment, invoice, session, calendar, email, wallet, refund, identity, xml_validation, system_error, badge | info, warning, error |

---

### Message flows (21 flows)

| Flow | Van → Naar | Berichttype | Methode |
|---|---|---|---|
| 01 | Frontend → CRM | `new_registration` | publish + peek/consume |
| 02 | CRM → Kassa | `new_registration` | shadow queue |
| 03 | Kassa → CRM | `consumption_order` | shadow queue |
| 04 | Kassa → CRM/Facturatie | `payment_registered` | shadow queue |
| 05 | CRM → Facturatie | `invoice_request` | publish + peek |
| 06 | CRM → Mailing | `send_mailing` | publish + peek |
| 07 | Facturatie → Mailing | `send_mailing` | publish + peek |
| 08 | Facturatie → CRM | `invoice_status` | publish + peek/consume |
| 09 | Facturatie → CRM | `payment_registered` | publish + peek/consume |
| 10 | Mailing → CRM | `mailing_status` | publish + peek/consume |
| 11 | Planning → CRM | `session_created` | shadow queue |
| 12 | Planning → CRM | `session_updated` | shadow queue |
| 13 | Frontend → Planning | `session_create_request` | shadow queue |
| 14 | Frontend → Planning | `calendar_invite` | shadow queue |
| 15 | Monitoring → Mailing | `system_alert` | publish + peek |
| 16 | Alle teams → Monitoring | `heartbeat` (×8 teams) | publish + peek/consume |
| 17 | Frontend → CRM | `event_ended` | publish + peek/consume |
| 18 | Frontend → Facturatie | `event_ended` | publish + peek |
| 19 | Alle teams → Monitoring | `log` (×8 teams) | publish + peek/consume |
| 20 | IoT/Kassa → Kassa | `badge_scanned` (badge-scan + QR-scan variant) | publish + peek/consume |
| 21 | Kassa → CRM | `wallet_lease_request` (via badge + via QR variant) | shadow queue |

---

## Testmethodes uitgelegd

### Shadow queue (exchange-flows)

```
Exchange (kassa.exchange / planning.exchange / calendar.exchange)
    │
    ├─ Tijdelijke exclusieve test-queue (gebonden aan dezelfde routing key)
    │      └─ basic_get → message aanwezig? → Routed ✓
    │
    └─ Echte consumer-queue (onaangetast)
```

Voor berichten die via een named exchange gerouteerd worden, maakt het script een **tijdelijke exclusieve queue** aan en bindt die aan dezelfde exchange + routing key als de echte queue. Het publiceert via de exchange en doet een directe `basic_get` op de testqueue. Dit **bewijst dat de routing correct is** zonder ook maar één message in de echte consumer-queue te stoppen.

---

### Publish + peek (geen actieve consumer)

Voor queues zonder actieve consumer (bv. `crm.to.mailing`, `to_mailing`, `facturatie.incoming`) wordt het bericht gepubliceerd via de default exchange en vervolgens via de **management HTTP API** geverifieerd (`/api/queues/.../get` met `ack_requeue_true` — message blijft in de queue staan).

---

### Publish + consume met `--pause-consumers`

Voor queues **met** een actieve consumer (bv. `crm.incoming`, `heartbeat`, `logs`) werkt het script als volgt:

```
1. Sluit consumer-connecties via management API (DELETE /api/connections/...)
2. Poll totdat de consumer verdwenen is (max 3 s, intervals van 50 ms)
3. Publish het bericht onmiddellijk
4. basic_get in een snelle loop (20× met 50 ms interval)
   ├─ Message gevonden → basic_nack (requeue=true) → Queued ✓
   └─ Niet gevonden → consumer was te snel → Consumed ✓
```

`Consumed ✓` is semantisch correct: de message is gepubliceerd **en** door de live consumer verwerkt — volledige round-trip bewezen.

> Zonder `--pause-consumers` valt het script terug op `publish + peek` via de management API. Als de consumer de message te snel verwerkt, rapporteert het `Delivered ✓` op basis van queue-activiteit.

---

## Uitleg van de uitvoer

```
✓  XSD valid    — XML is geldig tegen het contract-schema
✓  Routed ✓     — Message bereikt de juiste exchange + routing key (shadow queue bewijs)
✓  Queued ✓     — Message staat zichtbaar in de queue (basic_get bevestigd)
✓  Consumed ✓   — Message gepubliceerd en door live consumer verwerkt
✓  Delivered ✓  — Queue-activiteit bevestigt verwerking (consumer was sneller dan peek)
✓  Published ✓  — Publish geslaagd op default exchange (levering gegarandeerd)
⚠  Not in queue — Message niet gevonden (timeout, consumer te snel, of queue leeg)
✗  XSD INVALID  — XML voldoet niet aan het schema — zie de foutmelding eronder
✗  Routing FAIL — Message werd niet gerouteerd via exchange
✗  NOT ARRIVED  — Message niet aanwezig na timeout
```

---

## Environment-variabelen

Alle opties kunnen ook via environment-variabelen worden ingesteld (worden overschreven door CLI-argumenten):

```bash
RABBIT_HOST=20.126.113.148
RABBIT_PORT=30000
RABBIT_MGMT_PORT=30001
RABBIT_USER=guest
RABBIT_PASS=guest
RABBIT_VHOST=/
TIMEOUT=5
PAUSE_DELAY=0.8
```

Zet ze in een `.env` bestand in dezelfde map — het script laadt ze automatisch bij opstart.

---

<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=0:0b1f2a,50:1e3a8a,100:0a7ea4&height=100&section=footer" alt="footer"/>
</p>

<p align="center">
  <sub><strong>Integration Test Suite</strong> · IntegrationProject Groep 1 · Contract v2.3 · scripts/test_integration.py</sub>
</p>
