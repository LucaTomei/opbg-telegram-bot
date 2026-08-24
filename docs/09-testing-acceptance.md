# Strategia test e criteri di accettazione

## Regole

- Nessun test CI accede al portale reale.
- Nessun test crea o cancella appuntamenti reali.
- Fixture esclusivamente sintetici.
- Test di integrazione usano MySQL Docker usa-e-getta.
- I test verificano idempotenza, autorizzazione, redazione e race condition, non soltanto happy path.

## Unit test

### Portal parser/client

- CSRF da input e meta;
- default form senza valori hardcoded;
- login success/failure/redirect inatteso;
- pazienti da select;
- prestazioni JSON complete/mancanti/vuote;
- medici JSON complete/mancanti/vuote;
- disponibilità JSON e HTML server-rendered;
- più sedi e doctor mapping;
- normalizzazione date/ora Europe/Rome;
- lista appuntamenti label-based con campi riordinati;
- cancel/pdf link parsing;
- PDF content-type, magic bytes e size;
- schema cambiato genera `PortalContractChanged` redatto.

### Filtri

- intervallo inclusivo;
- rolling window;
- DST Europe/Rome;
- weekday mask;
- fasce multiple e confini;
- date escluse;
- sedi selected/all dynamic;
- ordine preferenza;
- doctor/service exact match;
- fingerprint deterministico.

### Sicurezza

- encryption roundtrip/tamper/key version;
- blind index e separazione tenant;
- token hash/expiry/single use;
- callback ownership;
- nonce destructive single use;
- log redaction per password/cookie/token/email/tax code/HTML;
- admin non può vedere segreti.

## Integration test MySQL

- migrazione da zero;
- repository CRUD e constraints;
- lease concorrenti: un solo worker acquisisce;
- optimistic locking;
- booking idempotency unique;
- notification dedup;
- cascade/soft delete corretti;
- admin audit append-only;
- conversation state sopravvive restart simulato;
- re-auth sospende/riprende watch.

## Bot test

- menu reply per user/admin;
- invito valido/scaduto/revocato/usato;
- consenso;
- multi-account e patient selection;
- flussi service/doctor con pagination;
- scelta sedi multipla/all;
- editor completo filtri;
- monitor CRUD e notifiche toggle;
- callback duplicata;
- back/menu/cancel da ogni stato;
- double confirmation prenotazione/disdetta;
- admin authorization e audit;
- errori user-friendly senza leak.

## Worker test

- scheduling+jitter;
- backoff 429/5xx/network;
- account isolation;
- notification-only;
- auto-book successo;
- slot perso;
- booking ambiguo senza retry;
- worker crash tra POST e reconcile;
- circuit breaker account/globale;
- pausa/disabilita/elimina durante lease;
- quiet hours e notifiche critiche.

## Web test

- ticket connect valido/scaduto/consumato;
- CSRF, SameSite, CSP, no-store;
- login riuscito e account cifrato;
- password errata senza enumeration;
- rate limit;
- re-auth;
- link PDF one-time: owner, expiry, single use;
- nessun analytics/asset remoto.

## Contract test live opt-in

Solo manualmente con variabili esplicite:

- login e logout/verify;
- lettura account/pazienti;
- autocomplete servizio/medico;
- lettura disponibilità;
- lista appuntamenti;
- download PDF di appuntamento esplicitamente selezionato.

Prenotazione/disdetta live sono escluse dalla suite automatica. Se validate manualmente, richiedono flag distruttivo, ID utente/account esplicito e prompt umano fuori dalla CI.

## Acceptance criteria principali

| ID | Criterio |
| --- | --- |
| AC-001 | Utente non invitato non accede a dati o funzioni. |
| AC-002 | Utente invitato collega più account senza inviare password su Telegram. |
| AC-003 | Dati account A non sono visibili/operabili dall'utente B. |
| AC-004 | Ricerca per prestazione mostra risultati reali selezionabili e paginati. |
| AC-005 | Ricerca per medico mantiene il vincolo sul medico. |
| AC-006 | L'utente può scegliere una, più o tutte le sedi restituite. |
| AC-007 | Filtri periodo/giorni/orari/esclusioni producono gli slot attesi. |
| AC-008 | Prenotazione manuale richiede doppia conferma e viene riconciliata. |
| AC-009 | Monitor notifica soltanto nuovi slot quando configurato. |
| AC-010 | Auto-book non prenota mai slot fuori filtro. |
| AC-011 | Callback duplicata non duplica prenotazione/disdetta/notifica. |
| AC-012 | Esito ambiguo non causa retry distruttivo. |
| AC-013 | Monitor può essere modificato, duplicato, pausato, ripreso ed eliminato. |
| AC-014 | Notifiche possono essere disabilitate indipendentemente dal watch. |
| AC-015 | Appuntamenti vengono sincronizzati, disdetti e verificati. |
| AC-016 | PDF valido viene consegnato e non resta persistito. |
| AC-017 | Admin vede tutti gli oggetti previsti e ogni reveal/azione è auditata. |
| AC-018 | Admin non può ottenere password/cookie/token in chiaro. |
| AC-019 | Auth portale scaduta sospende solo account coinvolto e chiede re-auth. |
| AC-020 | Circuit breaker evita raffiche durante guasto/cambio contratto. |
| AC-021 | Restart conserva conversazioni, lease recuperabili e idempotency. |
| AC-022 | MySQL non è esposto in produzione. |
| AC-023 | Repo e immagini non contengono HAR, `.env`, segreti o dati reali. |
| AC-024 | Tutti i servizi Docker diventano healthy da database vuoto. |
| AC-025 | Nessun bottone visibile è privo di handler o produce placeholder. |

## Evidenza finale

`docs/IMPLEMENTATION_STATUS.md` deve elencare per ogni AC file implementativi, test e ultimo esito. Un criterio senza evidenza è incompleto.

