# Docker e operazioni

## Artefatti richiesti

Il deploy si compone di:

- `Dockerfile` multi-stage non-root;
- immagine pubblicata su un registry dalla pipeline, con tag `latest` e
  `sha-<commit>`;
- `compose.yaml` per la produzione, che esegue l'immagine pubblicata senza
  costruirla;
- `compose.dev.yaml` per lo sviluppo, applicato solo esplicitamente, che
  aggiunge la build dal sorgente;
- entrypoint sicuro;
- script migrazioni, backup, restore, health e key generation;
- `Makefile` o task runner con comandi documentati;
- `.dockerignore` rigoroso;
- README aggiornato con deploy e rollback.

## Servizi Compose

```text
mysql
bot
worker
migrate
backup (profilo opzionale)
```

`bot`, `worker` e `migrate` usano la stessa immagine e versione, con comandi diversi.
Nessun servizio pubblica porte: il bot usa long polling.

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
- Deploy esegue migrazione una sola volta prima di avviare servizi applicativi:
  `bot` e `worker` dipendono dal completamento di `migrate`.
- Lock per evitare migrazioni concorrenti.
- Rollback documentato; non automatico se distruttivo.
- Test da database vuoto e upgrade dalla migrazione precedente.

## Health

### Liveness

Processo ed event loop vivi; non dipende dal portale.

### Readiness bot e worker

- configurazione valida;
- MySQL raggiungibile;
- schema alla revisione corrente;
- per il worker, heartbeat recente.

Il controllo è `python -m app.cli healthcheck`, usato dagli health check dei
container. Non effettua login né prenotazioni.

## Networking

- Nessuna porta pubblicata in produzione: il bot usa long polling e non riceve
  connessioni in ingresso.
- MySQL è raggiungibile solo dalla rete interna del Compose.
- In sviluppo `compose.dev.yaml` pubblica MySQL su `127.0.0.1:13306` per i
  test di integrazione.

## Avvio atteso

Il README finale deve documentare equivalenti di:

```bash
cp .env.example .env
docker compose pull
docker compose up -d
docker compose ps
docker compose logs --tail=200 bot worker
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
2. pull della nuova immagine;
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

