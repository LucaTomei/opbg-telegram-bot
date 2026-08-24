# Deploy su un container LXC di Proxmox

Guida per portare lo stack su un PCT (container LXC) di un nodo Proxmox, ad
esempio un Minisforum MS-01. Il bot usa long polling: non serve nessuna porta
in ingresso, nessun dominio, nessun certificato. Serve solo traffico in uscita
verso `api.telegram.org` e verso il portale.

- [1. Creare il container](#1-creare-il-container)
- [2. Preparare il sistema](#2-preparare-il-sistema)
- [3. Installare Docker](#3-installare-docker)
- [4. Portare il codice nel container](#4-portare-il-codice-nel-container)
- [5. Generare i segreti](#5-generare-i-segreti)
- [6. Primo avvio](#6-primo-avvio)
- [7. Verificare](#7-verificare)
- [8. Backup](#8-backup)
- [9. Aggiornare](#9-aggiornare)
- [Diagnosi](#diagnosi)

## 1. Creare il container

Dalla shell del nodo Proxmox.

Scarica un template Debian se non è già presente:

```bash
pveam update
pveam available --section system | grep debian-13
pveam download local debian-13-standard_13.0-1_amd64.tar.zst
```

Crea il container. `nesting=1` e `keyctl=1` servono a Docker: senza il primo il
demone non parte, senza il secondo fallisce la gestione del keyring.

```bash
pct create 130 local:vztmpl/debian-13-standard_13.0-1_amd64.tar.zst \
  --hostname opbg-bot \
  --unprivileged 1 \
  --features nesting=1,keyctl=1 \
  --cores 2 \
  --memory 4096 \
  --swap 1024 \
  --rootfs local-lvm:24 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp \
  --onboot 1 \
  --password
```

Dimensionamento: 2 vCPU e 4 GB di RAM sono comodi per MySQL più due processi
Python; il minimo praticabile è 2 GB. 24 GB di disco lasciano spazio a
database, immagini Docker e backup.

Avvia ed entra:

```bash
pct start 130
pct enter 130
```

Il `--password` chiesto in fase di creazione è la password di root del
container. Se preferisci l'accesso via `pct enter` dal nodo, puoi non
installare affatto SSH.

## 2. Preparare il sistema

Dentro il container:

```bash
apt update && apt full-upgrade -y
apt install -y ca-certificates curl git python3 rsync
timedatectl set-timezone Europe/Rome
```

Crea un utente non privilegiato che possiederà il deploy:

```bash
adduser --disabled-password --gecos "" opbg
```

## 3. Installare Docker

```bash
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg \
  -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
usermod -aG docker opbg
systemctl enable --now docker
```

Controlla subito lo storage driver:

```bash
docker info --format '{{.Driver}}'
```

Deve rispondere `overlay2`. Se risponde `vfs` lo stack funziona ma consuma
molto disco: vedi [Diagnosi](#diagnosi).

## 4. Portare il codice nel container

Con una deploy key di sola lettura, generata **dentro** il container:

```bash
su - opbg
ssh-keygen -t ed25519 -C "opbg-bot@ms01" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub
```

Registra la chiave pubblica su GitHub in *Settings → Deploy keys* del
repository, senza permesso di scrittura, poi:

```bash
git clone git@github.com:LucaTomei/opbg-telegram-bot.git ~/opbg-telegram-bot
cd ~/opbg-telegram-bot
```

In alternativa, senza deploy key, copia il progetto dal tuo Mac:

```bash
rsync -av --exclude .git --exclude .venv --exclude .env \
  ./ root@<ip-del-container>:/home/opbg/opbg-telegram-bot/
```

## 5. Generare i segreti

```bash
cp .env.example .env
python3 scripts/generate_keys.py >> .env
```

Lo script stampa `APP_SECRET_KEY`, `APP_ENCRYPTION_KEY`, `APP_BLIND_INDEX_KEY`,
`MYSQL_PASSWORD` e `MYSQL_ROOT_PASSWORD`. Finiscono in fondo al file e hanno
precedenza sulle righe vuote copiate dall'esempio; per pulizia, cancella le
cinque righe vuote iniziali.

Completa a mano:

```dotenv
TELEGRAM_BOT_TOKEN=<il token di @BotFather>
TELEGRAM_BOT_USERNAME=<username del bot, senza @>
TELEGRAM_ADMIN_IDS=<il tuo ID Telegram numerico>
BACKUP_PASSPHRASE=<passphrase lunga, diversa dalle chiavi>
```

Il tuo ID numerico te lo dice un bot come `@userinfobot`. `APP_ENV` deve
restare `production`.

Proteggi il file e mettilo al sicuro **fuori** dal container:

```bash
chmod 600 .env
```

> `APP_ENCRYPTION_KEY` e `APP_BLIND_INDEX_KEY` non sono recuperabili: perderle
> significa perdere credenziali e pazienti già salvati. Conservale in un
> password manager, non nello stesso posto dei backup del database.

## 6. Primo avvio

```bash
docker compose -f compose.yaml build
docker compose -f compose.yaml run --rm migrate
docker compose -f compose.yaml up -d
```

`-f compose.yaml` è importante: senza, Compose applica anche
`compose.override.yaml`, che è il profilo di sviluppo e pubblica MySQL su
`127.0.0.1:13306`. In produzione nessun servizio deve pubblicare porte.

Per non ripeterlo a ogni comando:

```bash
echo 'export COMPOSE_FILE=compose.yaml' >> ~/.profile
```

I container hanno `restart: unless-stopped` e Docker parte al boot, quindi il
container LXC creato con `--onboot 1` riporta su tutto da solo dopo un riavvio
del nodo.

## 7. Verificare

```bash
docker compose ps
docker compose exec bot python -m app.cli healthcheck
docker compose logs --tail=100 bot worker
```

`docker compose ps` deve mostrare `mysql`, `bot` e `worker` in `running` e
healthy, e nessuna porta pubblicata. Se `bot` continua a riavviarsi, cerca
`BOT_TOKEN_REJECTED` nei log: il token è sbagliato.

Poi da Telegram:

1. invia `/start` al bot: essendo il tuo ID in `TELEGRAM_ADMIN_IDS` entri come
   amministratore;
2. accetta l'informativa;
3. `/invite` per generare un codice da consegnare a un altro utente;
4. **Account → Collega account** per collegare il primo account del portale.

## 8. Backup

Il job di backup produce un dump compresso e cifrato nel volume `backup-data`:

```bash
docker compose --profile backup run --rm backup
```

Pianificalo dal container, come utente `opbg`:

```cron
30 3 * * * cd /home/opbg/opbg-telegram-bot && COMPOSE_FILE=compose.yaml docker compose --profile backup run --rm backup >> /home/opbg/backup.log 2>&1
```

Copia gli archivi fuori dal container, altrimenti un guasto del PCT si porta
via anche i backup:

```bash
docker run --rm -v opbg-telegram-bot_backup-data:/backups -v /mnt/nas:/out \
  alpine sh -c 'cp -n /backups/*.enc /out/'
```

Aggiungi il container ai backup di Proxmox (*Datacenter → Backup*): uno
snapshot notturno dà un ripristino completo in pochi minuti. La passphrase dei
backup e le chiavi applicative non devono stare nello stesso archivio dei dati.

La procedura di restore è in [`11-runbook.md`](11-runbook.md#restore).

## 9. Aggiornare

```bash
cd ~/opbg-telegram-bot
docker compose --profile backup run --rm backup   # 1. backup verificato
git pull                                          # 2. nuovo codice
docker compose build                              # 3. nuova immagine
docker compose run --rm migrate                   # 4. migrazioni
docker compose up -d                              # 5. restart
docker compose ps                                 # 6. health
```

Per tornare indietro: `git checkout <tag-precedente>`, ricostruisci e riavvia.
Non fare un downgrade del database senza averlo pianificato.

Ogni tanto libera spazio dalle immagini vecchie:

```bash
docker image prune -f
```

## Diagnosi

**Docker non parte, `failed to start daemon`.** Mancano `nesting` o `keyctl`.
Dal nodo Proxmox:

```bash
pct set 130 --features nesting=1,keyctl=1
pct reboot 130
```

**`docker info` dice `vfs`.** Il container non riesce a usare overlayfs. Usa
fuse-overlayfs:

```bash
apt install -y fuse-overlayfs
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'JSON'
{ "storage-driver": "fuse-overlayfs" }
JSON
systemctl restart docker
```

**Errori di AppArmor all'avvio dei container.** Alcuni host richiedono un
profilo permissivo per il PCT. Sul nodo, in
`/etc/pve/lxc/130.conf`:

```text
lxc.apparmor.profile: unconfined
```

Riavvia il container. È un allentamento dell'isolamento: usalo solo se
necessario.

**MySQL non diventa healthy.** Quasi sempre è memoria: con meno di 2 GB il
container viene ucciso durante l'inizializzazione. Alza la RAM del PCT
(`pct set 130 --memory 4096`) e riparti da `docker compose up -d`.

**Il worker non prende lavoro.** Controlla l'heartbeat:

```bash
docker compose exec bot python -m app.cli healthcheck
docker compose logs worker --tail=100
```

Poi, da Telegram, **Amministrazione → Stato sistema**.
