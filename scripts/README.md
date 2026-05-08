# Integration Test Suite — Groep 1

Volledig geautomatiseerde testscript voor alle RabbitMQ message flows van het XML/XSD Contract v2.3.

## Vereisten

```bash
pip install pika lxml
```

Python 3.9 of hoger.

---

## Snelstart

```bash
# Standaard run — valideert XML én test live routing op shift-festival
python test_integration.py

# Met consumer-pause zodat messages zichtbaar blijven in de queue
python test_integration.py --pause-consumers
```

Host, AMQP-poort (30000) en management-poort (30001) zijn standaard ingesteld op de productie-VM. Je hoeft niets extra mee te geven.

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
| `--pause-consumers` | *(uit)* | Pauzeer actieve consumers zodat messages zichtbaar worden |
| `--pause-delay` | `0.8` | Seconden wachten na sluiten consumer-connectie (zelden nodig) |
| `--verbose` | *(uit)* | Print de volledige XML payload per flow |
| `--env` | `.env` | Laad environment-variabelen uit een bestand |

---

## Veelgebruikte commando's

### Basis live test (kortste commando)
```bash
python test_integration.py
```

### Met consumer-pause (ziet messages in queue staan)
```bash
python test_integration.py --pause-consumers
```
De script sluit tijdelijk de AMQP-connecties van actieve consumers via de management API. Ze reconnecten automatisch na de test. Dit geeft een `Queued ✓` of `Consumed ✓` bevestiging per flow.

### Alleen XML/XSD validatie, geen netwerk nodig
```bash
python test_integration.py --dry-run
```
Handig om lokaal te controleren of de XML-structuren kloppen zonder toegang tot de cluster.

### Alleen specifieke teams testen
```bash
python test_integration.py --teams crm,kassa
python test_integration.py --teams planning,frontend
python test_integration.py --teams monitoring
```

### Langere timeout (tragere verbinding)
```bash
python test_integration.py --timeout 15
```

### Verbose — print de volledige XML per flow
```bash
python test_integration.py --verbose
```

### Andere host/poort (bv. lokale ontwikkelomgeving)
```bash
python test_integration.py --host localhost --port 5672 --mgmt-port 15672
```

---

## Wat test het script?

### XSD-validatie
Elke message wordt gevalideerd tegen het XSD-schema uit contract v2.3 voordat hij gepubliceerd wordt. Fouten worden meteen gerapporteerd.

### Contract-overtreding checks
Het script verifieert ook dat ongeldige berichten worden **afgewezen**:
- `xmlns` namespace in header (v1.0 overblijfsel)
- `version=1.0` in plaats van `2.0`
- `<age>` veld in plaats van `<date_of_birth>`
- Monetaire bedragen zonder `currency`-attribuut
- Ongeldige `mail_type` enum waarde
- `system_alert` in `<message>` envelope (moet platte `<alert>` root zijn)
- `badge_scanned` met zowel `badge_id` als `identity_uuid` tegelijk (xs:choice)
- `badge_scanned` zonder enige identifier

### Log-matrix
Alle combinaties van 8 teams × 13 acties × 3 niveaus worden gevalideerd (312 XSD-checks):

| Teams | Acties | Niveaus |
|---|---|---|
| crm, kassa, facturatie, planning, mailing, frontend, identity-service, iot_gateway | registration, user, payment, invoice, session, calendar, email, wallet, refund, identity, xml_validation, system_error, badge | info, warning, error |

### Message flows (21 flows, 70 tests)

| Flow | Van → Naar | Type | Methode |
|---|---|---|---|
| 01 | Frontend → CRM | new_registration | publish + peek/consume |
| 02 | CRM → Kassa | new_registration | shadow queue |
| 03 | Kassa → CRM | consumption_order | shadow queue |
| 04 | Kassa → CRM/Facturatie | payment_registered | shadow queue |
| 05 | CRM → Facturatie | invoice_request | publish + peek |
| 06 | CRM → Mailing | send_mailing | publish + peek |
| 07 | Facturatie → Mailing | send_mailing | publish + peek |
| 08 | Facturatie → CRM | invoice_status | publish + peek/consume |
| 09 | Facturatie → CRM | payment_registered | publish + peek/consume |
| 10 | Mailing → CRM | mailing_status | publish + peek/consume |
| 11 | Planning → CRM | session_created | shadow queue |
| 12 | Planning → CRM | session_updated | shadow queue |
| 13 | Frontend → Planning | session_create_request | shadow queue |
| 14 | Frontend → Planning | calendar_invite | shadow queue |
| 15 | Monitoring → Mailing | system_alert | publish + peek |
| 16 | Alle teams → Monitoring | heartbeat (×8 teams) | publish + peek/consume |
| 17 | Frontend → CRM | event_ended | publish + peek/consume |
| 18 | Frontend → Facturatie | event_ended | publish + peek |
| 19 | Alle teams → Monitoring | log (8 teams) | publish + peek/consume |
| 20 | IoT/Kassa → Kassa | badge_scanned (badge + QR variant) | publish + peek/consume |
| 21 | Kassa → CRM | wallet_lease_request (badge + QR variant) | shadow queue |

---

## Uitleg van de testmethodes

### Shadow queue (exchange-flows)
Voor berichten die via een exchange worden gerouteerd (kassa.exchange, planning.exchange, calendar.exchange) maakt het script een tijdelijke exclusieve queue aan, bindt die aan dezelfde exchange + routing key, publiceert het bericht, en voert een directe `basic_get` uit. Dit bewijst dat de routing correct werkt zonder de echte consumer te verstoren.

### Publish + peek (default exchange, geen actieve consumer)
Voor queues zonder actieve consumer (bv. `crm.to.mailing`, `to_mailing`) wordt het bericht gepubliceerd en vervolgens via de management API gecheckt of hij aanwezig is in de queue.

### Publish + consume (default exchange, actieve consumer)
Voor queues met een actieve consumer (bv. `crm.incoming`, `heartbeat`, `logs`) wordt bij `--pause-consumers` de consumer-connectie tijdelijk gesloten via de management API. Zodra bevestigd is dat de consumer weg is, wordt het bericht gepubliceerd en via AMQP `basic_get` opgehaald. Als de consumer te snel reconnect, geldt `Consumed ✓` als bewijs van levering (message is gepubliceerd én verwerkt).

---

## Uitleg van de uitvoer

```
✓  XSD valid    — XML is geldig tegen het contract-schema
✓  Routed ✓     — Message bereikt de juiste exchange/routing key (shadow queue)
✓  Queued ✓     — Message staat zichtbaar in de queue (management API)
✓  Consumed ✓   — Message gepubliceerd en meteen verwerkt door live consumer
✓  Published ✓  — Publish geslaagd (default exchange = levering gegarandeerd)
⚠  Not in queue — Message niet gevonden (consumer te snel of queue leeg)
✗  XSD INVALID  — XML voldoet niet aan het schema
✗  Routing FAIL — Message werd niet gerouteerd via exchange
```

---

## Environment-variabelen

Alle opties kunnen ook via environment-variabelen worden ingesteld:

```
RABBIT_HOST=20.126.113.148
RABBIT_PORT=30000
RABBIT_MGMT_PORT=30001
RABBIT_USER=guest
RABBIT_PASS=guest
RABBIT_VHOST=/
TIMEOUT=5
```

Zet ze in een `.env` bestand in dezelfde map en het script laadt ze automatisch.
