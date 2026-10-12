<div align="center">

# 🏥 OPBG Telegram Booking Bot

**Prenotazioni, monitoraggio delle disponibilità e appuntamenti del portale
dell'Ospedale Pediatrico Bambino Gesù, direttamente da Telegram.**

[![CI](https://github.com/LucaTomei/opbg-telegram-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/LucaTomei/opbg-telegram-bot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.13](https://img.shields.io/badge/python-3.13-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![aiogram 3](https://img.shields.io/badge/aiogram-3-26A5E4?logo=telegram&logoColor=white)](https://docs.aiogram.dev/)
[![MySQL 8.4](https://img.shields.io/badge/MySQL-8.4_LTS-4479A1?logo=mysql&logoColor=white)](compose.yaml)
[![Docker image](https://img.shields.io/badge/ghcr.io-opbg--telegram--bot-2496ED?logo=docker&logoColor=white)](https://github.com/LucaTomei/opbg-telegram-bot/pkgs/container/opbg-telegram-bot)

[Cosa fa](#-cosa-fa) ·
[Pubblicarlo](#-avvio-rapido) ·
[Architettura](#-architettura) ·
[Sicurezza](#-sicurezza) ·
[Documentazione](#-documentazione)

</div>

---

Bot Telegram privato e multiutente che automatizza il percorso **LAPI** del
portale di prenotazione dell'Ospedale Pediatrico Bambino Gesù: ricerca di
prestazioni e medici, monitoraggio delle disponibilità, prenotazione con doppia
conferma, gestione degli appuntamenti e amministrazione completa.

Tutto avviene dentro Telegram. Nessuna interfaccia web, nessuna porta in
ingresso, nessun dominio da configurare: basta un server con Docker.

> [!IMPORTANT]
> Progetto **non ufficiale**, non affiliato né gestito dall'ospedale. Ogni
> account collegato deve appartenere a una persona autorizzata. Il bot non
> aggira CAPTCHA, MFA, rate limiting o blocchi del portale.

## ✨ In breve

- 🔎 **Ricerca** per prestazione o per medico, su una, più o tutte le sedi
- 🔔 **Monitoraggi** con notifica, auto-prenotazione o entrambe, filtri su
  date, giorni e fasce orarie
- ✅ **Prenotazione** con doppia conferma e verifica sull'elenco appuntamenti
- 📅 **Appuntamenti**: elenco, promemoria PDF, disdetta verificata
- 👥 **Multiutente** su invito, più account del portale per utente
- 🛡️ **Amministrazione** completa in chat, dati sensibili cifrati e audit
- 🐳 **Un comando** per partire: `docker compose up -d`

## 📑 Indice

- [Cosa fa](#-cosa-fa)
- [Architettura](#-architettura)
- [Requisiti](#-requisiti)
- [Avvio rapido](#-avvio-rapido)
- [Configurazione](#-configurazione)
- [Sviluppo](#-sviluppo)
- [Test](#-test)
- [Operazioni](#-operazioni)
- [Sicurezza](#-sicurezza)
- [Limiti noti](#-limiti-noti)
- [Documentazione](#-documentazione)
- [Licenza](#-licenza)

## 🧭 Cosa fa

| Area | Funzioni |
| --- | --- |
| Accesso | Bot su invito, consenso versionato, ruoli utente e amministratore |
| Account | Più account del portale per utente, collegati in chat, ri-autenticazione, test accesso, rinomina, disabilitazione, rimozione |
| Pazienti | Sincronizzazione dal portale, selezione, identificativi cifrati e mascherati |
| Ricerca | Per prestazione oppure per medico specifico, risultati paginati e selezione esatta |
| Sedi | Una, più o **tutte le sedi dinamiche**, con ordine di preferenza |
| Filtri | Periodo fisso o mobile, anticipo minimo, giorni della settimana, fasce orarie multiple, date escluse, ordinamento, limite risultati |
| Prenotazione | Ricerca immediata, riepilogo, **doppia conferma**, protocollo idempotente e riconciliazione sull'elenco appuntamenti |
| Monitoraggi | Notifica, auto-prenotazione o entrambe; modifica, duplicazione, pausa, ripresa, toggle notifiche, esegui ora, eliminazione |
| Appuntamenti | Elenco e storico, dettaglio, promemoria PDF, disdetta con doppia conferma e verifica |
| Notifiche | Nuovi slot, slot persi, esiti auto-prenotazione, nuova autenticazione, sospensioni, appuntamenti rilevati, disdette |
| Amministrazione | Utenti, inviti, account, pazienti, monitoraggi, slot visti, esecuzioni, tentativi, appuntamenti, notifiche, errori, circuit breaker, code e lease, audit, stato sistema |

Fuori ambito: SSN e ricetta elettronica, pagamenti, registrazione libera,
aggiramento delle protezioni del portale, diagnosi medica.

## 🧱 Architettura

Una sola immagine applicativa, due processi più il database:

```text
   Telegram ◀───── long polling ─────┐
                                     │
                              ┌──────┴──────┐        ┌──────────────┐
                              │     bot     │        │    worker    │
                              │  (aiogram)  │        │  scheduler   │
                              └──────┬──────┘        │  lease       │
                                     │               │  notifiche   │
                              ┌──────▼───────────────┴──────┐
                              │            mysql            │
                              └─────────────────────────────┘
```

Nessun servizio pubblica porte: il bot riceve gli aggiornamenti in long
polling e il worker parla solo con MySQL e con il portale.

Livelli:

```text
Telegram ──▶ services ──▶ domain ──▶ repositories / portal adapter
worker   ──▶ services ──▶ domain ──▶ repositories / portal adapter
```

- il dominio non importa aiogram, SQLAlchemy o httpx;
- gli handler non eseguono SQL: passano dai repository;
- l'adapter del portale non invia messaggi Telegram;
- coordinamento senza Redis: transazioni MySQL, lease con scadenza e
  `GET_LOCK` per le sequenze mutanti.

Struttura del codice in [`app/`](app/): `config.py`, `logging.py`,
[`domain/`](app/domain/), [`security/`](app/security/), [`db/`](app/db/),
[`portal/`](app/portal/), [`services/`](app/services/), [`bot/`](app/bot/),
[`worker/`](app/worker/).

## 📋 Requisiti

- Docker 24+ e Docker Compose v2
- Python 3.13 (solo per sviluppo e test locali)
- MySQL 8.4 LTS (fornito dal Compose)
- Un bot Telegram creato con [@BotFather](https://t.me/BotFather)

Non serve un dominio, né un certificato, né aprire porte sul router.

## 🚀 Avvio rapido

Su un server Linux con Docker bastano due file, senza clonare il repository:

```bash
mkdir opbg-telegram-bot && cd opbg-telegram-bot
BASE=https://raw.githubusercontent.com/LucaTomei/opbg-telegram-bot/main
curl -fsSLO "$BASE/compose.yaml"
curl -fsSL  "$BASE/.env.example" -o .env && chmod 600 .env

# segreti generati, mai inventati
for name in APP_SECRET_KEY APP_ENCRYPTION_KEY APP_BLIND_INDEX_KEY \
            MYSQL_PASSWORD MYSQL_ROOT_PASSWORD BACKUP_PASSPHRASE; do
  value="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=')"
  sed -i "s|^${name}=.*|${name}=${value}|" .env
done

nano .env                 # TELEGRAM_BOT_TOKEN, TELEGRAM_BOT_USERNAME, TELEGRAM_ADMIN_IDS
docker compose pull       # scarica l'immagine pubblicata su GHCR
docker compose up -d      # applica le migrazioni, poi avvia bot e worker
docker compose ps
```

`compose.yaml` non costruisce niente: punta all'immagine che la pipeline
pubblica su GitHub Container Registry a ogni push su `main` che superi i test.
Per costruirla dal sorgente, da un clone del repository:

```bash
make build                # docker compose -f compose.yaml -f compose.dev.yaml build
```

> [!TIP]
> La guida completa, con requisiti, backup, aggiornamenti e messa in sicurezza
> del server, è in [`docs/12-deploy-docker.md`](docs/12-deploy-docker.md).

Poi, da Telegram:

1. l'amministratore (il cui ID è in `TELEGRAM_ADMIN_IDS`) invia `/start`;
2. accetta l'informativa;
3. crea un invito con `/invite` e consegna il codice o il link;
4. l'utente invia `/start CODICE-INVITO`, accetta l'informativa e collega il
   primo account da **Account → Collega account**.

## 🔧 Configurazione

Tutte le variabili sono documentate in [`.env.example`](.env.example). Le più
importanti:

| Variabile | Descrizione |
| --- | --- |
| `OPBG_IMAGE`, `OPBG_TAG` | Immagine da eseguire; per impostazione predefinita `ghcr.io/lucatomei/opbg-telegram-bot:latest` |
| `APP_ENCRYPTION_KEY` | Chiave AES-256 per i dati a riposo. **Perderla rende illeggibili credenziali e pazienti.** |
| `APP_BLIND_INDEX_KEY` | Chiave separata per gli indici di ricerca sui campi cifrati |
| `APP_SECRET_KEY` | Pepe applicativo per gli hash di inviti e nonce di conferma |
| `TELEGRAM_BOT_TOKEN` | Token del bot |
| `TELEGRAM_BOT_USERNAME` | Username del bot, usato per il link di invito |
| `TELEGRAM_ADMIN_IDS` | ID Telegram degli amministratori, separati da virgola |
| `MYSQL_PASSWORD`, `MYSQL_ROOT_PASSWORD` | Credenziali del database |
| `MONITOR_MIN_INTERVAL_SECONDS` | Intervallo minimo tra due controlli (mai sotto 120 s) |
| `PORTAL_MAX_CONCURRENCY` | Chiamate concorrenti verso il portale |

L'avvio fallisce se una chiave manca, è debole o contiene ancora un
segnaposto d'esempio.

## 💻 Sviluppo

```bash
make venv install         # ambiente virtuale e dipendenze
docker compose up -d mysql
make migrate
make verify               # formato, lint, tipi, test, audit
```

Comandi disponibili: `make help`.

`make verify` esegue i controlli locali. **Prima di un push usa
`make verify-ci`**: aggiunge `make audit-ci`, che rilancia i due scanner in un
container Ubuntu identico a quello della CI. Serve perché `git grep` di Apple e
quello GNU trattano diversamente i confini di parola `\b`: uno scanner può
passare su macOS e fallire sul runner.

Le dipendenze dirette sono fissate in [`pyproject.toml`](pyproject.toml) e
bloccate in [`requirements.lock`](requirements.lock) e
[`requirements-dev.lock`](requirements-dev.lock); rigenerale con `make lock`.

## 🧪 Test

| Suite | Comando | Dipendenze |
| --- | --- | --- |
| Unit, contract, security | `make test-unit` | nessuna |
| Integrazione | `make test-integration` | MySQL usa-e-getta |
| Tutto | `make test` | MySQL usa-e-getta |
| Copertura | `make coverage` | MySQL usa-e-getta |
| Portale reale (opt-in) | `pytest -m live` | credenziali reali |

I test di integrazione usano `TEST_DATABASE_URL` (default:
`mysql+aiomysql://root:...@127.0.0.1:13306/opbg_bot_test`) e girano sullo
schema prodotto dalle migrazioni, non su `create_all`: una divergenza fra
modelli e migrazioni fa fallire la suite.

Nessun test automatico crea o disdice un appuntamento reale: i test live sono
opt-in, in sola lettura, e disabilitati per impostazione predefinita.

## 📦 Operazioni

| Scenario | Guida |
| --- | --- |
| Qualsiasi server Linux con Docker (VPS, server di casa) | [`docs/12-deploy-docker.md`](docs/12-deploy-docker.md) |
| Container LXC su Proxmox | [`docs/11-deploy-proxmox.md`](docs/11-deploy-proxmox.md) |

Il runbook completo è in [`docs/10-runbook.md`](docs/10-runbook.md): aggiunta e
rimozione di amministratori, generazione inviti, ri-autenticazione, pausa
globale dei monitoraggi, circuit breaker, diagnosi di un cambio del portale,
rotazione di token e chiavi, cancellazione dati, backup e restore, lettura dei
correlation ID.

Aggiornamento:

```bash
docker compose --profile backup run --rm backup   # 1. backup verificato
docker compose pull                               # 2. nuova immagine
docker compose up -d                              # 3. migrazioni e restart
docker compose ps                                 # 4. health
```

Rollback applicativo: fissare `OPBG_TAG=sha-<commit>` nel file di ambiente e
riavviare, perché ogni build pubblicata è taggata anche con il proprio commit.
Non eseguire un downgrade distruttivo del database senza averlo pianificato; la
procedura è descritta nel runbook.

## 🔐 Sicurezza

- email, password, cookie, identificativi dei pazienti e payload sensibili
  delle conversazioni sono cifrati con AES-256-GCM, legati a tabella, record,
  campo e versione di chiave;
- le credenziali del portale si inseriscono in chat: il messaggio con la
  password viene **cancellato subito** dopo la lettura, la password non entra
  mai nello stato conversazione e il login viene verificato prima di salvare;
- gli indici di ricerca sui campi cifrati sono HMAC con chiave separata;
- inviti e nonce di conferma sono salvati solo come hash, con un pepe derivato
  da `APP_SECRET_KEY`: una copia del database non basta per verificare un
  codice indovinato;
- password, cookie, CSRF, token, codici fiscali e HTML del portale non finiscono
  mai nei log: la redazione è centralizzata e testata;
- ogni azione amministrativa e ogni accesso ai dati completi produce una voce
  di audit immutabile;
- ogni operazione distruttiva richiede una doppia conferma con nonce a
  scadenza, ed è idempotente rispetto ai callback duplicati;
- MySQL non è esposto: nessun servizio pubblica porte in produzione.

Le chat Telegram non sono cifrate end-to-end: l'informativa mostrata all'utente
lo dice esplicitamente prima del collegamento del primo account.

Vulnerabilità: segnalale in privato come descritto in
[`SECURITY.md`](SECURITY.md).

Non committare mai `.env`, file HAR, PDF, catture o dati reali: sono bloccati
da [`.gitignore`](.gitignore) e verificati da `scripts/secret_scan.sh`.

## 🚧 Limiti noti

- **Prenotazione e disdetta reali non coperte da test automatici**: la logica
  è testata contro un portale sintetico con le stesse route, campi form e
  payload, inclusi esito ambiguo, slot perso e sessione scaduta. La prova
  manuale controllata è in
  [`docs/10-runbook.md`](docs/10-runbook.md#verifica-manuale-della-prenotazione-live).
- **Chiave di blind index non ruotabile a caldo**: cambiarla invalida gli
  indici di ricerca sui campi cifrati e richiede di ricostruirli. La chiave di
  cifratura, invece, ruota online con `APP_PREVIOUS_ENCRYPTION_KEYS`.
- **Password inserita in chat**: scelta di prodotto esplicita. Il rischio
  residuo è mitigato cancellando subito il messaggio, non scrivendo mai la
  password nello stato conversazione, verificando il login prima di salvare e
  avvisando l'utente se la cancellazione fallisce.
- **Nessuna lettura condivisa fra monitor simili**: ogni monitor legge la
  propria disponibilità; il carico verso il portale resta limitato dal rate
  limiter globale, dal limite per account e dal circuit breaker.

## 📚 Documentazione

1. [`docs/01-product-requirements.md`](docs/01-product-requirements.md)
2. [`docs/02-telegram-ux.md`](docs/02-telegram-ux.md)
3. [`docs/03-portal-integration.md`](docs/03-portal-integration.md)
4. [`docs/04-data-model.md`](docs/04-data-model.md)
5. [`docs/05-architecture.md`](docs/05-architecture.md)
6. [`docs/06-monitoring-and-autobook.md`](docs/06-monitoring-and-autobook.md)
7. [`docs/07-admin-security-privacy.md`](docs/07-admin-security-privacy.md)
8. [`docs/08-docker-operations.md`](docs/08-docker-operations.md)
9. [`docs/09-testing-acceptance.md`](docs/09-testing-acceptance.md)
10. [`docs/10-runbook.md`](docs/10-runbook.md)
11. [`docs/11-deploy-proxmox.md`](docs/11-deploy-proxmox.md) — deploy su un
    container LXC di Proxmox
12. [`docs/12-deploy-docker.md`](docs/12-deploy-docker.md) — pubblicare il bot
    su un qualsiasi server con Docker

## 📄 Licenza

Distribuito con licenza [MIT](LICENSE). Progetto non affiliato all'Ospedale
Pediatrico Bambino Gesù.

Le modifiche sono elencate in [`CHANGELOG.md`](CHANGELOG.md).
