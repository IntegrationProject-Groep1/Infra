# Team Infra - Integration Project Groep 1

![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?style=for-the-badge&logo=docker&logoColor=white)
![GitHub Actions](https://img.shields.io/badge/github%20actions-%232671E5.svg?style=for-the-badge&logo=githubactions&logoColor=white)
![Ubuntu](https://img.shields.io/badge/Ubuntu-E95420?style=for-the-badge&logo=ubuntu&logoColor=white)
![Nginx](https://img.shields.io/badge/nginx-%23009639.svg?style=for-the-badge&logo=nginx&logoColor=white)
![RabbitMQ](https://img.shields.io/badge/Rabbitmq-FF6600?style=for-the-badge&logo=rabbitmq&logoColor=white)

Welkom in de centrale repository van Team Infrastructuur. Deze repository bevat de centrale configuratie voor de productie-VM en de universele deployment flows voor alle teams.

## Centrale Dashboards

* **Log Viewer (Dozzle):** [https://integrationproject-2526s2-dag01.westeurope.cloudapp.azure.com:30002](https://integrationproject-2526s2-dag01.westeurope.cloudapp.azure.com:30002)
* **RabbitMQ Management:** [https://integrationproject-2526s2-dag01.westeurope.cloudapp.azure.com:30001](https://integrationproject-2526s2-dag01.westeurope.cloudapp.azure.com:30001)

---

## Wat zit er in deze repository?

* **`docker-compose.yml`**: De master orchestratie file die alle team-containers aanstuurt.
* **`dozzle-nginx.conf`**: Configuratie voor de centrale log-viewer via HTTPS.
* **`rabbitmq.conf`**: Centrale broker instellingen inclusief SSL-beheer.
* **`pipelines/deploy.yml + ci.yml`**: De universele CI/CD pipeline template voor alle teams.

---

## De Deployment Flow

1.  **Push & Tag**: Een team pusht hun code en maakt een GitHub Release (Tag) aan.
2.  **Build**: Onze `deploy.yml` pipeline bouwt een image en pusht deze naar de GHCR (`ghcr.io/integrationproject-groep1/[servicenaam]`).
3.  **Productie**: De VM detecteert nieuwe versies via **Watchtower** en herstart de container automatisch.

### Hoe maak je een Tagged Release?
Om de pipeline te triggeren, maak je een nieuwe Tag/Release aan in GitHub:

![Demo: Hoe maak je een Tagged Release](assets/tag-release.gif)

---

## Instructies voor Development Teams

Willen jullie je applicatie live zetten op de VM? Zorg dan dat jullie repository aan de volgende eisen voldoet:

### 1. RabbitMQ Naming Convention (Verplicht)
Alle teams maken gebruik van de gedeelde **'/'** (default) Virtual Host. Om conflicten te voorkomen hanteren we een strikte naamgeving:

* **Prefix**: Gebruik altijd je teamnaam als prefix voor elke queue of exchange.
* **Voorbeelden**: `kassa.orders`, `crm.customers`.
* **Uitzondering**: De `heartbeat` queue voor Team Monitoring is de enige gedeelde queue zonder team-prefix.
* **Host**: Gebruik `rabbitmq_broker` als hostname binnen het Docker netwerk.

### 2. Repository Eisen
- [ ] **Dockerfile**: Plaats een werkende file in de root. Test lokaal of deze succesvol bouwt!
- [ ] **Poort (`EXPOSE`)**: Vermeld duidelijk op welke interne poort jullie app draait. Wij hebben dit nodig voor de routing.
- [ ] **.env.example**: Zorg voor een duidelijke file met alle benodigde variabelen, maar **zonder** echte wachtwoorden.
- [ ] **.gitignore & .dockerignore**: Zorg dat gevoelige bestanden (`.env`, `node_modules`, `.git`) worden genegeerd.
- [ ] **Deploy Pipeline**: Kopieer onze `deploy.yml` naar jullie `.github/workflows/` map.

---

## Poortallocatie (Service Inventory)

Elk team heeft een specifiek blok aan poorten toegewezen gekregen. Gebruik enkel poorten binnen jouw toegewezen reeks:

| Team / Service | Poortreeks |
| :--- | :--- |
| **Team Infra** | 30000 - 30009 |
| **Team Facturatie** | 30010 - 30019 |
| **Team Frontend** | 30020 - 30029 |
| **Team Kassa** | 30030 - 30039 |
| **Team CRM** | 30040 - 30049 |
| **Team Planning** | 30050 - 30059 |
| **Team Monitoring** | 30060 - 30069 |
| **Buffer** | 30070 - 30100 |
[Port Allocation](https://ehb-my.sharepoint.com/:x:/g/personal/nguyen_dang_student_ehb_be/IQAQcHP-Zmq5SYh-HKAu5JOtAbvbD1Bntkzn1AS5BYP1GRc)

---

## Beheer & Onderhoud
Wijzigingen aan de centrale infrastructuur worden eerst gepusht naar deze repository. Wijzig nooit handmatig instellingen op de VM zonder overleg met Team Infra.
