# Architettura

## Componenti Docker

### `mysql`

MySQL 8.4 LTS, volume persistente, health check, nessuna porta pubblicata in produzione.

### `web`

FastAPI:

- pagina HTTPS one-time per collegamento/ri-autenticazione account;
- endpoint download PDF one-time se configurato;
- `/health/live`, `/health/ready`, `/version`;
- nessuna API amministrativa pubblica.

### `bot`

aiogram long polling:

- reply e inline keyboards;
- routing comandi/callback;
- conversazioni persistite in MySQL;
- nessuna chiamata lunga al portale dentro l'handler: delegare a service con timeout o job.

### `worker`

- scheduler e lease MySQL;
- monitoraggi disponibilità;
- auto-prenotazione;
- sincronizzazione appuntamenti;
- coda notifiche e retry;
- cleanup ticket/nonces/eventi;
- heartbeat e circuit breaker.

### `proxy`

Caddy o equivalente per TLS diretto, security headers e reverse proxy verso `web`. Il webhook Telegram non è necessario perché il bot usa long polling.

### `backup` opzionale/profile

Job Docker schedulato per dump MySQL cifrato con retention. Non deve includere `.env` o chiavi nello stesso archivio.

## Struttura target

Claude Code deve creare una struttura equivalente a:

```text
app/
  config.py
  logging.py
  main_bot.py
  main_web.py
  main_worker.py
  db/
    base.py
    session.py
    models/
    repositories/
  domain/
    enums.py
    entities.py
    errors.py
  portal/
    client.py
    models.py
    parsers.py
    forms.py
    redaction.py
  bot/
    routers/
    keyboards/
    callbacks.py
    middleware/
    presenters/
  web/
    routes/
    forms.py
    templates/
  services/
    accounts.py
    bookings.py
    appointments.py
    watches.py
    notifications.py
    admin.py
  worker/
    scheduler.py
    leases.py
    tasks.py
    circuit_breaker.py
  security/
    encryption.py
    blind_index.py
    tokens.py
    audit.py
alembic/
tests/
  unit/
  integration/
  contract/
  fixtures/synthetic/
scripts/
docker/
```

Nomi diversi sono accettabili se le responsabilità rimangono separate.

## Dipendenze tra livelli

```text
Telegram/Web -> Application services -> Domain -> Repositories/Portal adapter
Worker       -> Application services -> Domain -> Repositories/Portal adapter
```

- Il dominio non importa aiogram, FastAPI, SQLAlchemy o httpx.
- Gli handler non eseguono query SQL dirette.
- Il portal adapter non invia messaggi Telegram.
- I repository non contengono logica di parsing HTML.

## Concorrenza senza Redis

- Usare transazioni MySQL e lease con `lease_owner/lease_expires_at`.
- Acquisire job scaduti con `SELECT ... FOR UPDATE SKIP LOCKED` dove disponibile.
- Prenotazione/disdetta richiedono lock logico per account e target.
- Lease rinnovato durante task lunghi; scadenza recupera worker morto.
- Un singolo account esegue al massimo una sequenza mutante alla volta.
- Il limite globale portale è condiviso tra worker tramite tabella/semaforo o named lock documentato.

## Sessioni portale

- `PortalSessionManager` carica cookie cifrati, verifica sessione e rinnova login.
- Cookie aggiornati vengono cifrati e salvati soltanto dopo risposta valida.
- Credenziali vengono decrittate nel minor scope possibile e subito dereferenziate.
- Auth failure sospende job dell'account e notifica re-auth; niente loop password.
- CSRF è acquisito per fase e non riutilizzato oltre la sessione prevista.

## Configurazione

- Pydantic Settings o equivalente.
- Startup fallisce se segreti/configurazione obbligatori sono assenti o deboli.
- `.env.example` è documentazione; `.env` mai committato.
- Nessun default di produzione per password/chiavi.

## Osservabilità

- JSON structured logs con timestamp, level, service, correlation ID, user/account ID interno e event code.
- Redazione centralizzata di email, password, cookie, token, tax code, query string sensibili e HTML.
- Metriche minime: job due/attivi/errori, latenza portale, status login, booking outcomes, notifiche, circuit breaker.
- Health readiness verifica MySQL e heartbeat worker; non effettua prenotazioni né login reali.

## Error handling

- Eccezioni tipizzate dal dominio.
- Retry solo per operazioni idempotenti o dopo riconciliazione.
- Nessun retry automatico del POST finale di prenotazione o della disdetta.
- Correlation ID comunicato all'utente; dettaglio tecnico solo log redatto/admin.

