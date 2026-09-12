# Architettura

## Componenti Docker

### `mysql`

MySQL 8.4 LTS, volume persistente, health check, nessuna porta pubblicata in produzione.

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
- ripristino automatico degli account rifiutati dal portale;
- cleanup nonce, stato conversazioni ed eventi;
- heartbeat e circuit breaker.

### `backup` opzionale/profile

Job Docker schedulato per dump MySQL cifrato con retention. Non deve includere `.env` o chiavi nello stesso archivio.

## Struttura target

La struttura del codice è la seguente:

```text
app/
  config.py
  logging.py
  main_bot.py
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
Telegram -> Application services -> Domain -> Repositories/Portal adapter
Worker   -> Application services -> Domain -> Repositories/Portal adapter
```

- Il dominio non importa aiogram, SQLAlchemy o httpx.
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
- Un portale irraggiungibile, lento o che sta limitando le richieste **non è**
  un auth failure: la verifica che segue il login distingue "il portale rifiuta"
  da "il portale non risponde", altrimenti un disservizio di passaggio
  costerebbe a un account funzionante le credenziali salvate.
- Allo stesso modo, una verifica che non riconosce la pagina **non è** un auth
  failure: se il portale non ci fa entrare ma non ci richiede nemmeno le
  credenziali ha cambiato forma, ed è `PORTAL_CONTRACT_CHANGED` con evento di
  sistema per l'amministratore. Trattarlo come password sbagliata sospenderebbe
  tutti gli account insieme per un restyling del sito.
- Auth failure sospende job dell'account e notifica re-auth.
- L'account sospeso riprova da solo le credenziali salvate, a intervalli
  distanziati (`ACCOUNT_RECOVERY_DELAY_SECONDS`) e solo finché è
  `REAUTH_REQUIRED`: niente loop password. Ogni rifiuto vero consuma un
  tentativo, e al blocco l'account passa a una persona. Un account `LOCKED` o
  fermato da un CAPTCHA non viene mai ritentato in automatico.
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
- Il comando `healthcheck` verifica configurazione, MySQL e revisione dello schema; non effettua prenotazioni né login reali.

## Error handling

- Eccezioni tipizzate dal dominio.
- Retry solo per operazioni idempotenti o dopo riconciliazione.
- Nessun retry automatico del POST finale di prenotazione o della disdetta.
- Correlation ID comunicato all'utente; dettaglio tecnico solo log redatto/admin.

