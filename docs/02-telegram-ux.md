# UX Telegram

## Reply keyboard persistente

La tastiera principale deve restare disponibile salvo durante l'inserimento sicuro di dati non sensibili.

```text
🗓 Prenota              🔔 Monitoraggi
📅 Appuntamenti         👤 Account
⚙️ Impostazioni         ℹ️ Aiuto
```

Per gli amministratori aggiungere:

```text
🛡 Amministrazione
```

Comandi minimi: `/start`, `/menu`, `/cancel`, `/help`, `/privacy`, `/status`. Comandi admin: `/invite`, `/admin`.

## Regole inline keyboard

- Tutte le selezioni contestuali usano inline keyboard.
- Ogni pagina include `◀️ Indietro`, `🏠 Menu` e, quando applicabile, `❌ Annulla`.
- Risultati, utenti, account e monitoraggi sono paginati.
- Callback data usa un prefisso breve, un ID opaco e un'azione; niente email, tax code, nomi, date sanitarie o segreti.
- Il server ricarica l'oggetto da MySQL e ricontrolla proprietario, ruolo, stato e scadenza.
- I bottoni mutanti vengono invalidati dopo il primo uso.

## Prenotazione manuale

### Schermata 1 — Account

- Elenco account attivi con stato autenticazione.
- `➕ Collega account` avvia la richiesta guidata di etichetta, email e password.
- Se un solo account attivo, può essere pre-selezionato ma il riepilogo deve mostrarlo.

### Schermata 2 — Paziente

- Elenco pazienti sincronizzati dal portale.
- Pulsante aggiorna.
- Nessun tax code mostrato integralmente.

### Schermata 3 — Tipo ricerca

```text
🔎 Cerca prestazione
🩺 Cerca medico
```

Il testo inserito è una query, non viene trattato come identificativo definitivo.

### Schermata 4 — Risultati

- Riga chiara con nome/descrizione.
- Per prestazioni mostra prenotabilità online e preparazione se disponibile.
- Per medici mostra titolo e nominativo restituito dal portale.
- Richiedere una selezione esatta; mai scegliere silenziosamente il primo risultato.

### Schermata 5 — Sedi

- `✅ Tutte le sedi` oppure toggle multipli.
- Mostrare quante disponibilità correnti risultano per sede se noto.
- Consentire ordinamento di preferenza in una schermata separata.

### Schermata 6 — Filtri

- `📆 Periodo`
- `🗓 Giorni settimana`
- `🕐 Fasce orarie`
- `🚫 Date escluse`
- `↕️ Ordinamento`
- `✅ Mostra disponibilità`

La prenotazione manuale può saltare i filtri e mostrare tutti gli slot correnti.

### Schermata 7 — Slot

Ogni bottone include data, ora e abbreviazione sede. Il messaggio contiene dettaglio completo. Paginare e offrire `Aggiorna`.

### Schermata 8 — Conferma

Mostrare account, paziente, tipo ricerca, prestazione, medico se noto, sede, data e ora. Prima conferma: `Procedi`; seconda conferma separata e a scadenza: `Conferma prenotazione`.

### Esito

- Successo: dettaglio, ID mascherato, `📄 Scarica promemoria`, `📅 I miei appuntamenti`.
- Slot perso: messaggio chiaro e ritorno agli slot aggiornati.
- Esito ambiguo: nessun retry automatico, sincronizzazione elenco e istruzioni.

## Creazione monitoraggio

Riutilizza account, paziente, ricerca, sedi e filtri della prenotazione. Poi richiede:

1. modalità `Solo notifica`, `Auto-prenota`, `Auto-prenota + notifica`;
2. notifiche attive o silenziose;
3. frequenza consentita;
4. comportamento al successo;
5. riepilogo e doppia conferma se auto-prenotazione è attiva.

## Lista monitoraggi

Ogni voce mostra stato, ricerca, paziente/account mascherati, periodo, sedi, ultima verifica e prossima verifica.

Inline actions:

- `Dettagli`
- `Esegui ora`
- `Modifica`
- `Duplica`
- `Pausa` / `Riprendi`
- `Notifiche ON/OFF`
- `Auto-prenota ON/OFF`
- `Elimina` con doppia conferma

## Appuntamenti

Mostrare card testuali paginated. Inline actions:

- `Dettagli`
- `📄 PDF`
- `Disdici`
- `Aggiorna`

Disdetta:

1. mostrare il dettaglio;
2. chiedere “Vuoi procedere?”;
3. generare una seconda conferma con scadenza;
4. effettuare la chiamata una volta;
5. risincronizzare e comunicare esito.

## Account

- collega;
- sincronizza pazienti;
- test accesso;
- rinomina;
- aggiorna credenziali;
- disabilita;
- rimuovi con indicazione dei monitoraggi che verranno sospesi.

## Impostazioni

- timezone informativa;
- quiet hours;
- notifiche globali;
- preferenze pagina/numero slot;
- privacy e consensi;
- elimina tutti i propri dati.

## Amministrazione

Reply entry `🛡 Amministrazione`, poi inline:

- `Utenti`
- `Inviti`
- `Account portale`
- `Pazienti`
- `Monitoraggi`
- `Tentativi prenotazione`
- `Appuntamenti cache`
- `Notifiche`
- `Errori e circuit breaker`
- `Code e lease`
- `Audit`
- `Stato sistema`

Ogni lista ha filtri, ricerca e paginazione. Dati inizialmente mascherati. Azioni per conto altrui richiedono motivo, doppia conferma e audit.

## Messaggi e concorrenza

- Modificare il messaggio esistente quando possibile per non inondare la chat.
- Rispondere ai callback immediatamente per evitare spinner infiniti.
- Ignorare in modo idempotente callback già consumati.
- `/cancel` annulla solo la conversazione corrente, mai un monitoraggio o appuntamento.
- Errori tecnici includono un correlation ID, non stack trace o payload del portale.

