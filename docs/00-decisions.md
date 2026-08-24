# Decisioni definitive

Queste decisioni sono già state prese e non devono essere riaperte durante l'implementazione.

| Area | Decisione |
| --- | --- |
| Accesso | Bot privato. Possono usarlo soltanto amministratori configurati e utenti entrati con un invito valido. |
| Multiutenza | Più utenti Telegram indipendenti. Ogni utente può collegare più account del portale. |
| Percorso sanitario | Solo LAPI. SSN è esplicitamente fuori ambito. |
| Ricerca | Per prestazione/prescrizione oppure per specifico medico. |
| Sedi | Selezione di una, più o tutte le sedi restituite dal portale per la ricerca corrente. |
| Prenotazione | Ricerca manuale immediata e prenotazione confermata dall'utente. |
| Monitoraggio | Modalità solo notifica, solo auto-prenotazione con esito notificato, oppure entrambe. |
| Filtri | Periodo fisso o mobile, giorni della settimana, fasce orarie, date escluse, sedi e preferenza cronologica. |
| Gestione | Creazione, modifica, duplicazione, pausa, ripresa, disabilitazione notifiche ed eliminazione. |
| Appuntamenti | Elenco, dettaglio, aggiornamento cache, disdetta con doppia conferma e download promemoria PDF. |
| Telegram UX | Reply keyboard persistente per il menu e inline keyboard per selezioni/azioni. |
| Amministrazione | L'admin vede e gestisce utenti, account, pazienti, monitoraggi, tentativi, appuntamenti, notifiche, errori e salute del sistema. Segreti mai visibili. |
| Database | MySQL 8.4 LTS. Nessun Redis. |
| Deploy | Tutto in Docker Compose. Un'unica immagine applicativa usata da servizi web, bot e worker. |
| Login portale | Pagina HTTPS temporanea; mai email/password nei messaggi Telegram. |
| Integrazione | Client HTTP e parser HTML/JSON. Nessun browser headless salvo futura decisione documentata. |
| Lingue | UI italiana; codice e commenti tecnici in inglese. |
| Pagamento LAPI | La cattura osservata indica pagamento allo sportello. Il bot non gestisce carte o pagamenti. |

## Interpretazione di “prescrizione”

Poiché il prodotto è solo LAPI, “prescrizione” nell'interfaccia indica il nome della prestazione richiesta dall'utente. Non si devono chiedere né inviare numero di ricetta, NRE, impegnativa, data prescrizione o campi SSN.

## Confini amministrativi

L'amministratore può operare su dati e configurazioni di tutti, ma:

- non può visualizzare password, cookie, CSRF, token o chiavi;
- vede inizialmente i dati personali mascherati;
- per rivelare dati completi deve usare un'azione esplicita, motivata e auditata;
- prenotare o disdire per conto di un altro utente richiede doppia conferma e produce audit;
- nessuna esportazione massiva di dati sanitari è abilitata per impostazione predefinita.

