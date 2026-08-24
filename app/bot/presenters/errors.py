"""Translate domain errors into safe Italian messages.

The message never contains a portal payload, a stack trace or any personal
datum; when a technical detail matters the correlation id is shown instead.
"""

from __future__ import annotations

from app.bot import texts
from app.domain.errors import (
    AccountSuspendedError,
    AlreadyConsumedError,
    AmbiguousSearchResult,
    AuthenticationFailed,
    BookingAmbiguous,
    BookingRejected,
    CancellationAmbiguous,
    CircuitOpenError,
    ConflictError,
    ConsentRequiredError,
    DecryptionError,
    DomainError,
    ExpiredError,
    LockUnavailableError,
    NoAvailability,
    NotFoundError,
    OptimisticLockError,
    PdfUnavailable,
    PermissionDeniedError,
    PortalCaptchaRequired,
    PortalContractChanged,
    PortalRateLimited,
    PortalUnavailable,
    QuotaExceededError,
    RateLimitedError,
    ReauthenticationRequired,
    SlotNoLongerAvailable,
    ValidationError,
)

_MESSAGES: dict[type[DomainError], str] = {
    AccountSuspendedError: (
        "L'account del portale non è utilizzabile in questo momento. Controlla la sezione Account."
    ),
    AlreadyConsumedError: texts.ALREADY_HANDLED,
    AmbiguousSearchResult: (
        "La ricerca salvata non corrisponde più a un unico risultato del portale. "
        "Rifai la selezione dal menu Prenota."
    ),
    AuthenticationFailed: (
        "Le credenziali del portale non sono più valide. "
        "Aggiornale da <b>Account → Aggiorna credenziali</b>."
    ),
    BookingAmbiguous: (
        "Esito incerto della prenotazione. Non ho ritentato: controlla "
        "<b>Appuntamenti</b> prima di riprovare."
    ),
    BookingRejected: texts.BOOK_REJECTED,
    CancellationAmbiguous: (
        "Esito incerto della disdetta. Aggiorna l'elenco appuntamenti e verifica."
    ),
    CircuitOpenError: (
        "Il servizio verso il portale è temporaneamente sospeso per protezione. "
        "Riprova tra qualche minuto."
    ),
    ConflictError: "L'elemento è già presente oppure è cambiato nel frattempo.",
    ConsentRequiredError: texts.CONSENT_REQUIRED,
    DecryptionError: ("Non è stato possibile leggere un dato protetto. Contatta l'amministratore."),
    ExpiredError: texts.EXPIRED_ACTION,
    LockUnavailableError: (
        "Un'altra operazione è già in corso su questo account. Riprova tra poco."
    ),
    NoAvailability: texts.BOOK_NO_SLOTS,
    NotFoundError: texts.NOT_FOUND,
    OptimisticLockError: (
        "L'elemento è stato modificato nel frattempo. Riapri la schermata e riprova."
    ),
    PdfUnavailable: texts.APPOINTMENT_PDF_UNAVAILABLE,
    PermissionDeniedError: texts.PERMISSION_DENIED,
    PortalCaptchaRequired: (
        "Il portale richiede una verifica manuale (CAPTCHA o secondo fattore). "
        "Accedi dal sito ufficiale e riprova più tardi."
    ),
    PortalContractChanged: (
        "Il portale è cambiato e la lettura automatica non è più affidabile. "
        "L'amministratore è stato avvisato."
    ),
    PortalRateLimited: (
        "Il portale sta limitando le richieste. Riprovo automaticamente più tardi."
    ),
    PortalUnavailable: ("Il portale non è raggiungibile in questo momento. Riprova più tardi."),
    QuotaExceededError: "Hai raggiunto il limite consentito per questa risorsa.",
    RateLimitedError: "Hai eseguito troppe richieste. Attendi qualche istante.",
    ReauthenticationRequired: (
        "La sessione del portale è scaduta. Aggiorna le credenziali da "
        "<b>Account → Aggiorna credenziali</b>."
    ),
    SlotNoLongerAvailable: texts.BOOK_SLOT_LOST,
    ValidationError: "Dato non valido. Controlla il formato richiesto e riprova.",
}


def describe_error(error: DomainError, correlation_id: str = "") -> str:
    """Return the Italian message for a domain error."""
    for error_type in type(error).__mro__:
        if error_type in _MESSAGES:
            message = _MESSAGES[error_type]
            break
    else:
        return texts.GENERIC_ERROR.format(cid=correlation_id)
    if correlation_id and isinstance(
        error, PortalContractChanged | BookingAmbiguous | CancellationAmbiguous
    ):
        return f"{message}\nCodice di riferimento: <code>{correlation_id}</code>"
    return message
