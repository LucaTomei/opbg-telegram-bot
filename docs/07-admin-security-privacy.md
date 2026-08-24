# Amministrazione, sicurezza e privacy

## Inviti e ruoli

- Gli admin arrivano da `TELEGRAM_ADMIN_IDS` e vengono upsertati.
- Inviti casuali ad alta entropia, hashati, con scadenza, limite usi, revoca e nota.
- Nessuna registrazione senza invito.
- Middleware di autorizzazione su ogni messaggio e ogni callback.
- Ruoli iniziali: `USER`, `ADMIN`; niente controllo basato su username Telegram.

## Vista amministrativa completa

L'admin deve poter consultare:

- utenti e stato/consenso;
- account portale, stato login, ultimo sync ed errori;
- pazienti collegati;
- monitoraggi, filtri, stato, ultima/prossima esecuzione;
- slot visti e notifiche;
- tentativi di prenotazione e outcome;
- appuntamenti cache e disdette;
- errori portale, Telegram e sistema;
- job, lease, heartbeat, circuit breaker e versione.

Filtri minimi: utente, account, stato, intervallo data, tipo ricerca e correlation ID.

Azioni admin:

- genera/revoca inviti;
- sospendi/riattiva/blocca utente;
- disabilita/riabilita account;
- pausa/riprendi/esegui monitor;
- disabilita notifiche;
- forza sync non distruttivo;
- avvia prenotazione/disdetta per conto dell'utente solo con motivo e doppia conferma;
- risolvi/annota evento di sistema;
- controlla circuit breaker.

## Mascheramento e rivelazione

- Liste mostrano dati mascherati.
- `Mostra dati completi` richiede conferma, motivo non vuoto e genera audit.
- La rivelazione ha durata breve e non viene mantenuta nella reply keyboard.
- Password, cookie, token, CSRF, encryption key e blind-index key non sono mai rivelabili.
- Evitare messaggi contenenti aggregazioni massive di dati sanitari.

## Audit

Registrare in modo append-only:

- login/admin start;
- reveal PII;
- creazione/revoca inviti;
- cambi stato utente/account/watch;
- azioni per conto altrui;
- prenotazioni/disdette admin;
- override circuit breaker;
- cancellazione dati;
- cambio configurazione sensibile.

Audit include chi, cosa, target interno, motivo, timestamp, correlation ID e hash before/after; mai il segreto.

## Collegamento account

1. L'utente avvia il collegamento da **Account → Collega account**.
2. Il bot chiede etichetta, email e password in passi separati.
3. Il messaggio con la password viene cancellato dalla chat appena letto; se la
   cancellazione fallisce l'utente viene avvisato di eliminarlo a mano.
4. La password non viene mai scritta nello stato conversazione: vive solo per la
   durata della verifica.
5. Il login viene verificato sul portale prima di salvare qualsiasi cosa.
6. Solo dopo la verifica credenziali e sessione vengono cifrate e i pazienti
   sincronizzati.
7. Tentativi e fallimenti sono limitati per utente; superata la soglia il flusso
   si blocca e l'utente viene avvisato.

## Cifratura

- Authenticated encryption con chiave applicativa esterna al DB.
- Nonce unico generato dal sistema per ogni cifratura.
- Associated data include tabella, record ID, campo e key version.
- Blind index con chiave separata derivata tramite KDF.
- Supportare key version e procedura di rotazione.
- Startup rifiuta chiave di esempio/debole.
- Backup non contiene la chiave nello stesso luogo.

## Logging e redazione

Filtro centralizzato e testato per:

- Authorization/Cookie/Set-Cookie;
- password e campi form;
- email, tax code, token e CSRF;
- query `tax_code`;
- HTML/JSON del portale;
- nomi di pazienti/medici quando non necessari.

Non abilitare debug HTTP in produzione. Eccezioni memorizzano codici e hash/fingerprint, non raw response.

## Telegram e PDF

Le chat bot Telegram non sono un canale end-to-end. Informare gli utenti.

- Il promemoria viene inviato come documento su richiesta esplicita, senza salvataggio locale permanente.
- Mai inviare PDF automaticamente a gruppi o chat diverse dall'utente proprietario.
- Admin può scaricarlo per conto altrui soltanto tramite azione auditata.

## Privacy e retention

- Minimizzazione dei dati.
- Consenso/versione informativa prima di collegare account.
- Funzione esporta impostazioni non sensibili dell'utente.
- Funzione elimina account e funzione elimina profilo.
- Retention configurabile per eventi, notifiche e audit.
- PDF mai persistiti.
- Backup cifrati, retention limitata, restore controllato.

## Rate limit e difese

- Rate limit per utente Telegram sui tentativi di collegamento.
- Validazione di etichetta, email e password prima di contattare il portale.
- Errori identici per account inesistente e password errata.
- Dopo ripetuti fallimenti il flusso viene bloccato e l'utente notificato.

## HAR e segreti

- `.gitignore` blocca `*.har`, capture, `.env`, PDF e backup.
- CI fallisce se trova file HAR o pattern di segreti/dati fiscali.
- I fixture sono sintetici.
- La password usata nelle catture originali deve essere cambiata e le sessioni terminate.

