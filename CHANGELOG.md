# Changelog

Il formato segue [Keep a Changelog](https://keepachangelog.com/it/1.1.0/) e il
versionamento è [semantico](https://semver.org/lang/it/).

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

[1.0.0]: https://github.com/LucaTomei/opbg-telegram-bot/releases/tag/v1.0.0
