# Pubblicare il bot con Docker

Guida per mettere online una propria istanza del bot su un qualsiasi server
Linux con Docker: una VPS (Hetzner, DigitalOcean, OVH, Scaleway…), un server di
casa, un mini PC. Per un container LXC di Proxmox c'è la guida
dedicata [`11-deploy-proxmox.md`](11-deploy-proxmox.md).

Il bot usa il **long polling**: è lui a chiedere a Telegram i nuovi messaggi.
Quindi non servono un dominio, un certificato HTTPS, un reverse proxy né porte
aperte in ingresso. Serve solo traffico in uscita verso `api.telegram.org` e
verso il portale.

- [1. Requisiti](#1-requisiti)
- [2. Creare il bot Telegram](#2-creare-il-bot-telegram)
- [3. Installare Docker](#3-installare-docker)
- [4. Preparare la cartella](#4-preparare-la-cartella)
- [5. Generare i segreti](#5-generare-i-segreti)
- [6. Avviare](#6-avviare)
- [7. Verificare](#7-verificare)
- [8. Primo accesso da Telegram](#8-primo-accesso-da-telegram)
- [9. Aggiornare e tornare indietro](#9-aggiornare-e-tornare-indietro)
- [10. Backup](#10-backup)
- [11. Mettere in sicurezza il server](#11-mettere-in-sicurezza-il-server)
- [Usare una propria immagine](#usare-una-propria-immagine)
- [Diagnosi](#diagnosi)

## 1. Requisiti

| Risorsa | Minimo | Note |
| --- | --- | --- |
| Sistema | Linux x86_64 | Debian 12, Ubuntu 24.04 o simili; su arm64 (Raspberry Pi) va costruita l'immagine, vedi [sotto](#usare-una-propria-immagine) |
| RAM | 2 GB | MySQL da solo ne usa fino a 1 GB |
| Disco | 10 GB | immagini, database, backup |
| Rete | solo in uscita | nessuna porta in ingresso oltre a SSH |
| Software | Docker Engine 24+ con Compose v2 | installato al passo 3 |

> Il bot conserva credenziali del portale e dati di pazienti, cifrati. Scegli un
> server di cui ti fidi, tienilo aggiornato e non condividerlo con servizi che
> non controlli.

## 2. Creare il bot Telegram

1. Apri [@BotFather](https://t.me/BotFather) e invia `/newbot`.
2. Scegli nome e username (deve finire in `bot`).
3. Copia il **token**: è la credenziale del bot, trattalo come una password.
4. Scopri il tuo **ID numerico** Telegram, per esempio scrivendo a
   [@userinfobot](https://t.me/userinfobot). Sarai l'amministratore.

Facoltativo, sempre da BotFather: `/setprivacy` su `Enable` e `/setjoingroups`
su `Disable`, perché il bot lavora solo in chat privata.

## 3. Installare Docker

Sul server, come utente con `sudo`:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"     # poi esci e rientra
docker version
docker compose version
```

Lo script ufficiale installa Docker Engine e il plugin Compose v2 e abilita il
servizio al boot. Se preferisci i pacchetti della distribuzione, assicurati che
`docker compose` (con lo spazio) esista: la vecchia `docker-compose` v1 non va
bene.

## 4. Preparare la cartella

Bastano due file: `compose.yaml` del repository, usato così com'è, e un `.env`
con la tua configurazione. Niente sorgente, niente build.

```bash
sudo mkdir -p /opt/opbg-telegram-bot
sudo chown "$USER": /opt/opbg-telegram-bot
cd /opt/opbg-telegram-bot

BASE=https://raw.githubusercontent.com/LucaTomei/opbg-telegram-bot/main
curl -fsSLO "$BASE/compose.yaml"
curl -fsSL  "$BASE/.env.example" -o .env
chmod 600 .env
```

`compose.yaml` punta all'immagine pubblicata su GitHub Container Registry,
`ghcr.io/lucatomei/opbg-telegram-bot`, che la pipeline ricostruisce a ogni push
su `main` che superi i test.

## 5. Generare i segreti

Cinque valori vanno generati, non inventati. `openssl` produce il formato
giusto e scrive direttamente nel `.env`:

```bash
for name in APP_SECRET_KEY APP_ENCRYPTION_KEY APP_BLIND_INDEX_KEY \
            MYSQL_PASSWORD MYSQL_ROOT_PASSWORD BACKUP_PASSPHRASE; do
  value="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=')"
  sed -i "s|^${name}=.*|${name}=${value}|" .env
done
```

Poi apri `.env` e completa la parte Telegram:

```dotenv
TELEGRAM_BOT_TOKEN=<token di BotFather>
TELEGRAM_BOT_USERNAME=<username del bot, senza @>
TELEGRAM_ADMIN_IDS=<il tuo ID numerico; più ID separati da virgola>
```

Il resto ha valori predefiniti sensati e documentati nel file stesso. Lascia
`APP_ENV=production`. L'avvio fallisce se una chiave manca, è troppo corta o è
ancora un segnaposto.

> **Copia subito il `.env` in un password manager.** `APP_ENCRYPTION_KEY` e
> `APP_BLIND_INDEX_KEY` non si possono recuperare: perderle significa perdere
> tutte le credenziali e i pazienti salvati, anche avendo i backup del
> database. Non tenerle nello stesso posto dei backup.

## 6. Avviare

```bash
docker compose pull
docker compose up -d
```

Al primo avvio Compose crea il database, esegue le migrazioni nel servizio
`migrate`, aspetta che finisca e solo allora avvia `bot` e `worker`. Tutti i
servizi hanno `restart: unless-stopped`: dopo un riavvio del server ripartono
da soli.

## 7. Verificare

```bash
docker compose ps
docker compose logs --tail=100 bot worker
```

`mysql`, `bot` e `worker` devono risultare `running (healthy)` entro un paio di
minuti; `migrate` risulta uscito con codice 0. Nessun servizio deve pubblicare
porte:

```bash
docker compose ps --format '{{.Service}}  {{.Status}}  [{{.Ports}}]'
```

Le parentesi devono essere vuote. Controllo puntuale di salute:

```bash
docker compose exec bot python -m app.cli healthcheck
```

## 8. Primo accesso da Telegram

1. Invia `/start` al bot: il tuo ID è in `TELEGRAM_ADMIN_IDS`, quindi entri come
   amministratore.
2. Accetta l'informativa.
3. **Account → Collega account** per collegare il tuo account del portale. Il
   messaggio con la password viene cancellato subito dalla chat.
4. Per altre persone: `/invite` genera un codice o un link a scadenza. Chi lo
   riceve invia `/start CODICE`, accetta l'informativa e collega il proprio
   account.

Il bot è privato: senza invito valido nessuno può usarlo.

## 9. Aggiornare e tornare indietro

```bash
cd /opt/opbg-telegram-bot
docker compose --profile backup run --rm backup   # 1. backup
docker compose pull                               # 2. nuova immagine
docker compose up -d                              # 3. migrazioni e restart
docker compose ps                                 # 4. salute
docker image prune -f                             # 5. spazio
```

Ogni immagine è pubblicata anche con il tag del proprio commit. Per fissare una
versione esatta o tornare indietro, nel `.env`:

```dotenv
OPBG_TAG=sha-<commit>
```

e poi `docker compose up -d`. Le migrazioni non si annullano da sole: tornare a
un'immagine più vecchia di uno schema già migrato va pianificato, vedi il
[runbook](10-runbook.md).

Se hai scaricato `compose.yaml` con `curl`, ogni tanto riscaricalo dal
repository: le nuove versioni possono aggiungere servizi o variabili. Le
modifiche sono elencate in [`CHANGELOG.md`](../CHANGELOG.md).

## 10. Backup

Il servizio `backup` esegue un dump di MySQL, lo comprime e lo cifra con
`BACKUP_PASSPHRASE`, conservando gli archivi per `BACKUP_RETENTION_DAYS` giorni
nel volume `backup-data`:

```bash
docker compose --profile backup run --rm backup
```

Pianificalo ogni notte con `crontab -e`:

```cron
30 3 * * * cd /opt/opbg-telegram-bot && docker compose --profile backup run --rm backup >> /var/log/opbg-backup.log 2>&1
```

Un backup che resta sullo stesso server non protegge da un guasto del server.
Copia gli archivi altrove, per esempio:

```bash
docker run --rm -v opbg-telegram-bot_backup-data:/backups -v "$HOME/opbg-backups":/out \
  alpine sh -c 'cp -n /backups/*.enc /out/'
rsync -a "$HOME/opbg-backups/" utente@altro-host:/backup/opbg/
```

Gli archivi sono cifrati, ma non contengono le chiavi applicative: per un
ripristino servono archivio, passphrase **e** il `.env` originale. La procedura
di restore è nel [runbook](10-runbook.md#restore).

## 11. Mettere in sicurezza il server

Il bot non apre porte, ma il server sì. Le basi:

```bash
# Firewall: tutto chiuso in ingresso tranne SSH
sudo apt install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw enable

# Aggiornamenti di sicurezza automatici
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

- Accesso SSH solo con chiave: `PasswordAuthentication no` in
  `/etc/ssh/sshd_config`, poi `sudo systemctl restart ssh`.
- Docker scrive le proprie regole iptables e **scavalca ufw** per le porte
  pubblicate. Questo stack non ne pubblica nessuna: non aggiungere `ports:` a
  `compose.yaml`, in particolare a MySQL.
- `.env` resta con permessi `600` e non va mai copiato in un repository.
- Il token del bot si può revocare e rigenerare da BotFather con `/revoke`: in
  caso di dubbio fallo, aggiorna `.env` e `docker compose up -d`.

## Usare una propria immagine

Chi parte da un fork può pubblicare la propria immagine: abilita le Actions nel
fork, fai un push su `main` e la pipeline la pubblica su
`ghcr.io/<tuo-utente>/opbg-telegram-bot`. Poi rendi pubblico il pacchetto dalle
impostazioni del package su GitHub, oppure esegui `docker login ghcr.io` sul
server, e nel `.env`:

```dotenv
OPBG_IMAGE=ghcr.io/<tuo-utente-in-minuscolo>/opbg-telegram-bot
```

In alternativa costruisci l'immagine direttamente sul server dal sorgente:

```bash
git clone https://github.com/LucaTomei/opbg-telegram-bot.git
cd opbg-telegram-bot
cp .env.example .env    # e completalo come al passo 5
docker compose -f compose.yaml -f compose.dev.yaml build
docker compose up -d
```

Il file di sviluppo serve solo alla build; per aggiornare: `git pull`, di nuovo
`build` e `up -d`.

## Diagnosi

**`bot` si riavvia in continuazione.** Cerca `BOT_TOKEN_REJECTED` nei log: il
token è sbagliato o revocato.

```bash
docker compose logs --tail=200 bot
```

**`required variable ... is missing a value`.** Una variabile obbligatoria del
`.env` è vuota. Ricontrolla il passo 5.

**MySQL non diventa healthy.** Quasi sempre è memoria: sotto i 2 GB il kernel
uccide `mysqld` durante l'inizializzazione. Su una VPS piccola aggiungi swap:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

**`denied` o `unauthorized` durante il `pull`.** Il pacchetto dell'immagine non
è pubblico: esegui `docker login ghcr.io` con un token GitHub che abbia
`read:packages`, oppure costruisci l'immagine dal sorgente.

**`all predefined address pools have been fully subnetted`.** Il server ha
troppe reti Docker: la soluzione è nella
[guida Proxmox](11-deploy-proxmox.md#su-un-pct-docker-già-esistente).

**Il worker non prende lavoro, i monitoraggi non partono.** Controlla
l'heartbeat con `docker compose exec bot python -m app.cli healthcheck` e i log
del worker, poi da Telegram **Amministrazione → Stato sistema**.

Tutte le altre procedure operative (rotazione delle chiavi, pausa globale dei
monitoraggi, circuit breaker, cancellazione dati) sono nel
[runbook](10-runbook.md).
