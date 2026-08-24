# Requisiti di prodotto

## Obiettivo

Ridurre il lavoro manuale necessario per trovare e gestire appuntamenti LAPI sul portale OPBG, mantenendo isolamento degli account, controllo umano e limiti rispettosi verso il portale.

## Attori

### Utente invitato

- accetta informativa e condizioni d'uso;
- collega uno o più account del portale;
- seleziona account e paziente;
- cerca prestazioni o medici;
- consulta disponibilità e prenota;
- crea e gestisce monitoraggi;
- consulta, scarica e disdice i propri appuntamenti.

### Amministratore

- è configurato da ambiente e non necessita invito;
- genera, revoca e controlla inviti;
- abilita, sospende o blocca utenti;
- vede account, pazienti, monitoraggi, tentativi, appuntamenti, notifiche ed errori di tutti;
- esegue azioni per conto di un utente con conferma e audit;
- controlla code, limiti, circuit breaker, health e versione applicativa.

### Worker

- acquisisce in lease monitoraggi scaduti;
- rinnova o ricrea la sessione corretta;
- interroga disponibilità;
- filtra e deduplica slot;
- notifica o tenta auto-prenotazione;
- registra risultato e pianifica la prossima esecuzione.

## Funzioni utente obbligatorie

### Invito e consenso

1. `/start <invite-code>` valida codice, scadenza, revoca e numero usi.
2. L'utente accetta privacy, trattamento tecnico dei dati e natura non ufficiale del servizio.
3. Il bot registra versione e data del consenso.
4. Un utente sospeso non può eseguire chiamate al portale.

### Account del portale

- collegare account tramite link HTTPS one-time;
- assegnare un'etichetta libera, ad esempio “Mio account” o “Famiglia Rossi”;
- testare accesso;
- sincronizzare pazienti;
- rinominare, disabilitare, riabilitare e rimuovere account;
- richiedere nuova autenticazione dopo password errata/sessione revocata;
- impedire duplicati dello stesso account per il medesimo utente, senza confrontare email in chiaro.

### Prenotazione manuale

1. Selezione account.
2. Selezione paziente.
3. Scelta “Cerca prestazione” o “Cerca medico”.
4. Inserimento testo di ricerca.
5. Elenco paginato risultati.
6. Scelta risultato esatto.
7. Caricamento sedi disponibili.
8. Scelta una, più o tutte le sedi.
9. Filtri temporali opzionali.
10. Elenco slot paginato e ordinabile.
11. Riepilogo completo.
12. Doppia conferma immediatamente prima della chiamata mutante.
13. Prenotazione idempotente e verifica nell'elenco appuntamenti.
14. Invio esito, dettaglio e pulsante PDF.

### Monitoraggi

Ogni monitoraggio memorizza:

- account e paziente;
- tipo ricerca: prestazione o medico;
- query originale, risultato selezionato e identificativi correnti;
- sedi incluse o “tutte le sedi dinamiche”;
- periodo fisso `date_from/date_to` oppure finestra mobile in giorni;
- giorni della settimana inclusi;
- una o più fasce orarie;
- date escluse;
- ordinamento: prima disponibile, prima per sede preferita, data/ora crescente;
- modalità: notifica, auto-prenotazione, entrambe;
- notifiche abilitate/disabilitate;
- avvisa solo nuovi slot oppure riepiloga a ogni controllo;
- eventuali quiet hours;
- intervallo di controllo soggetto ai limiti globali;
- politica dopo successo: completa e disattiva, oppure continua se configurato esplicitamente;
- stato e motivo dell'ultimo errore.

Azioni disponibili: dettaglio, modifica, duplica, esegui ora, pausa, riprendi, abilita/disabilita notifiche, cambia modalità ed elimina.

### Notifiche

- nuovo slot compatibile;
- slot non più disponibile dopo un tentativo;
- auto-prenotazione riuscita;
- auto-prenotazione fallita o ambigua;
- credenziali da aggiornare;
- monitoraggio sospeso dal circuit breaker o dall'admin;
- appuntamento rilevato/sincronizzato;
- disdetta riuscita/fallita;
- PDF non disponibile.

Le notifiche devono essere deduplicate e rispettare quiet hours, salvo esiti di prenotazione/disdetta e avvisi di sicurezza.

### Appuntamenti

- sincronizzare dal portale su richiesta e periodicamente;
- elencare prossimi appuntamenti e storico se disponibile;
- mostrare prestazione, medico, paziente, sede, data, ora, stato e avvisi;
- scaricare PDF senza conservarlo permanentemente;
- disdire con due conferme separate e nonce a scadenza;
- verificare la scomparsa/cambio stato dopo disdetta;
- impedire doppia disdetta dovuta a callback ripetuti.

## Filtri temporali

- `Dal` e `Al` con validazione timezone Europe/Rome;
- finestra mobile, ad esempio “da oggi a +30 giorni”;
- lunedì–domenica selezionabili;
- fasce multiple, ad esempio 08:00–12:30 e 16:00–19:30;
- date singole escluse;
- opzione “qualsiasi ora”;
- opzione “non prima di X ore da adesso”;
- ordinamento e numero massimo di proposte mostrate.

## Sedi

- Le sedi arrivano dalla risposta del portale e non devono essere hardcoded.
- La selezione può contenere una o più sedi.
- “Tutte” è dinamico: include anche nuove sedi restituite in futuro.
- Se una sede salvata non viene più restituita, il monitoraggio continua sulle altre e segnala l'anomalia una sola volta.
- È possibile impostare un ordine di preferenza senza escludere le sedi secondarie.

## Requisiti non funzionali

- timezone Europe/Rome e gestione corretta DST;
- disponibilità 24/7 salvo manutenzione;
- restart-safe: stato conversazioni, job, idempotenza e lease persistiti;
- nessun singolo errore account deve bloccare gli altri;
- log strutturati e redatti;
- metriche minime e health check;
- backup cifrati e procedura restore testata;
- cancellazione account e dati correlati;
- accessibilità testuale: nessuna informazione affidata soltanto a emoji.

## Fuori ambito

- SSN e ricetta elettronica;
- pagamenti o memorizzazione carte;
- registrazione libera/pubblica;
- aggiramento di protezioni del portale;
- modifica/riprogrammazione tramite endpoint non catturato;
- diagnosi o consiglio medico;
- vendita o uso commerciale senza ulteriore valutazione legale/privacy.

