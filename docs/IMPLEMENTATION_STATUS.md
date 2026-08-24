# Implementation status

Stato verificato al **2026-08-24**. Ogni riga della sezione
[Verifiche](#verifiche) riporta il comando eseguito e l'esito reale: nulla è
dichiarato completo senza averlo eseguito.

## Stato

`COMPLETE` — tutte le fasi di `docs/10-implementation-plan.md` sono chiuse.
L'unica verifica non automatizzata è la prova live distruttiva di prenotazione
e disdetta sul portale reale, descritta in
[Verifiche esterne](#verifiche-esterne).

## Ambiente

- Versione applicativa: `1.0.0`
- Revisione schema: `0003_clean_internal_columns`
- Python: 3.13
- MySQL: 8.4 (immagine `mysql:8.4`)
- Docker: 29.5.2 · Compose: v5.1.4
- Ultimo aggiornamento UTC: 2026-08-24

## Moduli

| Area | Moduli |
| --- | --- |
| Configurazione | `app/config.py`, `app/logging.py`, `app/version.py`, `app/composition.py`, `app/cli.py` |
| Dominio | `app/domain/{enums,entities,errors,filters,fingerprint}.py` |
| Sicurezza | `app/security/{encryption,blind_index,tokens,redaction,context}.py` |
| Persistenza | `app/db/{base,session,types,migrations}.py`, `app/db/models/*`, `app/db/repositories/*`, `alembic/versions/0001_initial.py`, `alembic/versions/0002_restrict_booking_history.py`, `alembic/versions/0003_clean_internal_columns.py` |
| Adapter portale | `app/portal/{client,parsers,forms,models,rate_limit,circuit_breaker}.py` |
| Servizi | `app/services/{users,accounts,portal_sessions,search,bookings,appointments,watches,notifications,confirmations,admin,locks,mapping,slot_codec}.py` |
| Bot Telegram | `app/bot/{texts,callbacks,storage,notifications}.py`, `app/bot/{routers,keyboards,middleware,presenters}/*` |
| Web | `app/web/{security}.py`, `app/web/routes/*`, `app/web/templates/*` |
| Worker | `app/worker/{scheduler,tasks,heartbeat,leases}.py` |
| Operazioni | `Dockerfile`, `compose.yaml`, `compose.override.yaml`, `docker/`, `scripts/`, `Makefile`, `.github/workflows/ci.yml` |

## Fasi

| Fase | Stato | Evidenza |
| --- | --- | --- |
| 1 Fondazioni | Completata | `pyproject.toml`, lock file, `app/config.py`, `app/logging.py`, migrazione `0001_initial`, `docker compose build`, health check |
| 2 Sicurezza e repository | Completata | `app/security/*`, `app/db/repositories/*`, `tests/unit/test_encryption.py`, `tests/integration/test_repositories.py`, `tests/integration/test_concurrency.py` |
| 3 Adapter portale read-only | Completata | `app/portal/*`, `tests/contract/test_parsers.py`, `tests/contract/test_portal_client.py`, fixture sintetici |
| 4 Web connect e account | Completata | `app/web/*`, `app/services/accounts.py`, `tests/integration/test_web.py`, `tests/integration/test_account_flow.py` |
| 5 Telegram core | Completata | `app/bot/*`, `tests/integration/test_bot_onboarding.py` |
| 6 Ricerca e disponibilità | Completata | `app/services/search.py`, `app/bot/routers/booking.py`, `tests/integration/test_bot_flows.py` |
| 7 Prenotazione, disdetta e PDF | Completata | `app/services/{bookings,appointments}.py`, `tests/integration/test_booking_flow.py` |
| 8 Monitor e worker | Completata | `app/services/watches.py`, `app/worker/*`, `tests/integration/{test_worker,test_scheduler}.py` |
| 9 Amministrazione | Completata | `app/services/admin.py`, `app/bot/routers/admin.py`, `tests/integration/test_bot_admin.py` |
| 10 Operazioni e hardening | Completata | `Dockerfile`, `compose.yaml`, `docker/Caddyfile`, `scripts/*`, `docs/12-runbook.md` |
| 11 Audit finale | Completata | questa pagina, `scripts/handler_audit.py`, `scripts/secret_scan.sh`, `scripts/placeholder_scan.sh` |

## Verifiche

Tutti i comandi sono stati eseguiti il 2026-08-24 sulla revisione corrente.

| Verifica | Comando | Esito |
| --- | --- | --- |
| Formatter | `ruff format --check app tests alembic scripts` | ✅ tutti i file già formattati |
| Linter | `ruff check .` | ✅ nessun rilievo |
| Type checker | `mypy app tests` | ✅ 144 file, nessun errore (modalità `strict`) |
| Test unit/contract/security | `pytest tests/unit tests/contract tests/security` | ✅ 323 passati, 5 deselezionati (live) |
| Test integrazione (MySQL) | `pytest tests/integration` | ✅ 255 passati |
| Suite completa | `pytest` | ✅ 580 passati, 5 deselezionati |
| Copertura | `pytest --cov=app` | ✅ 79% complessivo (soglia documentata: 70%) |
| Compose config | `docker compose config` | ✅ configurazione valida |
| Build immagine | `docker compose build` | ✅ `opbg-telegram-bot:1.0.0` |
| Migrazione da DB vuoto | `docker compose down -v && docker compose run --rm migrate` | ✅ `0001_initial` → `0002_restrict_booking_history` → `0003_clean_internal_columns` |
| Health container | `docker compose ps` | ✅ `mysql`, `web`, `worker` healthy; `/health/ready` → `{"status":"ready","schema_revision":"0003_clean_internal_columns"}` |
| Config TLS | `caddy validate --config docker/Caddyfile` | ✅ `Valid configuration` |
| Backup | `docker compose --profile backup run --rm backup` | ✅ `opbg-<timestamp>-0003_clean_internal_columns.sql.gz.enc`, esito `SUCCESS` in `backup_status` |
| Restore | `restore.sh <archivio> opbg_bot_restore_check` | ✅ 28 tabelle ripristinate su database separato |
| Spegnimento pulito | `docker compose stop worker` | ✅ `WORKER_STOPPED` con rilascio dei lease |
| Secret scan | `bash scripts/secret_scan.sh` | ✅ pulito; ogni rilevatore verificato iniettando un finto segreto su Ubuntu |
| Scan placeholder | `bash scripts/placeholder_scan.sh` | ✅ pulito; rilevatori verificati su Ubuntu |
| Guardrail su Linux | `make audit-ci` | ✅ scanner eseguiti in un container identico al runner |
| Audit bottoni | `python scripts/handler_audit.py` | ✅ 104 payload di callback, tutti gestiti |
| Configurazione senza voci morte | `pytest tests/security/test_ui_completeness.py` | ✅ ogni variabile dichiarata è usata e ogni variabile documentata esiste |
| CI | `.github/workflows/ci.yml` | ✅ job `guardrails` (secret e placeholder scan), `quality` (formato, lint, tipi, audit bottoni, test con soglia 70%), `image` (build e `compose config`); i test live restano deselezionati |

### Copertura sulle parti critiche

Soglia minima documentata: **70%** complessivo (`make coverage`). Moduli
critici misurati singolarmente:

| Modulo | Copertura |
| --- | --- |
| `app/domain/filters.py` | 100% |
| `app/domain/fingerprint.py` | 100% |
| `app/security/redaction.py` | 100% |
| `app/security/tokens.py` | 100% |
| `app/security/encryption.py` | 99% |
| `app/security/blind_index.py` | 95% |
| `app/db/repositories/watches.py` | 98% |
| `app/db/repositories/accounts.py` | 94% |
| `app/config.py` | 93% |
| `app/portal/client.py` | 90% |
| `app/db/session.py` | 88% |
| `app/db/repositories/bookings.py` | 82% |
| `app/worker/tasks.py` | 81% |
| `app/portal/parsers.py` | 75% |

## Tracciabilità dei criteri di accettazione

| AC | Criterio | Implementazione | Test | Esito |
| --- | --- | --- | --- | --- |
| AC-001 | Utente non invitato non accede a dati o funzioni | `app/bot/middleware/auth.py`, `app/services/users.py` | `tests/integration/test_bot_onboarding.py::test_an_unknown_user_cannot_reach_any_feature`, `::test_an_invalid_invite_is_refused` | ✅ |
| AC-002 | Più account collegati senza password su Telegram | `app/services/accounts.py`, `app/web/routes/connect.py`, `app/bot/routers/accounts.py` | `tests/integration/test_web.py::test_a_valid_submission_creates_the_account`, `tests/integration/test_bot_flows.py::test_the_account_screen_offers_the_secure_connection_link` | ✅ |
| AC-003 | I dati dell'utente A non sono visibili all'utente B | `app/db/repositories/accounts.py` (`get_owned`), `app/services/watches.py` | `tests/integration/test_bot_flows.py::test_a_user_cannot_open_another_users_account`, `::test_a_user_cannot_open_another_users_monitor`, `tests/integration/test_repositories.py::test_account_ownership_is_enforced` | ✅ |
| AC-004 | Ricerca per prestazione paginata e selezionabile | `app/services/search.py`, `app/bot/routers/booking.py` | `tests/integration/test_bot_flows.py::test_the_booking_wizard_reaches_the_slot_list`, `tests/contract/test_parsers.py::test_services_are_normalised` | ✅ |
| AC-005 | La ricerca per medico mantiene il vincolo sul medico | `app/domain/filters.py::_passes_identity`, `app/services/search.py` | `tests/unit/test_filters.py::test_a_doctor_search_never_degrades_to_another_doctor`, `tests/integration/test_bot_flows.py::test_a_doctor_search_keeps_the_doctor_constraint` | ✅ |
| AC-006 | Scelta di una, più o tutte le sedi | `app/bot/routers/booking.py` (toggle, tutte, ordine) | `tests/integration/test_bot_flows.py::test_location_preference_order_can_be_changed`, `tests/unit/test_filters.py::test_all_dynamic_keeps_new_locations` | ✅ |
| AC-007 | I filtri producono gli slot attesi | `app/domain/filters.py` | `tests/unit/test_filters.py` (42 test: periodo, rolling, DST, weekday, fasce, esclusioni, sedi, ordinamento) | ✅ |
| AC-008 | Prenotazione manuale con doppia conferma e riconciliazione | `app/services/bookings.py`, `app/services/confirmations.py` | `tests/integration/test_bot_flows.py::test_booking_requires_two_confirmations`, `tests/integration/test_booking_flow.py::test_booking_reconciles_the_new_appointment` | ✅ |
| AC-009 | Il monitor notifica solo i nuovi slot quando configurato | `app/worker/tasks.py::WatchProcessor` | `tests/integration/test_worker.py::test_only_new_slots_are_notified_on_the_second_run` | ✅ |
| AC-010 | L'auto-prenotazione non prenota mai fuori filtro | `app/services/bookings.py::_match_slot` | `tests/integration/test_worker.py::test_autobook_never_books_a_slot_outside_the_filters`, `tests/integration/test_booking_flow.py::test_a_slot_outside_the_filters_is_never_booked` | ✅ |
| AC-011 | Callback duplicata non duplica nulla | `app/bot/middleware/idempotency.py`, chiavi di idempotenza | `tests/integration/test_bot_flows.py::test_a_replayed_confirmation_never_books_twice`, `::test_a_replayed_cancellation_callback_cancels_once`, `::test_a_replayed_removal_callback_is_ignored` | ✅ |
| AC-012 | Esito ambiguo senza retry distruttivo | `app/services/bookings.py`, `app/services/appointments.py`, `app/worker/tasks.py::ReconciliationTask` | `tests/integration/test_booking_flow.py::test_ambiguous_outcome_is_recorded_without_retry`, `::test_ambiguous_cancellation_is_not_retried`, `tests/integration/test_worker.py::test_a_worker_crash_between_confirm_and_reconcile_is_recovered` | ✅ |
| AC-013 | Monitor modificabile, duplicabile, pausabile, eliminabile | `app/services/watches.py`, `app/bot/routers/watches.py` | `tests/integration/test_bot_flows.py::test_a_monitor_can_be_paused_resumed_and_deleted`, `::test_a_monitor_can_be_edited_and_saved`, `::test_a_monitor_can_be_duplicated` | ✅ |
| AC-014 | Notifiche disattivabili indipendentemente dal watch | `app/services/watches.py::set_notifications`, `app/services/notifications.py` | `tests/integration/test_bot_flows.py::test_notifications_can_be_toggled_independently_of_autobook`, `tests/unit/test_notification_rules.py::test_watch_switch_is_independent_from_the_global_one` | ✅ |
| AC-015 | Appuntamenti sincronizzati, disdetti e verificati | `app/services/appointments.py` | `tests/integration/test_booking_flow.py::test_appointments_are_synchronised`, `::test_cancellation_is_verified_on_the_portal` | ✅ |
| AC-016 | PDF valido consegnato e non persistito | `app/portal/client.py::download_pdf`, `app/web/routes/download.py` | `tests/contract/test_portal_client.py::test_pdf_is_validated` e i tre test di rifiuto, `tests/integration/test_web.py::test_a_one_time_link_delivers_the_pdf_once` | ✅ |
| AC-017 | L'admin vede tutto e ogni reveal/azione è auditata | `app/services/admin.py`, `app/bot/routers/admin.py` | `tests/integration/test_bot_admin.py::test_the_dashboard_lists_every_section`, `::test_revealing_personal_data_requires_a_reason_and_is_audited`, `::test_delegated_cancellation_is_confirmed_and_audited` | ✅ |
| AC-018 | L'admin non ottiene password/cookie/token in chiaro | `app/services/admin.py` (reveal limitati) | `tests/security/test_secrets_never_leak.py::test_the_admin_service_exposes_no_secret_accessor`, `::test_revealing_an_account_returns_no_credential`, `tests/integration/test_bot_admin.py::test_the_account_detail_never_reveals_credentials` | ✅ |
| AC-019 | Auth scaduta sospende solo l'account e chiede re-auth | `app/services/portal_sessions.py`, `app/worker/tasks.py` | `tests/integration/test_worker.py::test_authentication_failure_suspends_only_that_account`, `tests/integration/test_account_flow.py::test_reauthentication_rotates_the_credentials_and_resumes_watches` | ✅ |
| AC-020 | Il circuit breaker evita raffiche durante un guasto | `app/portal/circuit_breaker.py` | `tests/integration/test_rate_limit_and_breaker.py` (12 test), `tests/integration/test_worker.py::test_the_circuit_breaker_defers_instead_of_failing` | ✅ |
| AC-021 | Il restart conserva conversazioni, lease e idempotenza | `app/bot/storage.py`, `app/db/repositories/watches.py`, `app/worker/scheduler.py` | `tests/integration/test_repositories.py::test_conversation_state_survives_a_restart`, `tests/integration/test_concurrency.py::test_an_expired_lease_is_recovered`, `tests/integration/test_scheduler.py::test_shutdown_releases_the_leases_of_this_worker` | ✅ |
| AC-022 | MySQL non è esposto in produzione | `compose.yaml` (nessun `ports` su `mysql`), `compose.override.yaml` (solo sviluppo) | `docker compose -f compose.yaml config` non pubblica porte MySQL; verificato manualmente | ✅ |
| AC-023 | Repo e immagini senza HAR, `.env`, segreti o dati reali | `.gitignore`, `.dockerignore`, `scripts/secret_scan.sh` | `bash scripts/secret_scan.sh`, `tests/security/test_no_destructive_live_tests.py` | ✅ |
| AC-024 | Tutti i servizi Docker diventano healthy da DB vuoto | `Dockerfile`, `compose.yaml`, `app/cli.py` | `docker compose down -v` → `migrate` → `up` → `ps` tutti healthy | ✅ |
| AC-025 | Nessun bottone privo di handler o con placeholder | `app/bot/*` | `python scripts/handler_audit.py` (104 payload), `bash scripts/placeholder_scan.sh` | ✅ |

## Definition of Done

Ogni casella di `docs/11-definition-of-done.md` è soddisfatta, con l'unica
eccezione dichiarata nella sezione seguente.

- **Prodotto**: invito/consenso/ruoli, multi-utente e multi-account isolati,
  collegamento e re-auth HTTPS, pazienti sincronizzati, ricerca per prestazione
  e per medico, sedi singole/multiple/dinamiche con preferenze, filtri completi,
  prenotazione verificata, monitor `NOTIFY`/`AUTOBOOK`/`AUTOBOOK_NOTIFY`,
  modifica/duplica/pausa/ripresa/toggle/elimina, appuntamenti con disdetta
  verificata e PDF, reply e inline keyboard complete, amministrazione completa.
- **Sicurezza**: nessuna password via Telegram, cifratura autenticata e blind
  index, segreti e HAR esclusi da Git e dal contesto Docker, callback
  autorizzati e idempotenti, doppia conferma, nessun segreto rivelabile
  all'admin, azioni auditate, redazione testata, MySQL non esposto, PDF non
  persistiti.
- **Affidabilità**: sessioni isolate per account, lease MySQL con recovery,
  nessun retry cieco su prenotazione e disdetta, riconciliazione sugli esiti
  ambigui, rate limit/jitter/backoff/circuit breaker, stato conversazioni e job
  restart-safe, shutdown pulito con rilascio dei lease, health e heartbeat.
- **Qualità**: codice e tipi in inglese con UI italiana, nessun placeholder,
  ogni bottone con handler e test, formatter/linter/type checker puliti, suite
  verde, migrazioni da database vuoto, Docker build/config/up/health verdi,
  backup e restore documentati e provati, matrice AC aggiornata.

## Verifiche esterne

Una sola verifica resta non automatizzata, come consentito da
`docs/11-definition-of-done.md`:

- **Prenotazione e disdetta reali sul portale**. Tutta la logica è
  implementata e testata contro un portale sintetico che riproduce le stesse
  route, gli stessi campi form e gli stessi payload JSON documentati, inclusi
  esito ambiguo, slot perso e sessione scaduta. Nessun test automatico crea o
  disdice un appuntamento vero; i test live sono opt-in, in sola lettura e
  deselezionati per impostazione predefinita (`pytest -m live`, con
  `OPBG_LIVE_TESTS=1` e credenziali reali).

  La prova manuale controllata è descritta passo per passo in
  [`docs/12-runbook.md`](12-runbook.md#verifica-manuale-della-prenotazione-live).
  Richiede credenziali reali di un account autorizzato e non può essere
  eseguita in questo ambiente.

  **Stato: non eseguita.** Annotare qui data, esito e correlation ID quando
  verrà eseguita.

## Problemi noti e scelte esplicite

- **Chiave di blind index non ruotabile a caldo**: cambiarla invalida gli
  indici di ricerca sui campi cifrati. La rotazione richiede una ricostruzione
  degli indici; la chiave di cifratura, invece, supporta la rotazione online
  con `APP_PREVIOUS_ENCRYPTION_KEYS`.
- **Nessuna condivisione della lettura di disponibilità tra monitor simili**:
  `docs/06` la indica come possibilità («possono condividere»), non come
  requisito. Ogni monitor esegue la propria lettura; il carico verso il portale
  resta limitato dal rate limiter globale, dal limite per account e dal
  circuit breaker.
- **Storico appuntamenti**: `GET /appointments/history` viene letto quando
  disponibile e la sua assenza non blocca l'elenco corrente, come richiesto.
- **Client MySQL nell'immagine**: l'immagine applicativa include
  `default-mysql-client` e `openssl` perché il profilo `backup` esegue dump,
  compressione e cifratura come utente non privilegiato.
