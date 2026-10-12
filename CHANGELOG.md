# Changelog

Il formato segue [Keep a Changelog](https://keepachangelog.com/it/1.1.0/) e il
versionamento è [semantico](https://semver.org/lang/it/).

## [Non rilasciato]

### Aggiunto

- Guida per pubblicare il bot su un qualsiasi server con Docker, con
  aggiornamento automatico dell'immagine via cron.
- Immagine ricostruita ogni settimana per le patch di sicurezza dell'immagine
  base; i tag `v1.2.3` pubblicano anche `1.2.3` e `1.2`.
- Policy di sicurezza e modelli per issue e pull request.

### Modificato

- Licenza MIT al posto di quella proprietaria.
- CI sui runner ospitati da GitHub.

### Corretto

- I test di integrazione non dipendono più dalla data in cui vengono eseguiti.
- L'informativa diceva che le credenziali si inseriscono in una pagina HTTPS:
  si scrivono in chat e il messaggio con la password viene cancellato subito.

### Rimosso

- Codice mai usato: funzioni, costanti e testi rimasti dalla vecchia pagina
  web di collegamento e da refactoring precedenti.
- Documento sui requisiti Docker, superato dalle guide di deploy e dal runbook.

## [1.0.0] — 2026-08-24

Prima versione completa.

### Aggiunto

- Accesso su invito con consenso versionato, ruoli utente e amministratore.
- Collegamento di più account del portale per utente, interamente in chat, con
  ri-autenticazione, test di accesso, rinomina, disabilitazione e rimozione.
- Sincronizzazione e selezione dei pazienti, con identificativi cifrati e
  mascherati nell'interfaccia.
- Ricerca per prestazione o per medico specifico, paginata e con selezione
  esatta.
- Selezione di una, più o tutte le sedi restituite dal portale, con ordine di
  preferenza.
- Filtri su periodo fisso o mobile, anticipo minimo, giorni della settimana,
  fasce orarie multiple, date escluse, ordinamento e limite risultati.
- Prenotazione con doppia conferma a scadenza, protocollo idempotente e
  riconciliazione sull'elenco appuntamenti.
- Monitoraggi in modalità notifica, auto-prenotazione o entrambe, con modifica,
  duplicazione, pausa, ripresa, toggle notifiche, esecuzione immediata ed
  eliminazione.
- Elenco e storico appuntamenti, dettaglio, promemoria PDF e disdetta
  verificata.
- Amministrazione completa dentro Telegram, con mascheramento predefinito,
  rivelazione motivata e audit immutabile.
- Worker con scheduler, lease MySQL, heartbeat, coda notifiche, rate limiter e
  circuit breaker per account e globale.
- Cifratura AES-256-GCM dei dati a riposo con rotazione online delle chiavi,
  indici ciechi HMAC e redazione centralizzata dei log.
- Distribuzione con Docker Compose: `mysql`, `bot`, `worker`, `migrate` e il
  profilo `backup`. Nessuna porta pubblicata.

[Non rilasciato]: https://github.com/LucaTomei/opbg-telegram-bot/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/LucaTomei/opbg-telegram-bot/releases/tag/v1.0.0
