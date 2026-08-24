# Monitoraggio e auto-prenotazione

## Ciclo worker

1. Seleziona monitoraggi `ACTIVE` con `next_check_at <= now` e lease libero/scaduto.
2. Acquisisce lease in transazione.
3. Verifica stato utente, account, paziente e circuit breaker.
4. Carica/valida sessione portale per l'account.
5. Riesegue la ricerca salvata per prestazione o medico.
6. Risolve sedi dinamiche/correnti.
7. Recupera disponibilità.
8. Normalizza, filtra e ordina slot.
9. Confronta con `seen_slots`.
10. Notifica e/o tenta auto-prenotazione.
11. Registra esito, calcola `next_check_at`, rilascia lease.

Il worker deve completare gli altri account anche se uno fallisce.

## Ricerca persistente

Non affidarsi per sempre agli ID interni osservati. Salvare query e risultato selezionato; a intervalli o in caso di errore, rifare autocomplete e riconciliare per descrizione normalizzata. Se più risultati diventano ambigui, sospendere il monitor e chiedere scelta all'utente.

### Per prestazione

- Rifare `/service-autocomplete`.
- Selezionare la prestazione esatta memorizzata.
- Recuperare sedi/slot.

### Per medico

- Rifare `/medici-autocomplete`.
- Selezionare il medico esatto memorizzato.
- Recuperare prestazione/sessione collegata e sedi/slot.
- Uno slot deve rispettare il medico quando il portale fornisce l'informazione; non degradare silenziosamente a qualsiasi medico.

## Applicazione filtri

Ordine deterministico:

1. periodo fisso o rolling window;
2. lead time minimo;
3. date escluse;
4. weekday;
5. fasce orarie;
6. sedi incluse;
7. medico/prestazione esatta;
8. ordinamento sede preferita, data, ora;
9. limite risultati.

Usare timezone Europe/Rome e convertire a UTC per storage/confronti.

## Modalità notifiche

### `NOTIFY`

- Non esegue mutazioni sul portale.
- Notifica soltanto slot nuovi se `notify_only_changes=true`.
- Inline: `Prenota ora`, `Dettagli`, `Silenzia`, `Pausa`.
- `Prenota ora` deve ricaricare e revalidare lo slot.

### `AUTOBOOK`

- Tenta il primo slot secondo filtri/ordinamento.
- Invia sempre esito di successo, fallimento critico o ambiguità anche se le normali notifiche sono disabilitate.
- Non invia notifiche di semplice disponibilità se non richiesto.

### `AUTOBOOK_NOTIFY`

- Notifica nuovi slot e tenta quello migliore.
- Deduplica il messaggio di disponibilità e quello di successo.

## Protocollo auto-book idempotente

1. Calcolare `slot_fingerprint` e `idempotency_key`.
2. Creare `booking_attempt` unique in transazione; se già presente e concluso, non ripetere.
3. Acquisire lock sull'account.
4. Recuperare nuovamente disponibilità.
5. Accertare che lo stesso slot sia ancora presente e rispetti filtri correnti.
6. Salvare snapshot degli appuntamenti.
7. Inviare scelta slot.
8. Inviare una sola volta `confirm=true`.
9. Ricaricare appuntamenti e riconciliare.
10. Segnare `SUCCEEDED`, `SLOT_LOST`, `REJECTED` o `AMBIGUOUS`.
11. Su `AMBIGUOUS`, mai reinviare conferma; notificare utente/admin.
12. Su successo, completare/disattivare il monitor salvo politica esplicita diversa.

## Pianificazione e limiti

- Default 300 secondi.
- Minimo globale configurabile, non inferiore a 120 secondi.
- Jitter per evitare raffiche.
- Backoff esponenziale su rete/5xx/429 fino al massimo configurato.
- Limite chiamate concorrenti globale e una sessione attiva per account.
- `Esegui ora` rispetta limite e non può essere spammato.
- Più monitor simili sullo stesso account possono condividere una lettura disponibilità nella stessa finestra breve, senza condividere dati tra account.

## Circuit breaker

- Per account: auth/contratto ripetutamente fallito sospende solo quell'account.
- Globale: molti errori di schema/5xx arrestano temporaneamente monitoraggi per proteggere portale e dati.
- Admin può vedere, aprire/chiudere solo con motivo auditato.
- Alla riapertura usare half-open con una singola richiesta idempotente.

## Notifiche e deduplica

- Dedup key include evento, watch, slot/attempt e destinatario.
- Retry Telegram usa backoff e rispetta rate limit.
- Quiet hours sopprimono/rinviano eventi informativi, non esiti mutanti o richieste re-auth urgenti.
- Disabilitare notifiche non disattiva automaticamente l'auto-book; UI deve mostrare chiaramente la distinzione.
- Pausare monitor disattiva sia check sia auto-book, ma conserva configurazione.
- Eliminare richiede conferma ed è irreversibile nell'UI.

## Race conditions

- Lo slot può sparire in qualunque momento; è un esito normale.
- Due monitor dello stesso account non possono prenotare contemporaneamente.
- Due utenti diversi restano indipendenti; il portale decide chi ottiene lo slot.
- Un update Telegram duplicato non genera un secondo attempt.
- Worker morto viene recuperato dopo lease expiry senza ripetere un POST finale non riconciliato.

