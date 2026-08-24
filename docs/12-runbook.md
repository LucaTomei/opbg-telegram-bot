# Runbook operativo

Procedure per chi gestisce il servizio. Ogni comando presuppone di trovarsi
nella directory del progetto sull'host di produzione, con `.env` compilato.
Nessun segreto va mai passato sulla riga di comando.

## Indice

- [Stato del sistema](#stato-del-sistema)
- [Amministratori](#amministratori)
- [Inviti](#inviti)
- [Ri-autenticazione di un account](#ri-autenticazione-di-un-account)
- [Pausa globale dei monitoraggi](#pausa-globale-dei-monitoraggi)
- [Circuit breaker](#circuit-breaker)
- [Cambio del portale](#cambio-del-portale)
- [Correlation ID](#correlation-id)
- [Rotazione del token del bot](#rotazione-del-token-del-bot)
- [Rotazione della chiave di cifratura](#rotazione-della-chiave-di-cifratura)
- [Cancellazione di un utente o di un account](#cancellazione-di-un-utente-o-di-un-account)
- [Backup](#backup)
- [Restore](#restore)
- [Aggiornamento e rollback](#aggiornamento-e-rollback)
- [Verifica manuale della prenotazione live](#verifica-manuale-della-prenotazione-live)

## Stato del sistema

Da Telegram: **🛡 Amministrazione → Stato sistema** e **Code e lease**.

Da riga di comando:

```bash
docker compose ps
docker compose logs --tail=200 web bot worker
curl -fsS https://$APP_DOMAIN/health/ready
curl -fsS https://$APP_DOMAIN/version
```

`/health/ready` verifica configurazione, MySQL e revisione dello schema. Non
contatta mai il portale.

## Amministratori

Gli amministratori arrivano esclusivamente da `TELEGRAM_ADMIN_IDS`.

```bash
# 1. modifica TELEGRAM_ADMIN_IDS in .env
docker compose up -d bot worker
# oppure, senza riavviare:
docker compose run --rm web bootstrap-admins
```

Chi viene rimosso dalla variabile perde il ruolo al successivo avvio: il
comando revoca il ruolo amministrativo a tutti gli ID non più elencati.

## Inviti

Da Telegram: `/invite`, oppure **Amministrazione → Inviti → Nuovo invito**.
Il codice è mostrato **una sola volta**; nel database esiste solo il suo hash.
La revoca è immediata da **Inviti**, toccando la voce corrispondente.

## Ri-autenticazione di un account

Quando il portale rifiuta le credenziali il bot mette in pausa i monitoraggi
di quell'account e invia una notifica. L'utente deve:

1. aprire **Account** → l'account interessato;
2. toccare **Aggiorna credenziali**;
3. aprire il link HTTPS monouso e inserire email e password.

Al termine i monitoraggi sospesi per `REAUTH_REQUIRED` tornano attivi da soli.

## Pausa globale dei monitoraggi

Il modo supportato è aprire il circuit breaker globale:

**Amministrazione → Errori e circuit breaker → Circuit breaker → Apri
globale**, indicando il motivo. Tutti i monitoraggi vengono differiti senza
perdere la configurazione. Per riprendere: **Chiudi globale**.

In alternativa, per una manutenzione più lunga:

```bash
docker compose stop worker
```

Il worker rilascia i propri lease durante lo spegnimento, quindi un altro
worker può riprendere il lavoro immediatamente.

## Circuit breaker

- **per account**: si apre dopo `BREAKER_ACCOUNT_FAILURE_THRESHOLD` errori e
  sospende solo quell'account;
- **globale**: si apre dopo `BREAKER_GLOBAL_FAILURE_THRESHOLD` errori e
  protegge il portale.

Alla scadenza del cooldown il breaker passa in `HALF_OPEN` e lascia passare una
sola richiesta idempotente. L'apertura o la chiusura manuale richiede un motivo
e produce una voce di audit.

## Cambio del portale

Sintomi: eventi `PORTAL_CONTRACT_CHANGED` in **Errori e circuit breaker**,
monitoraggi in errore, breaker aperto.

1. leggere l'evento: contiene il correlation ID e un messaggio redatto, mai
   l'HTML del portale;
2. verificare manualmente il portale con un browser;
3. aggiornare i parser in [`app/portal/parsers.py`](../app/portal/parsers.py) e
   i fixture sintetici in `tests/fixtures/synthetic/`;
4. eseguire `make test-unit` e rilasciare;
5. chiudere il breaker con un motivo.

I fixture devono restare sintetici: non copiare mai una cattura reale.

## Correlation ID

Ogni errore mostrato all'utente riporta un codice. Per ritrovarlo:

```bash
docker compose logs web bot worker | grep '"correlation_id":"<CODICE>"'
```

Oppure da **Amministrazione → Audit**, filtrando per correlation ID. I log sono
JSON e già redatti: non contengono password, cookie, token, tax code né HTML.

## Rotazione del token del bot

```bash
# 1. genera il nuovo token con @BotFather e revoca il precedente
# 2. aggiorna TELEGRAM_BOT_TOKEN in .env
docker compose up -d bot worker
docker compose logs --tail=50 bot
```

Se il token è errato il servizio si ferma con
`{"event_code":"BOT_TOKEN_REJECTED"}` invece di andare in crash loop silenzioso.

## Rotazione della chiave di cifratura

I dati esistenti restano leggibili finché la chiave precedente resta caricata.

```bash
docker compose --profile backup run --rm backup     # 1. backup verificato
make keys                                            # 2. nuova chiave
```

Nel file `.env`:

```dotenv
APP_ENCRYPTION_KEY=<nuova chiave>
APP_ENCRYPTION_KEY_VERSION=2
APP_PREVIOUS_ENCRYPTION_KEYS=1:<chiave precedente>
```

```bash
docker compose up -d web bot worker
```

I record vengono ri-cifrati con la nuova versione alla prima scrittura. La
chiave precedente può essere rimossa solo quando nessun record la usa più; fino
ad allora va conservata, separatamente dai backup.

La chiave di blind index (`APP_BLIND_INDEX_KEY`) **non è ruotabile a caldo**:
cambiarla invalida tutti gli indici di ricerca. Va cambiata solo insieme a una
ricostruzione degli indici.

Anche `APP_SECRET_KEY` non è ruotabile a caldo: pepa gli hash di inviti,
ticket di collegamento, link di download e nonce di conferma. Cambiarla
invalida tutti i token già emessi; gli utenti dovranno richiedere un nuovo
link o un nuovo invito.

## Cancellazione di un utente o di un account

L'utente può farlo da solo: **Impostazioni → Privacy → Elimina tutti i miei
dati**, con doppia conferma.

L'amministratore può bloccare un utente da **Amministrazione → Utenti**,
indicando il motivo: i monitoraggi vengono messi in pausa e l'azione è
registrata nell'audit. La cancellazione elimina crittograficamente credenziali,
cookie, pazienti e appuntamenti in cache; l'audit e i tentativi di prenotazione
restano per la retention configurata.

## Backup

```bash
docker compose --profile backup run --rm backup
```

Il job produce `/backups/opbg-<timestamp>-<schema>.sql.gz.enc`, cifrato con
AES-256-CBC e la passphrase indicata da `BACKUP_PASSPHRASE_FILE` o
`BACKUP_PASSPHRASE`. Se il dump è vuoto o fallisce, l'archivio non viene
conservato e l'esito `FAILURE` è visibile in **Stato sistema**.

La passphrase e `APP_ENCRYPTION_KEY` **non devono essere conservate insieme
agli archivi**: un backup e la sua chiave nello stesso posto equivalgono a un
backup in chiaro.

Retention: `BACKUP_RETENTION_DAYS` (default 14 giorni).

## Restore

Il restore scrive sempre su un database separato, mai sopra la produzione.

```bash
docker compose --profile backup run --rm --entrypoint sh backup \
  -c 'sh /opt/backup/restore.sh /backups/<archivio>.sql.gz.enc opbg_bot_restore'
```

Verifica:

```bash
docker compose exec mysql mysql -uroot -p -e \
  "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='opbg_bot_restore';"
```

Solo dopo il controllo si può decidere di puntare l'applicazione al database
ripristinato, fermando prima `web`, `bot` e `worker`.

## Aggiornamento e rollback

```bash
docker compose --profile backup run --rm backup
docker compose build
docker compose run --rm migrate
docker compose up -d
docker compose ps
curl -fsS https://$APP_DOMAIN/health/ready
```

Rollback applicativo: ripristinare l'immagine precedente e riavviare. Il
downgrade dello schema esiste ma **cancella le tabelle**:

```bash
docker compose run --rm --entrypoint sh web -c 'python -m app.cli downgrade -1'
```

Va usato solo su un ambiente ricostruibile o dopo un restore verificato.

## Verifica manuale della prenotazione live

Prenotazione e disdetta reali non sono automatizzate: nessun test crea o
cancella un appuntamento vero. La prova manuale controllata è:

1. usare un account di prova autorizzato e un paziente reale del proprio
   nucleo familiare;
2. scegliere una prestazione con disponibilità ampia;
3. eseguire il flusso **Prenota** fino alla doppia conferma;
4. verificare che il bot mostri **Prenotazione confermata** e che
   l'appuntamento compaia in **Appuntamenti** e sul portale ufficiale;
5. scaricare il promemoria PDF;
6. eseguire la disdetta dal bot con doppia conferma e verificare che
   l'appuntamento sparisca sia dal bot sia dal portale;
7. annotare data, esito e correlation ID in
   [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md).

Se un esito resta incerto il bot registra `AMBIGUOUS` e **non ritenta**:
riconciliare manualmente prima di ripetere l'operazione.
