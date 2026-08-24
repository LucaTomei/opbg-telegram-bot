# Definition of Done

Il progetto è completo soltanto quando tutte le caselle applicabili sono verificate.

## Prodotto

- [ ] Invito, consenso e ruoli completi.
- [ ] Più utenti e più account per utente isolati.
- [ ] Collegamento/re-auth HTTPS sicuro.
- [ ] Pazienti sincronizzati e selezionabili.
- [ ] Ricerca LAPI per prestazione.
- [ ] Ricerca LAPI per medico specifico.
- [ ] Sedi singole, multiple, tutte dinamiche e preferenze.
- [ ] Filtri periodo fisso/mobile, weekday, orari, esclusioni.
- [ ] Prenotazione manuale e verifica.
- [ ] Monitor notify/autobook/both.
- [ ] Modifica, duplica, pausa, riprendi, toggle notifiche, elimina.
- [ ] Appuntamenti, disdetta verificata e PDF.
- [ ] Reply keyboard e inline keyboard complete.
- [ ] Admin vede/gestisce tutto il perimetro definito.

## Sicurezza

- [ ] Nessuna password via Telegram.
- [ ] Dati sensibili cifrati e blind index corretti.
- [ ] Segreti e HAR esclusi da Git/Docker context.
- [ ] Callback autorizzati e idempotenti.
- [ ] Doppia conferma per mutazioni.
- [ ] Admin non può rivelare segreti.
- [ ] Reveal/azioni admin auditati.
- [ ] Redaction testata.
- [ ] MySQL non esposto.
- [ ] PDF temporanei/no-store.

## Affidabilità

- [ ] Sessioni account isolate.
- [ ] Lease MySQL e recovery worker.
- [ ] Booking/disdetta senza retry cieco.
- [ ] Reconcile su esiti ambigui.
- [ ] Rate limit, jitter, backoff e circuit breaker.
- [ ] Restart-safe conversation/job state.
- [ ] Graceful shutdown.
- [ ] Health e heartbeat.

## Qualità

- [ ] Codice e tipi in inglese, UI italiana.
- [ ] Nessun TODO/FIXME/pass/placeholder.
- [ ] Ogni bottone ha handler e test.
- [ ] Formatter/linter/type checker puliti.
- [ ] Unit/integration/contract/security test verdi.
- [ ] Coverage significativa sulle parti critiche, soglia documentata.
- [ ] Migrazioni da DB vuoto verdi.
- [ ] Docker build/config/up/health verdi.
- [ ] Backup e restore documentati/testati.
- [ ] Matrice AC con evidenze aggiornata.

## Verifica esterna ammessa

È accettabile lasciare non automatizzata soltanto la verifica live distruttiva di prenotazione/disdetta, purché:

- tutta la logica sia implementata e testata con adapter/fixture sintetici;
- il runbook descriva una prova manuale controllata;
- il codice non dichiari successo prima della riconciliazione;
- l'assenza della prova live sia dichiarata chiaramente, non nascosta.

