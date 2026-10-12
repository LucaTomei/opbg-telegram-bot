# Deploy su un container LXC di Proxmox

Guida per portare lo stack su un PCT (container LXC) di un nodo Proxmox, ad
esempio un Minisforum MS-01. Il bot usa long polling: non serve nessuna porta
in ingresso, nessun dominio, nessun certificato. Serve solo traffico in uscita
verso `api.telegram.org` e verso il portale.

- [Deploy senza repository](#deploy-senza-repository)
- [Deploy dal repository](#deploy-dal-repository)
- [Su un PCT Docker già esistente](#su-un-pct-docker-già-esistente)
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

## Su un PCT Docker già esistente

Se hai già un container con Docker e una cartella di stack, i passi 1-3 non ti
servono: parti da [Deploy senza repository](#deploy-senza-repository), oppure
dal passo 4 se vuoi costruire l'immagine sull'host. In entrambi i casi, due
accortezze.

**Spazio negli indirizzi Docker.** Ogni stack crea una rete bridge e Docker le
assegna una `/16` da un pool predefinito che ne contiene sedici. Su un host con
molti stack l'avvio fallisce con `all predefined address pools have been fully
subnetted`. Conta le reti e, se sei vicino al limite, allarga il pool:

```bash
docker network ls | wc -l
cat > /etc/docker/daemon.json <<'JSON'
{ "default-address-pools": [{ "base": "172.20.0.0/14", "size": 24 }] }
JSON
systemctl restart docker
```

`size: 24` dà 1024 reti invece di 16. Il riavvio del demone ferma e riavvia
tutti i container dell'host: fallo in una finestra tranquilla.

**File di Compose.** Non serve nessun accorgimento: gli override di sviluppo
stanno in `compose.dev.yaml`, che Compose non carica da solo. `docker compose
up -d` usa solo `compose.yaml`, non pubblica porte e applica le migrazioni prima
di avviare `bot` e `worker`.

## Deploy senza repository

La pipeline pubblica l'immagine su GitHub Container Registry a ogni push su
`main` che superi i test, con due tag: `latest` e `sha-<commit>`. Un
deployment la scarica e basta: niente sorgente sull'host, niente build, niente
compilatore. È il modo consigliato, e l'unico praticabile da un'interfaccia
grafica come Arcane, Portainer o Dockge.

### Credenziali del registry

Se il pacchetto è pubblico non serve nessun login. Se è privato, per esempio
quello pubblicato da un fork, l'host deve autenticarsi una volta sola con un
token GitHub che abbia il solo permesso `read:packages`:

```bash
echo '<token>' | docker login ghcr.io -u <utente-github> --password-stdin
```

Le credenziali finiscono in `~/.docker/config.json` e non scadono finché il
token è valido.

### Lo stack

Servono due file nella cartella del progetto, `compose.yaml` e `.env`. Il primo
è quello del repository, usato tale e quale: non contiene nulla di specifico
per una macchina. Puoi farlo clonare all'interfaccia, oppure incollarlo nel suo
editor — in quel caso ricorda che una copia incollata non si aggiorna da sola.

Il secondo contiene i segreti, generati sull'host come descritto in
[5. Generare i segreti](#5-generare-i-segreti). Due variabili facoltative
scelgono l'immagine:

```dotenv
OPBG_IMAGE=ghcr.io/lucatomei/opbg-telegram-bot
OPBG_TAG=latest
```

`OPBG_TAG=sha-<commit>` blocca lo stack su una versione esatta ed è il modo
per tornare indietro.

### Avvio e aggiornamento

```bash
docker compose pull
docker compose up -d
```

`up` esegue `migrate` e ne aspetta la fine prima di avviare `bot` e `worker`,
quindi funziona anche al primo avvio su un database vuoto. Un aggiornamento è
la stessa coppia di comandi: `pull` porta il nuovo `latest`, `up -d` applica le
migrazioni e ricrea i container cambiati.

Dall'interfaccia corrispondono al pulsante di redeploy con l'opzione «pull
image» attiva. **Senza quell'opzione il redeploy riavvia l'immagine vecchia**:
Compose non riscarica un tag che ha già in locale.

### Backup senza repository

Gli script di backup e restore viaggiano dentro l'immagine, quindi il profilo
`backup` funziona anche qui:

```bash
docker compose --profile backup run --rm backup
```

Se l'interfaccia non gestisce i profili, il job si lancia da solo, senza
Compose, purché veda la rete e il volume dello stack:

```bash
docker run --rm \
  --network opbg-telegram-bot_backend \
  --volume opbg-telegram-bot_backup-data:/backups \
  --env-file /percorso/dello/stack/.env \
  --env MYSQL_ROOT_PASSWORD \
  --entrypoint /opt/backup/backup.sh \
  ghcr.io/lucatomei/opbg-telegram-bot:latest
```

## Deploy dal repository

Serve solo per sviluppare sull'host o per costruire un'immagine che non passi
dal registry. In quel caso il file di sviluppo va richiesto esplicitamente,
perché aggiunge la build dal sorgente:

```bash
docker compose -f compose.yaml -f compose.dev.yaml build
docker compose -f compose.yaml -f compose.dev.yaml up -d
```

`compose.yaml` da solo non costruisce niente: punta all'immagine pubblicata.

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

> Questo passo serve solo se vuoi costruire l'immagine sull'host. Per il modo
> consigliato salta a [Deploy senza repository](#deploy-senza-repository): non
> serve nessun clone.

Il repository è pubblico: basta clonarlo nella cartella degli stack.

```bash
git clone https://github.com/LucaTomei/opbg-telegram-bot.git \
  /opt/docker/stacks/opbg-telegram-bot
cd /opt/docker/stacks/opbg-telegram-bot
```

## 5. Generare i segreti

```bash
cp .env.example .env
python3 scripts/generate_keys.py > /tmp/keys.env
while IFS='=' read -r name value; do
  sed -i "s|^${name}=.*|${name}=${value}|" .env
done < /tmp/keys.env
shred -u /tmp/keys.env
```

Lo script riempie `APP_SECRET_KEY`, `APP_ENCRYPTION_KEY`, `APP_BLIND_INDEX_KEY`,
`MYSQL_PASSWORD` e `MYSQL_ROOT_PASSWORD` al loro posto, senza duplicare righe.
Se nel container non c'è Python, `openssl` produce lo stesso formato:

```bash
for name in APP_SECRET_KEY APP_ENCRYPTION_KEY APP_BLIND_INDEX_KEY \
            MYSQL_PASSWORD MYSQL_ROOT_PASSWORD; do
  value="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=')"
  sed -i "s|^${name}=.*|${name}=${value}|" .env
done
```

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
docker compose -f compose.yaml -f compose.dev.yaml build
docker compose up -d
```

`up` esegue `migrate` e aspetta che finisca prima di avviare `bot` e `worker`,
quindi un solo comando basta anche al primo avvio. Il file di sviluppo serve
solo alla build: l'avvio usa `compose.yaml` da solo.

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
healthy, con la colonna delle porte vuota. Controllo esplicito:

```bash
docker compose ps --format '{{.Service}}  {{.Status}}  [{{.Ports}}]'
```

Le parentesi devono essere vuote per tutti e tre. Se `bot` continua a riavviarsi, cerca
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
30 3 * * * cd /opt/docker/stacks/opbg-telegram-bot && docker compose --profile backup run --rm backup >> /var/log/opbg-backup.log 2>&1
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

La procedura di restore è in [`10-runbook.md`](10-runbook.md#restore).

## 9. Aggiornare

```bash
cd /opt/docker/stacks/opbg-telegram-bot
docker compose --profile backup run --rm backup                  # 1. backup
git pull                                                         # 2. sorgente
docker compose -f compose.yaml -f compose.dev.yaml build         # 3. immagine
docker compose up -d                                             # 4. restart
docker compose ps                                                # 5. health
```

Per tornare indietro: `git checkout <commit>`, ricostruisci e riavvia. Non fare
un downgrade del database senza averlo pianificato.

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
