# Docker e operazioni

## Artefatti richiesti

Claude Code deve creare:

- `Dockerfile` multi-stage non-root;
- `compose.yaml` per produzione locale;
- `compose.override.yaml` o profilo development;
- configurazione Caddy/TLS;
- entrypoint sicuro;
- script migrazioni, backup, restore, health e key generation;
- `Makefile` o task runner con comandi documentati;
- `.dockerignore` rigoroso;
- README aggiornato con deploy e rollback.

## Servizi Compose

```text
mysql
web
bot
worker
proxy
backup (profile opzionale)
```

`web`, `bot` e `worker` usano la stessa immagine/versione ma comandi diversi.

## MySQL

- Immagine 8.4 LTS con digest/tag documentato.
- Volume nominato.
- Health check reale con utente limitato.
- `utf8mb4`, timezone UTC.
- Porta non pubblicata nel profilo produzione.
- Password solo da environment/secret; nessun valore debole di default.
- Pool dimensionato per piccolo carico.

## Migrazioni

- Job/command separato `migrate`.
- Deploy esegue migrazione una sola volta prima di avviare servizi applicativi.
- Lock per evitare migrazioni concorrenti.
- Rollback documentato; non automatico se distruttivo.
- Test da database vuoto e upgrade dalla migrazione precedente.

## Health

### Liveness

Processo/event loop vivo; non dipende dal portale.

### Readiness web/bot

- configurazione valida;
- MySQL raggiungibile;
- migrazione schema corrente;
- per bot, polling inizializzato.

### Readiness worker

- MySQL raggiungibile;
- heartbeat recente;
- non richiede che il portale sia online.

## TLS e networking

- Solo proxy espone 80/443.
- Redirect HTTP→HTTPS.
- HSTS dopo verifica dominio.
- Caddy inoltra solo route web previste.
- Pagina credenziali senza cache.
- Bot long polling: nessuna porta Telegram in ingresso.

## Avvio atteso

Il README finale deve documentare equivalenti di:

```bash
cp .env.example .env
docker compose build
docker compose run --rm migrate
docker compose up -d
docker compose ps
docker compose logs --tail=200 web bot worker
```

Non inserire mai segreti nei comandi di esempio.

## Backup

- Dump consistente MySQL.
- Compressione e cifratura prima della retention.
- Nome con timestamp UTC e schema version.
- Retention configurabile.
- Health/status dell'ultimo backup visibile all'admin.
- Procedura restore su istanza temporanea testata e documentata.
- Le chiavi di cifratura non sono nello stesso backup.

## Aggiornamento

1. backup verificato;
2. pull/build nuova immagine;
3. esecuzione migrazioni;
4. restart servizi;
5. health e smoke test;
6. rollback applicazione se necessario, senza downgrade DB distruttivo non pianificato.

## Operazioni admin/runbook

Documentare:

- aggiungere/rimuovere admin via env e restart;
- generare invito;
- ri-autenticare account;
- mettere in pausa globale i monitor;
- aprire/chiudere circuit breaker;
- diagnosticare portal contract change;
- ruotare bot token e encryption key;
- cancellare utente/account;
- backup/restore;
- leggere correlation ID senza accedere a segreti.

## Risorse consigliate

Per pochi utenti impostare limiti conservativi e documentarli; non promettere valori assoluti. MySQL deve avere persistent volume e memoria sufficiente. I servizi applicativi devono avere restart policy e shutdown graceful per rilasciare lease.

