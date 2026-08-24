# OPBG Telegram Booking Bot

Bot Telegram privato e multiutente che automatizza il percorso **LAPI** del
portale di prenotazione dell'Ospedale Pediatrico Bambino Gesù: ricerca di
prestazioni e medici, monitoraggio delle disponibilità, prenotazione con doppia
conferma, gestione degli appuntamenti e amministrazione completa.

> Servizio **privato e non ufficiale**, non gestito dall'ospedale. Ogni account
> collegato deve appartenere a una persona autorizzata. Il bot non aggira
> CAPTCHA, MFA, rate limiting o blocchi del portale.

## Indice

- [Cosa fa](#cosa-fa)
- [Architettura](#architettura)
- [Requisiti](#requisiti)
- [Avvio rapido](#avvio-rapido)
- [Configurazione](#configurazione)
- [Sviluppo](#sviluppo)
- [Test](#test)
- [Operazioni](#operazioni)
- [Sicurezza](#sicurezza)
- [Documentazione](#documentazione)

## Cosa fa

| Area | Funzioni |
| --- | --- |
| Accesso | Bot su invito, consenso versionato, ruoli utente/amministratore |
| Account | Più account del portale per utente, collegati via pagina HTTPS monouso, ri-autenticazione, test accesso, rinomina, disabilitazione, rimozione |
| Pazienti | Sincronizzazione dal portale, selezione, identificativi cifrati e mascherati |
| Ricerca | Per prestazione oppure per medico specifico, risultati paginati e selezione esatta |
| Sedi | Una, più o **tutte le sedi dinamiche**, con ordine di preferenza |
| Filtri | Periodo fisso o mobile, anticipo minimo, giorni della settimana, fasce orarie multiple, date escluse, ordinamento, limite risultati |
| Prenotazione | Ricerca immediata, riepilogo, **doppia conferma**, protocollo idempotente e riconciliazione sull'elenco appuntamenti |
| Monitoraggi | Modalità notifica, auto-prenotazione o entrambe; modifica, duplicazione, pausa, ripresa, toggle notifiche, esegui ora, eliminazione |
| Appuntamenti | Elenco e storico, dettaglio, promemoria PDF, disdetta con doppia conferma e verifica |
| Notifiche | Nuovi slot, slot persi, esiti auto-prenotazione, re-auth, sospensioni, appuntamenti rilevati, disdette, PDF non disponibile |
| Amministrazione | Utenti, inviti, account, pazienti, monitoraggi, slot visti, esecuzioni, tentativi, appuntamenti, notifiche, errori, circuit breaker, code e lease, audit, stato sistema |

Fuori ambito: SSN e ricetta elettronica, pagamenti, registrazione libera,
aggiramento delle protezioni del portale, diagnosi medica.

## Architettura

Una sola immagine applicativa, quattro processi:

```text
                    ┌───────────────┐
   HTTPS ──────────▶│ proxy (Caddy) │
                    └───────┬───────┘
                            │ /connect /download /health
                    ┌───────▼───────┐
                    │ web (FastAPI) │
                    └───────┬───────┘
 Telegram ◀────────┐        │        ┌──────────────────┐
                   │        │        │ worker           │
            ┌──────▼────────▼────────▼──────┐  scheduler │
            │            mysql              │  lease     │
            └───────────────────────────────┘  notifiche │
                            ▲                 └──────────┘
                    ┌───────┴───────┐
                    │ bot (aiogram) │ long polling
                    └───────────────┘
```

Livelli:

```text
Telegram / Web  ──▶ services ──▶ domain ──▶ repositories / portal adapter
worker          ──▶ services ──▶ domain ──▶ repositories / portal adapter
```

- il dominio non importa aiogram, FastAPI, SQLAlchemy o httpx;
- gli handler non eseguono SQL: passano dai repository;
- l'adapter del portale non invia messaggi Telegram;
- coordinamento senza Redis: transazioni MySQL, lease con scadenza e
  `GET_LOCK` per le sequenze mutanti.

Struttura del codice in [`app/`](app/): `config.py`, `logging.py`,
[`domain/`](app/domain/), [`security/`](app/security/), [`db/`](app/db/),
[`portal/`](app/portal/), [`services/`](app/services/), [`bot/`](app/bot/),
[`web/`](app/web/), [`worker/`](app/worker/).

## Requisiti

- Docker 24+ e Docker Compose v2 (per il deploy)
- Python 3.13 (solo per sviluppo e test locali)
- MySQL 8.4 LTS (fornito dal Compose)
- Un bot Telegram creato con [@BotFather](https://t.me/BotFather)
- Un dominio con DNS puntato all'host, per il certificato TLS automatico

## Avvio rapido

```bash
cp .env.example .env
make keys                 # genera i segreti e incollali in .env
docker compose build
docker compose run --rm migrate
docker compose up -d
docker compose ps
docker compose logs --tail=200 web bot worker
```

Poi, da Telegram:

1. l'amministratore (il cui ID è in `TELEGRAM_ADMIN_IDS`) invia `/start`;
2. accetta l'informativa;
3. crea un invito con `/invite` e lo consegna all'utente;
4. l'utente invia `/start CODICE-INVITO`, accetta l'informativa e collega il
   primo account dalla sezione **Account**.

Nessun segreto va mai passato sulla riga di comando o scritto in chat.

## Configurazione

Tutte le variabili sono documentate in [`.env.example`](.env.example). Le più
importanti:

| Variabile | Descrizione |
| --- | --- |
| `APP_ENCRYPTION_KEY` | Chiave AES-256 per i dati a riposo. **Perderla rende illeggibili credenziali e pazienti.** |
| `APP_BLIND_INDEX_KEY` | Chiave separata per gli indici di ricerca sui campi cifrati |
| `APP_SECRET_KEY` | Pepe applicativo per gli hash di inviti, ticket, link e nonce |
| `PUBLIC_BASE_URL` | URL HTTPS pubblico usato nei link monouso |
| `TELEGRAM_BOT_TOKEN` | Token del bot |
| `TELEGRAM_ADMIN_IDS` | ID Telegram degli amministratori, separati da virgola |
| `MYSQL_PASSWORD`, `MYSQL_ROOT_PASSWORD` | Credenziali del database |
| `MONITOR_MIN_INTERVAL_SECONDS` | Intervallo minimo tra due controlli (mai sotto 120 s) |
| `PORTAL_MAX_CONCURRENCY` | Chiamate concorrenti verso il portale |
| `PDF_DELIVERY_MODE` | `telegram` (documento) oppure `one_time_link` |

L'avvio fallisce se una chiave manca, è debole o contiene ancora un
segnaposto d'esempio.

## Sviluppo

```bash
make venv install         # ambiente virtuale e dipendenze
docker compose up -d mysql
make migrate
make verify               # formato, lint, tipi, test, audit
```

Comandi disponibili: `make help`.

Le dipendenze dirette sono fissate in [`pyproject.toml`](pyproject.toml) e
bloccate in [`requirements.lock`](requirements.lock) e
[`requirements-dev.lock`](requirements-dev.lock); rigenerale con `make lock`.

## Test

| Suite | Comando | Dipendenze |
| --- | --- | --- |
| Unit, contract, security | `make test-unit` | nessuna |
| Integrazione | `make test-integration` | MySQL usa-e-getta |
| Tutto | `make test` | MySQL usa-e-getta |
| Copertura | `make coverage` | MySQL usa-e-getta |
| Portale reale (opt-in) | `pytest -m live` | credenziali reali |

I test di integrazione usano `TEST_DATABASE_URL` (default:
`mysql+aiomysql://root:...@127.0.0.1:13306/opbg_bot_test`). Il profilo di
sviluppo pubblica MySQL su `127.0.0.1:13306`; in produzione la porta non è
esposta.

Nessun test automatico crea o disdice un appuntamento reale: i test live sono
opt-in, in sola lettura, e disabilitati per impostazione predefinita.

## Operazioni

Il runbook completo è in [`docs/12-runbook.md`](docs/12-runbook.md): aggiunta e
rimozione di amministratori, generazione inviti, ri-autenticazione, pausa
globale dei monitoraggi, circuit breaker, diagnosi di un cambio del portale,
rotazione di token e chiavi, cancellazione dati, backup e restore, lettura dei
correlation ID.

Aggiornamento:

```bash
docker compose --profile backup run --rm backup   # 1. backup verificato
docker compose build                              # 2. nuova immagine
docker compose run --rm migrate                   # 3. migrazioni
docker compose up -d                              # 4. restart
docker compose ps                                 # 5. health
```

Rollback applicativo: ripristinare il tag precedente dell'immagine e
riavviare. Non eseguire un downgrade distruttivo del database senza averlo
pianificato; la procedura è descritta nel runbook.

## Sicurezza

- le password del portale **non transitano mai** su Telegram: si inseriscono
  solo in una pagina HTTPS monouso, con CSRF, rate limit e CSP stretta;
- email, password, cookie, identificativi dei pazienti e payload sensibili
  delle conversazioni sono cifrati con AES-256-GCM, legati a tabella, record,
  campo e versione di chiave;
- gli indici di ricerca sui campi cifrati sono HMAC con chiave separata;
- inviti, ticket, link di download e nonce di conferma sono salvati solo come
  hash, con un pepe derivato da `APP_SECRET_KEY`: una copia del database non
  basta per verificare un codice indovinato;
- password, cookie, CSRF, token, tax code e HTML del portale non finiscono mai
  nei log: la redazione è centralizzata e testata;
- ogni azione amministrativa e ogni accesso ai dati completi produce una voce
  di audit immutabile;
- ogni operazione distruttiva richiede una doppia conferma con nonce a
  scadenza, ed è idempotente rispetto ai callback duplicati;
- MySQL non è esposto in produzione; il proxy inoltra solo le route previste.

Non committare mai `.env`, file HAR, PDF, catture o dati reali: sono bloccati
da [`.gitignore`](.gitignore) e verificati da `scripts/secret_scan.sh`.

## Documentazione

1. [`CLAUDE.md`](CLAUDE.md) — vincoli di implementazione
2. [`docs/00-decisions.md`](docs/00-decisions.md)
3. [`docs/01-product-requirements.md`](docs/01-product-requirements.md)
4. [`docs/02-telegram-ux.md`](docs/02-telegram-ux.md)
5. [`docs/03-portal-integration.md`](docs/03-portal-integration.md)
6. [`docs/04-data-model.md`](docs/04-data-model.md)
7. [`docs/05-architecture.md`](docs/05-architecture.md)
8. [`docs/06-monitoring-and-autobook.md`](docs/06-monitoring-and-autobook.md)
9. [`docs/07-admin-security-privacy.md`](docs/07-admin-security-privacy.md)
10. [`docs/08-docker-operations.md`](docs/08-docker-operations.md)
11. [`docs/09-testing-acceptance.md`](docs/09-testing-acceptance.md)
12. [`docs/10-implementation-plan.md`](docs/10-implementation-plan.md)
13. [`docs/11-definition-of-done.md`](docs/11-definition-of-done.md)
14. [`docs/12-runbook.md`](docs/12-runbook.md)
15. [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md) — stato,
    verifiche eseguite e matrice di tracciabilità
