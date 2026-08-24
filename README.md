# OPBG Telegram Booking Bot — repository seed

Questo repository contiene le specifiche vincolanti per fare implementare a Claude Code un bot Telegram privato e multiutente per il solo percorso LAPI del portale di prenotazione dell'Ospedale Pediatrico Bambino Gesù.

Il prodotto finale deve consentire di:

- collegare più account del portale a ciascun utente Telegram invitato;
- scegliere account e paziente;
- cercare una prestazione oppure uno specifico medico;
- selezionare una o più sedi;
- filtrare periodo, giorni, orari e preferenze;
- cercare e prenotare immediatamente uno slot;
- creare monitoraggi in modalità notifica o auto-prenotazione;
- sospendere, riattivare, modificare o eliminare monitoraggi e notifiche;
- elencare e disdire appuntamenti con doppia conferma;
- scaricare il promemoria PDF;
- offrire all'amministratore una vista completa e auditata su utenti, account, monitoraggi, prenotazioni ed errori.

## Stato iniziale

Questo è intenzionalmente un **repo-seed di specifiche**, non un'applicazione parziale. Claude Code deve generare tutto il codice, i test, le migrazioni MySQL e gli artefatti Docker seguendo `CLAUDE.md` e i documenti in `docs/`.

## Ordine di lettura

1. `CLAUDE.md`
2. `docs/00-decisions.md`
3. `docs/01-product-requirements.md`
4. `docs/02-telegram-ux.md`
5. `docs/03-portal-integration.md`
6. `docs/04-data-model.md`
7. `docs/05-architecture.md`
8. `docs/06-monitoring-and-autobook.md`
9. `docs/07-admin-security-privacy.md`
10. `docs/08-docker-operations.md`
11. `docs/09-testing-acceptance.md`
12. `docs/10-implementation-plan.md`
13. `docs/11-definition-of-done.md`

## Avvio dell'implementazione

Aprire Claude Code nella root del repository e incollare il prompt contenuto in `PROMPT_CLAUDE_CODE.md`. Non caricare o committare file HAR: le chiamate necessarie sono già documentate in forma sanitizzata.

## Sicurezza immediata

I file HAR originali contengono password, cookie, token e dati sanitari. Devono restare fuori dal repository. Dopo una cattura è necessario cambiare la password del portale e terminare le sessioni esistenti.

Il progetto è un'integrazione privata e non ufficiale. Ogni account collegato deve appartenere a un utente autorizzato; il sistema non deve aggirare CAPTCHA, MFA, blocchi del portale o limitazioni tecniche.

