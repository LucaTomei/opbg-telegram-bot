# Implementation status

## Current phase

`PHASE_1_FOUNDATIONS`

## Environment

- Application version: 1.0.0
- Python version: 3.13
- MySQL version: 8.4 LTS
- Docker/Compose version: Docker 29.5.2 / Compose v5.1.4
- Last updated UTC: 2026-08-24

## Planned modules

| Area | Modules |
| --- | --- |
| Configuration | `app/config.py`, `app/logging.py`, `app/version.py` |
| Domain | `app/domain/enums.py`, `app/domain/entities.py`, `app/domain/errors.py`, `app/domain/filters.py`, `app/domain/fingerprint.py` |
| Security | `app/security/encryption.py`, `app/security/blind_index.py`, `app/security/tokens.py`, `app/security/audit.py`, `app/security/redaction.py` |
| Persistence | `app/db/base.py`, `app/db/session.py`, `app/db/types.py`, `app/db/models/*`, `app/db/repositories/*`, `alembic/` |
| Portal adapter | `app/portal/client.py`, `app/portal/session_manager.py`, `app/portal/parsers.py`, `app/portal/forms.py`, `app/portal/models.py`, `app/portal/rate_limit.py`, `app/portal/circuit_breaker.py` |
| Services | `app/services/accounts.py`, `bookings.py`, `appointments.py`, `watches.py`, `notifications.py`, `admin.py`, `invites.py`, `users.py`, `locks.py` |
| Telegram bot | `app/bot/routers/*`, `app/bot/keyboards/*`, `app/bot/callbacks.py`, `app/bot/middleware/*`, `app/bot/presenters/*`, `app/bot/texts.py` |
| Web | `app/web/routes/*`, `app/web/forms.py`, `app/web/templates/*`, `app/web/security.py` |
| Worker | `app/worker/scheduler.py`, `leases.py`, `tasks.py`, `heartbeat.py`, `cleanup.py` |
| Ops | `Dockerfile`, `compose.yaml`, `compose.override.yaml`, `docker/`, `scripts/`, `Makefile` |
| Tests | `tests/unit`, `tests/integration`, `tests/contract`, `tests/security`, `tests/fixtures/synthetic` |

## Acceptance tests planned

Every `AC-001` … `AC-025` from `docs/09-testing-acceptance.md` is mapped in the traceability table below and backed by at least one automated test.

## Phase progress

| Phase | Status | Evidence/commands |
| --- | --- | --- |
| 1 Foundations | In progress | |
| 2 Security/repositories | Not started | |
| 3 Portal read-only | Not started | |
| 4 Web connect | Not started | |
| 5 Telegram core | Not started | |
| 6 Search/availability | Not started | |
| 7 Booking/cancel/PDF | Not started | |
| 8 Monitoring/worker | Not started | |
| 9 Administration | Not started | |
| 10 Operations/hardening | Not started | |
| 11 Final audit | Not started | |

## Verification results

| Check | Command | Result | Timestamp |
| --- | --- | --- | --- |
| Format | `ruff format --check .` | Not run | |
| Lint | `ruff check .` | Not run | |
| Type check | `mypy app tests` | Not run | |
| Unit tests | `pytest tests/unit` | Not run | |
| Integration tests | `pytest tests/integration` | Not run | |
| Contract tests | `pytest tests/contract` | Not run | |
| Security/redaction tests | `pytest tests/security` | Not run | |
| Docker Compose config | `docker compose config` | Not run | |
| Docker builds | `docker compose build` | Not run | |
| Empty-DB migration | `docker compose run --rm migrate` | Not run | |
| Container health | `docker compose ps` | Not run | |
| Secret scan | `scripts/secret_scan.sh` | Not run | |
| TODO/placeholder scan | `scripts/placeholder_scan.sh` | Not run | |

## Acceptance traceability

| AC | Implementation files | Test files | Result |
| --- | --- | --- | --- |
| AC-001 | | | Not run |

## External-only validation

To be completed at the end of Phase 11.

## Known issues

Nessuno registrato.
