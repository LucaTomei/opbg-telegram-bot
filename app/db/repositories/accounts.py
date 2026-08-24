"""Repositories for portal accounts, patients, tickets and download links."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Select, select

from app.db.base import blind_index_for
from app.db.models.accounts import ConnectRateLimit, Patient, PortalAccount
from app.db.repositories.base import BaseRepository, Page, utc_now
from app.domain.entities import CookieJar
from app.domain.enums import PortalAccountStatus

ACCOUNT_TABLE = "portal_accounts"
PATIENT_TABLE = "patients"


class PortalAccountRepository(BaseRepository[PortalAccount]):
    model = PortalAccount

    async def list_for_user(
        self, user_id: uuid.UUID, *, include_disabled: bool = True
    ) -> list[PortalAccount]:
        statement = (
            select(PortalAccount)
            .where(
                PortalAccount.telegram_user_id == user_id,
                PortalAccount.deleted_at.is_(None),
            )
            .order_by(PortalAccount.created_at.asc())
        )
        if not include_disabled:
            statement = statement.where(PortalAccount.status == PortalAccountStatus.ACTIVE)
        result = await self.session.scalars(statement)
        return list(result)

    async def count_for_user(self, user_id: uuid.UUID) -> int:
        return await self.count(
            select(PortalAccount).where(
                PortalAccount.telegram_user_id == user_id,
                PortalAccount.deleted_at.is_(None),
            )
        )

    async def find_by_email(self, user_id: uuid.UUID, email: str) -> PortalAccount | None:
        """Look up an account by e-mail without ever comparing plaintext."""
        index = blind_index_for(ACCOUNT_TABLE, "email", email)
        return await self.one_or_none(
            select(PortalAccount).where(
                PortalAccount.telegram_user_id == user_id,
                PortalAccount.email_blind_index == index,
                PortalAccount.deleted_at.is_(None),
            )
        )

    async def get_owned(self, account_id: uuid.UUID, user_id: uuid.UUID) -> PortalAccount | None:
        return await self.one_or_none(
            select(PortalAccount).where(
                PortalAccount.id == account_id,
                PortalAccount.telegram_user_id == user_id,
                PortalAccount.deleted_at.is_(None),
            )
        )

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        label: str,
        email: str,
        password: str,
        encryption_key_version: int,
    ) -> PortalAccount:
        account = PortalAccount(
            telegram_user_id=user_id,
            label=label[:64],
            email_blind_index=blind_index_for(ACCOUNT_TABLE, "email", email),
            encryption_key_version=encryption_key_version,
        )
        account.email = email
        account.password = password
        return await self.add(account)

    async def rotate_credentials(
        self, account: PortalAccount, *, email: str, password: str, encryption_key_version: int
    ) -> None:
        account.email = email
        account.password = password
        account.email_blind_index = blind_index_for(ACCOUNT_TABLE, "email", email)
        account.encryption_key_version = encryption_key_version
        account.consecutive_auth_failures = 0
        account.status = PortalAccountStatus.ACTIVE
        account.last_error_code = None
        await self.flush()

    async def store_session(
        self, account: PortalAccount, *, cookies: CookieJar, metadata: dict[str, object]
    ) -> None:
        """Persist the encrypted cookie jar only after a verified response."""
        account.cookie_jar = cookies
        account.portal_metadata = metadata
        account.last_login_at = utc_now()
        account.consecutive_auth_failures = 0
        account.last_error_code = None
        if account.status is PortalAccountStatus.REAUTH_REQUIRED:
            account.status = PortalAccountStatus.ACTIVE
        await self.flush()

    async def clear_session(self, account: PortalAccount) -> None:
        account.cookie_jar = None
        await self.flush()

    async def mark_auth_failure(
        self, account: PortalAccount, *, error_code: str, max_failures: int
    ) -> None:
        account.consecutive_auth_failures += 1
        account.last_error_code = error_code[:64]
        account.last_error_at = utc_now()
        account.cookie_jar = None
        if account.consecutive_auth_failures >= max_failures:
            account.status = PortalAccountStatus.LOCKED
        else:
            account.status = PortalAccountStatus.REAUTH_REQUIRED
        await self.flush()

    async def mark_error(self, account: PortalAccount, *, error_code: str) -> None:
        account.last_error_code = error_code[:64]
        account.last_error_at = utc_now()
        await self.flush()

    async def set_status(self, account: PortalAccount, status: PortalAccountStatus) -> None:
        account.status = status
        await self.flush()

    async def rename(self, account: PortalAccount, label: str) -> None:
        account.label = label[:64]
        await self.flush()

    async def soft_delete(self, account: PortalAccount) -> None:
        """Crypto-erase credentials and release the alive-uniqueness constraint."""
        account.password = None
        account.cookie_jar = None
        account.portal_metadata = None
        account.email = None
        account.deleted_at = utc_now()
        account.mark_deleted()
        await self.flush()

    async def list_all(
        self,
        *,
        page: int,
        page_size: int,
        status: PortalAccountStatus | None = None,
        user_id: uuid.UUID | None = None,
    ) -> Page[PortalAccount]:
        statement: Select[tuple[PortalAccount]] = select(PortalAccount).order_by(
            PortalAccount.created_at.desc()
        )
        if status is not None:
            statement = statement.where(PortalAccount.status == status)
        if user_id is not None:
            statement = statement.where(PortalAccount.telegram_user_id == user_id)
        return await self.paginate(statement, page=page, page_size=page_size)

    async def list_needing_sync(self, *, older_than: datetime, limit: int) -> list[PortalAccount]:
        statement = (
            select(PortalAccount)
            .where(
                PortalAccount.status == PortalAccountStatus.ACTIVE,
                PortalAccount.deleted_at.is_(None),
                (PortalAccount.last_sync_at.is_(None)) | (PortalAccount.last_sync_at < older_than),
            )
            # MySQL has no NULLS FIRST: never synchronised accounts come first.
            .order_by(
                PortalAccount.last_sync_at.is_(None).desc(),
                PortalAccount.last_sync_at.asc(),
            )
            .limit(limit)
        )
        result = await self.session.scalars(statement)
        return list(result)


class PatientRepository(BaseRepository[Patient]):
    model = Patient

    async def list_for_account(
        self, account_id: uuid.UUID, *, only_active: bool = True
    ) -> list[Patient]:
        statement = (
            select(Patient)
            .where(Patient.portal_account_id == account_id, Patient.deleted_at.is_(None))
            .order_by(Patient.created_at.asc())
        )
        if only_active:
            statement = statement.where(Patient.active.is_(True))
        result = await self.session.scalars(statement)
        return list(result)

    async def get_owned(self, patient_id: uuid.UUID, account_id: uuid.UUID) -> Patient | None:
        return await self.one_or_none(
            select(Patient).where(
                Patient.id == patient_id,
                Patient.portal_account_id == account_id,
                Patient.deleted_at.is_(None),
            )
        )

    async def find_by_tax_code(self, account_id: uuid.UUID, tax_code: str) -> Patient | None:
        index = blind_index_for(PATIENT_TABLE, "external_tax_code", tax_code)
        return await self.one_or_none(
            select(Patient).where(
                Patient.portal_account_id == account_id,
                Patient.external_tax_code_blind_index == index,
            )
        )

    async def upsert(
        self,
        *,
        account_id: uuid.UUID,
        tax_code: str,
        display_name: str,
        relation: str | None,
        now: datetime,
    ) -> Patient:
        patient = await self.find_by_tax_code(account_id, tax_code)
        if patient is None:
            patient = Patient(
                portal_account_id=account_id,
                external_tax_code_blind_index=blind_index_for(
                    PATIENT_TABLE, "external_tax_code", tax_code
                ),
                active=True,
                last_seen_at=now,
            )
            patient.external_tax_code = tax_code
            patient.display_name = display_name
            patient.relation = relation
            return await self.add(patient)
        patient.display_name = display_name
        patient.relation = relation
        patient.active = True
        patient.last_seen_at = now
        patient.deleted_at = None
        await self.flush()
        return patient

    async def deactivate_missing(
        self, account_id: uuid.UUID, *, seen_tax_codes: Sequence[str]
    ) -> int:
        indexes = {
            blind_index_for(PATIENT_TABLE, "external_tax_code", code) for code in seen_tax_codes
        }
        deactivated = 0
        for patient in await self.list_for_account(account_id, only_active=True):
            if patient.external_tax_code_blind_index not in indexes:
                patient.active = False
                deactivated += 1
        await self.flush()
        return deactivated

    async def list_all(
        self, *, page: int, page_size: int, account_id: uuid.UUID | None = None
    ) -> Page[Patient]:
        statement = select(Patient).order_by(Patient.created_at.desc())
        if account_id is not None:
            statement = statement.where(Patient.portal_account_id == account_id)
        return await self.paginate(statement, page=page, page_size=page_size)


class ConnectRateLimitRepository(BaseRepository[ConnectRateLimit]):
    model = ConnectRateLimit

    async def hit(
        self, *, scope: str, subject_hash: bytes, window_start: datetime, limit: int
    ) -> bool:
        """Increment the counter and return ``True`` when the limit is exceeded."""
        record = await self.one_or_none(
            select(ConnectRateLimit).where(
                ConnectRateLimit.scope == scope,
                ConnectRateLimit.subject_hash == subject_hash,
                ConnectRateLimit.window_start == window_start,
            )
        )
        if record is None:
            record = ConnectRateLimit(
                scope=scope, subject_hash=subject_hash, window_start=window_start, counter=1
            )
            self.session.add(record)
            await self.flush()
            return limit < 1
        record.counter += 1
        await self.flush()
        return record.counter > limit

    async def purge_before(self, *, before: datetime) -> int:
        statement = select(ConnectRateLimit).where(ConnectRateLimit.window_start < before)
        removed = 0
        for record in await self.session.scalars(statement):
            await self.session.delete(record)
            removed += 1
        await self.flush()
        return removed
