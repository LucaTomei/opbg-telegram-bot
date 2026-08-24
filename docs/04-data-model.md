# Modello dati MySQL

Usare migrazioni Alembic, InnoDB, `utf8mb4`, UTC nel database e conversione Europe/Rome ai bordi. Le chiavi interne possono essere UUIDv7 binari o BIGINT; scegliere una strategia unica e documentarla.

## Principi

- Dati sensibili cifrati con authenticated encryption.
- Campi di lookup sensibili affiancati da blind index HMAC, mai hash semplice.
- Nessun PDF persistito.
- Soft delete dove serve audit; hard delete crittografico per credenziali su richiesta dell'utente.
- Timestamp `created_at`, `updated_at` e, quando utile, `deleted_at`.
- Campo `version` per optimistic locking sugli oggetti modificabili.
- Foreign key e indici espliciti.

## Tabelle obbligatorie

### `telegram_users`

- `id`
- `telegram_id` unique
- `username_ciphertext`, `first_name_ciphertext`, `last_name_ciphertext`
- `role`: `USER|ADMIN`
- `status`: `PENDING_CONSENT|ACTIVE|SUSPENDED|BLOCKED|DELETED`
- `timezone`
- `notifications_enabled`
- `quiet_hours_start`, `quiet_hours_end`
- `consent_version`, `consented_at`
- timestamps/version

### `invite_codes`

- `id`
- `code_hash` unique
- `created_by_user_id`
- `expires_at`
- `max_uses`, `used_count`
- `revoked_at`
- optional `note`
- timestamps

### `invite_redemptions`

- `invite_id`, `telegram_user_id`
- `redeemed_at`
- unique per invite/user

### `portal_accounts`

- `id`, `telegram_user_id`
- `label`
- `email_ciphertext`, `email_blind_index`
- `password_ciphertext`
- `cookie_jar_ciphertext`
- `portal_metadata_ciphertext`
- `status`: `ACTIVE|DISABLED|REAUTH_REQUIRED|LOCKED|DELETED`
- `last_login_at`, `last_sync_at`, `last_error_code`
- `consecutive_auth_failures`
- `encryption_key_version`
- timestamps/version

Unique `(telegram_user_id, email_blind_index)` tra record non eliminati.

### `patients`

- `id`, `portal_account_id`
- `external_tax_code_ciphertext`, `external_tax_code_blind_index`
- `display_name_ciphertext`
- `relationship_ciphertext`
- `active`, `last_seen_at`
- timestamps/version

Unique `(portal_account_id, external_tax_code_blind_index)`.

### `conversation_states`

- `telegram_user_id` primary/unique
- `state`
- `payload_ciphertext`
- `expires_at`
- `version`, timestamps

Non salvare password neppure temporaneamente nello stato conversazione.

### `booking_drafts`

- `id`, `telegram_user_id`, `portal_account_id`, `patient_id`
- `search_type`: `SERVICE|DOCTOR`
- `query_text`
- identificativi/etichette selezionati cifrati quando sensibili
- `location_mode`: `ALL_DYNAMIC|SELECTED`
- filtri data/ora serializzati e validati
- `selected_slot_ciphertext`
- `status`, `expires_at`, `version`

### `booking_watches`

- `id`, owner/account/patient
- `name`
- `search_type`
- `query_text`
- `selected_service_external_id`, `selected_service_name`
- `selected_doctor_external_id`, `selected_doctor_name`
- `location_mode`
- `date_mode`: `FIXED|ROLLING`
- `date_from`, `date_to`, `rolling_days`
- `weekdays_mask`
- `time_windows_json`
- `excluded_dates_json`
- `sort_strategy`
- `mode`: `NOTIFY|AUTOBOOK|AUTOBOOK_NOTIFY`
- `notifications_enabled`
- `notify_only_changes`
- `status`: `DRAFT|ACTIVE|PAUSED|COMPLETED|ERROR|DELETED`
- `interval_seconds`
- `last_checked_at`, `next_check_at`, `last_success_at`
- `last_slot_fingerprint`, `consecutive_errors`, `last_error_code`
- `lease_owner`, `lease_expires_at`
- `completed_reason`
- timestamps/version

Indici: `(status,next_check_at)`, `(portal_account_id,status)`, `(telegram_user_id,status)`.

### `watch_locations`

- `watch_id`
- `portal_location_id`
- `location_name`
- `preference_order`
- unique `(watch_id, portal_location_id)`

Vuota quando `location_mode=ALL_DYNAMIC`.

### `seen_slots`

- `watch_id`
- `slot_fingerprint`
- `first_seen_at`, `last_seen_at`, `last_notified_at`
- `currently_available`
- optional normalized non-sensitive slot metadata
- unique `(watch_id, slot_fingerprint)`

### `booking_attempts`

- `id`, `telegram_user_id`, `portal_account_id`, optional `watch_id`, optional `draft_id`
- `idempotency_key` unique
- `slot_fingerprint`
- `status`: `STARTED|VERIFYING|SUCCEEDED|SLOT_LOST|REJECTED|AMBIGUOUS|FAILED`
- `stage`
- `portal_appointment_external_id`
- `error_code`, redacted `error_detail`
- `started_at`, `finished_at`
- `version`

Unique parziale/logica per watch + slot riuscito, così lo stesso slot non viene prenotato due volte.

### `appointments_cache`

- `id`, owner/account/patient
- `portal_external_id`, unique per account
- `service_name`, `doctor_name`, `location_name` cifrati se necessario
- `scheduled_at_utc`
- `status`: `PENDING|CANCELLED|COMPLETED|UNKNOWN`
- `payment_note`
- `first_seen_at`, `last_seen_at`, `cancelled_at`
- `raw_fingerprint` senza raw HTML
- timestamps/version

### `notification_events`

- `id`, `telegram_user_id`, optional `watch_id`, optional `appointment_id`
- `event_type`
- `dedup_key` unique
- `status`: `PENDING|SENT|FAILED|SUPPRESSED`
- `telegram_message_id`
- `attempt_count`, `next_attempt_at`, `last_error_code`
- timestamps

### `confirmation_nonces`

- `id`, `telegram_user_id`
- `action_type`, `target_type`, `target_id`
- `nonce_hash` unique
- `expires_at`, `consumed_at`
- `context_hash`

### `admin_audit_log`

- append-only `id`
- `admin_user_id`
- `action`
- `target_type`, `target_id`
- `reason`
- `before_hash`, `after_hash`
- `correlation_id`
- `created_at`

Proteggere da update/delete tramite repository e test; opzionalmente trigger DB.

### `system_events`

- `id`, `severity`, `component`, `event_code`
- messaggio redatto
- `correlation_id`
- timestamps/resolution

### `worker_heartbeats`

- `worker_id` unique
- `started_at`, `last_seen_at`, `version`, `metadata_json`

## Cancellazioni e cascata

- Rimozione portal account: sospende monitoraggi, elimina password/cookie, mantiene audit minimo e appuntamenti solo per retention configurata.
- Rimozione utente: revoca inviti personali, sospende job, elimina crittograficamente dati sensibili e Telegram identifiers secondo policy.
- Eliminazione monitoraggio: soft delete per audit; seen slots possono essere rimossi dopo retention.
- Non usare cascade che cancelli accidentalmente audit o tentativi di booking.

## Migrazioni e seed

- Migrazione iniziale completa.
- Seed solo per test; nessun admin hardcoded.
- Admin derivati da `TELEGRAM_ADMIN_IDS` vengono upsertati all'avvio.
- Migrazioni repeatable solo tramite Alembic; niente `create_all` in produzione.

