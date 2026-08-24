# Piano di implementazione obbligatorio

Claude Code deve procedere fino al completamento, aggiornando `IMPLEMENTATION_STATUS.md`.

## Fase 1 — Fondazioni

- package manager/lock;
- configurazione tipizzata;
- logging/redaction;
- Dockerfile/Compose/MySQL;
- SQLAlchemy/Alembic;
- health/version;
- test/lint/type-check CI locale.

Uscita: migrazione zero→head e container base healthy.

## Fase 2 — Sicurezza e repository

- encryption/blind index/token hashing;
- modelli e repository;
- invite/admin bootstrap;
- audit append-only;
- conversation state;
- test tamper/isolation/constraints.

Uscita: persistence completa e verificata.

## Fase 3 — Portal adapter read-only

- session manager;
- login/verify;
- patient sync;
- autocomplete service/doctor;
- availability JSON/HTML;
- appointment list/history;
- synthetic contract fixtures;
- rate limit/backoff/circuit breaker base.

Uscita: tutte le letture modellate senza mutazioni reali.

## Fase 4 — Web connect e account management

- ticket one-time;
- pagina HTTPS minimale;
- CSRF/rate limit/security headers;
- connect/re-auth;
- account/patient services;
- bot account screens.

Uscita: un utente test collega account fake/contract adapter in sicurezza.

## Fase 5 — Telegram core

- invite/consent;
- reply keyboard persistente;
- callback registry compatto;
- pagination/back/menu/cancel;
- multi-account/patient flows;
- error presenter italiano.

Uscita: navigazione completa, restart-safe e autorizzata.

## Fase 6 — Ricerca e disponibilità

- service flow;
- doctor flow;
- sedi multi-select/all/preferenze;
- editor filtri date/giorni/orari/esclusioni;
- elenco slot e refresh;
- booking drafts.

Uscita: ricerca manuale completa con fixture/adapter, nessun bottone morto.

## Fase 7 — Prenotazione, disdetta e PDF

- selection/confirm portal stages;
- idempotency, account lock, reconcile;
- list/detail appointments;
- cancel double confirm e verify;
- PDF Telegram e one-time link;
- ambiguous outcomes.

Uscita: flussi mutanti completamente testati su fake portal, live distruttivo disabilitato.

## Fase 8 — Monitor e worker

- watch CRUD completo;
- leases/scheduler/jitter/backoff;
- filtering e seen slots;
- notification modes;
- auto-book protocol;
- dedup/retry/quiet hours;
- worker heartbeat/circuit breaker.

Uscita: restart/race/crash simulations superate.

## Fase 9 — Amministrazione

- dashboard Telegram admin;
- liste/filtri/dettagli;
- invite/user/account/watch management;
- view bookings/appointments/errors/jobs;
- masked/reveal data;
- delegated actions con reason+confirm+audit;
- system health.

Uscita: copertura completa “tutto di tutti” senza esposizione segreti.

## Fase 10 — Operations e hardening

- proxy/TLS config;
- backup/restore scripts;
- graceful shutdown/lease release;
- retention cleanup;
- metrics/log correlation;
- runbook/update/rollback;
- security/secret scan.

Uscita: deploy riproducibile su Docker host pulito.

## Fase 11 — Audit finale

- matrice requisiti/AC;
- ricerca bottone→handler;
- ricerca route→authz;
- ricerca dato sensibile→encryption/redaction;
- formatter/lint/type/test;
- Compose config/build/up/health;
- migration empty DB;
- nessun TODO/FIXME/pass/placeholder;
- aggiornamento documentazione.

Uscita: `docs/11-definition-of-done.md` interamente soddisfatto o elenco esplicito di sole verifiche live esterne mancanti.

