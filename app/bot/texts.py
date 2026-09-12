"""Italian user-facing strings.

Every message is written so that the information is carried by the text, never
by an emoji alone; emoji are only decoration.
"""

from __future__ import annotations

from typing import Final

# --- navigation --------------------------------------------------------------
BTN_BOOK: Final[str] = "🗓 Prenota"
BTN_WATCHES: Final[str] = "🔔 Monitoraggi"
BTN_APPOINTMENTS: Final[str] = "📅 Appuntamenti"
BTN_ACCOUNTS: Final[str] = "👤 Account"
BTN_SETTINGS: Final[str] = "⚙️ Impostazioni"
BTN_HELP: Final[str] = "ℹ️ Aiuto"
BTN_ADMIN: Final[str] = "🛡 Amministrazione"

BTN_BACK: Final[str] = "◀️ Indietro"
BTN_MENU: Final[str] = "🏠 Menu"
BTN_CANCEL: Final[str] = "❌ Annulla"
BTN_REFRESH: Final[str] = "🔄 Aggiorna"
BTN_PREV_PAGE: Final[str] = "◀️ Precedente"
BTN_NEXT_PAGE: Final[str] = "Successiva ▶️"

# --- generic -----------------------------------------------------------------
MAIN_MENU: Final[str] = (
    "<b>Menu principale</b>\n"
    "Scegli un'operazione dalla tastiera qui sotto.\n\n"
    "• <b>Prenota</b>: cerca una prestazione o un medico e prenota subito.\n"
    "• <b>Monitoraggi</b>: controlla automaticamente le disponibilità.\n"
    "• <b>Appuntamenti</b>: elenco, promemoria PDF e disdetta.\n"
    "• <b>Account</b>: collega e gestisci gli account del portale.\n"
    "• <b>Impostazioni</b>: notifiche, orari silenziosi e privacy."
)

CANCELLED: Final[str] = "Operazione annullata. Sei tornato al menu principale."
NOTHING_TO_CANCEL: Final[str] = "Non c'è nessuna operazione in corso da annullare."
GENERIC_ERROR: Final[str] = (
    "Si è verificato un errore tecnico. Riprova più tardi.\n"
    "Codice di riferimento: <code>{cid}</code>"
)
NOT_AUTHORIZED: Final[str] = (
    "Questo bot è privato. Per usarlo serve un invito valido: apri il link "
    "ricevuto oppure invia <code>/start CODICE-INVITO</code>."
)
USER_SUSPENDED: Final[str] = (
    "Il tuo accesso è sospeso. Contatta l'amministratore per maggiori informazioni."
)
USER_BLOCKED: Final[str] = "Il tuo accesso è stato revocato."
CONSENT_REQUIRED: Final[str] = (
    "Prima di continuare devi accettare l'informativa. Usa /start per rileggerla."
)
EXPIRED_ACTION: Final[str] = "Questa richiesta è scaduta. Ripeti l'operazione dal menu."
ALREADY_HANDLED: Final[str] = "Questa richiesta è già stata elaborata."
PERMISSION_DENIED: Final[str] = "Non hai i permessi per questa operazione."
NOT_FOUND: Final[str] = "Elemento non trovato o non più disponibile."

# --- onboarding --------------------------------------------------------------
WELCOME_NEEDS_INVITE: Final[str] = (
    "<b>Assistente prenotazioni OPBG</b>\n\n"
    "Questo bot è privato e su invito. Invia <code>/start CODICE-INVITO</code> "
    "con il codice che ti è stato fornito."
)
INVITE_INVALID: Final[str] = "Codice di invito non valido."
INVITE_EXPIRED: Final[str] = "Codice di invito scaduto."
INVITE_CONSUMED: Final[str] = "Codice di invito già utilizzato o revocato."
INVITE_ACCEPTED: Final[str] = "Invito accettato. Ora leggi l'informativa qui sotto."

CONSENT_TEXT: Final[str] = (
    "<b>Informativa e condizioni d'uso</b>\n\n"
    "• Questo è un servizio <b>privato e non ufficiale</b>, non gestito "
    "dall'Ospedale Pediatrico Bambino Gesù.\n"
    "• Il bot accede al portale di prenotazione <b>per tuo conto</b>, usando "
    "credenziali che fornisci tu tramite una pagina HTTPS temporanea.\n"
    "• Email, password, cookie di sessione e identificativi dei pazienti sono "
    "<b>cifrati</b> nel database.\n"
    "• Vengono trattati dati relativi a prestazioni sanitarie: le chat Telegram "
    "<b>non sono cifrate end-to-end</b>.\n"
    "• L'amministratore del servizio può vedere i dati necessari alla gestione; "
    "ogni accesso ai dati completi viene registrato.\n"
    "• Puoi eliminare in qualunque momento i tuoi dati da <b>Impostazioni</b>.\n"
    "• Il servizio non aggira CAPTCHA, autenticazione a più fattori o limiti "
    "del portale, e non fornisce consulenza medica.\n\n"
    "Versione informativa: <b>{version}</b>"
)
CONSENT_ACCEPT: Final[str] = "✅ Accetto"
CONSENT_DECLINE: Final[str] = "❌ Non accetto"
CONSENT_DECLINED: Final[str] = (
    "Senza accettazione non è possibile usare il servizio. Puoi riprovare con /start."
)
CONSENT_ACCEPTED: Final[str] = (
    "Grazie. Ora puoi collegare il tuo primo account del portale da <b>Account</b>."
)

HELP_TEXT: Final[str] = (
    "<b>Guida rapida</b>\n\n"
    "<b>1. Collega un account</b> — <i>Account → Collega account</i>: "
    "il bot chiede nome, email e password del portale. Il messaggio con la "
    "password viene cancellato subito dalla chat.\n\n"
    "<b>2. Prenota</b> — <i>Prenota</i>: scegli account, paziente, prestazione "
    "o medico, sedi e filtri, poi conferma due volte.\n\n"
    "<b>3. Monitora</b> — se non c'è disponibilità puoi creare un monitoraggio "
    "che avvisa quando compaiono nuovi posti oppure prenota automaticamente.\n\n"
    "<b>4. Gestisci</b> — <i>Appuntamenti</i> per elenco, promemoria PDF e "
    "disdetta con doppia conferma.\n\n"
    "<b>Comandi</b>\n"
    "/menu — menu principale\n"
    "/cancel — annulla l'operazione in corso\n"
    "/status — stato del tuo profilo\n"
    "/privacy — informativa\n"
    "/help — questa guida"
)

PRIVACY_SHORT: Final[str] = (
    "<b>Privacy</b>\n\n"
    "Dati cifrati a riposo: email, password, cookie, identificativi dei "
    "pazienti e contenuti sensibili delle conversazioni.\n"
    "Il messaggio con la password viene cancellato dalla chat appena letto.\n"
    "Password, cookie e token non sono mai mostrati, nemmeno all'amministratore.\n"
    "Le notifiche e i PDF vengono inviati solo alla tua chat privata.\n"
    "I promemoria PDF non vengono conservati sul server.\n\n"
    "Puoi esportare le tue impostazioni o eliminare tutti i tuoi dati da "
    "<b>Impostazioni → Privacy</b>."
)

# --- accounts ----------------------------------------------------------------
ACCOUNTS_EMPTY: Final[str] = (
    "Non hai ancora collegato nessun account del portale.\nUsa <b>Collega account</b> per iniziare."
)
ACCOUNTS_TITLE: Final[str] = "<b>Account del portale</b>\nSeleziona un account per gestirlo."
ACCOUNT_CONNECT_BUTTON: Final[str] = "🔐 Collega account"
ACCOUNT_ASK_LABEL: Final[str] = (
    "<b>Collegamento account</b>\n\n"
    "Dai un nome a questo account, per riconoscerlo nell'elenco.\n"
    "Esempio: <i>Mio account</i> oppure <i>Famiglia Rossi</i>."
)
ACCOUNT_ASK_EMAIL: Final[str] = "Ora invia l'<b>email</b> con cui accedi al portale."
ACCOUNT_ASK_PASSWORD: Final[str] = (
    "Infine invia la <b>password</b> del portale.\n\n"
    "🔒 Il messaggio con la password viene <b>cancellato subito</b> dalla chat "
    "appena letto, e la password viene salvata cifrata: non resta in chiaro da "
    "nessuna parte. Se la cancellazione non riesce te lo segnalo, così puoi "
    "eliminarla a mano."
)
ACCOUNT_CREDENTIALS_DELETED: Final[str] = "🔒 Messaggio con la password eliminato."
ACCOUNT_CREDENTIALS_NOT_DELETED: Final[str] = (
    "⚠️ Non sono riuscito a eliminare il messaggio con la password: cancellalo tu dalla chat."
)
ACCOUNT_CONNECTING: Final[str] = "Verifico le credenziali sul portale…"
ACCOUNT_CONNECTED: Final[str] = (
    "✅ Account <b>{label}</b> collegato.\nPazienti sincronizzati: <b>{patients}</b>."
)
ACCOUNT_CONNECT_FAILED: Final[str] = (
    "Credenziali non valide oppure account non disponibile. "
    "Controlla i dati e riprova da <b>Account</b>."
)
ACCOUNT_REAUTH_START: Final[str] = (
    "<b>Aggiorna credenziali</b>\n\nInvia l'<b>email</b> del portale per l'account <b>{label}</b>."
)
ACCOUNT_REAUTH_DONE: Final[str] = (
    "✅ Credenziali aggiornate. I monitoraggi sospesi sono stati riattivati."
)
ACCOUNT_STATUS_LABELS: Final[dict[str, str]] = {
    "ACTIVE": "attivo",
    "DISABLED": "disabilitato",
    "REAUTH_REQUIRED": "richiede nuova autenticazione",
    "LOCKED": "bloccato",
    "DELETED": "rimosso",
}
ACCOUNT_TEST_OK: Final[str] = "Accesso al portale verificato correttamente."
ACCOUNT_TEST_FAILED: Final[str] = (
    "Non è stato possibile verificare l'accesso. Aggiorna le credenziali."
)
ACCOUNT_RENAMED: Final[str] = "Etichetta aggiornata."
ACCOUNT_RENAME_PROMPT: Final[str] = (
    "Invia la nuova etichetta per questo account (massimo 64 caratteri)."
)
ACCOUNT_DISABLED: Final[str] = "Account disabilitato. I monitoraggi collegati sono in pausa."
ACCOUNT_ENABLED: Final[str] = "Account riattivato."
ACCOUNT_REMOVED: Final[str] = "Account rimosso. Credenziali e sessione sono state cancellate."
ACCOUNT_REMOVE_WARNING: Final[str] = (
    "Rimuovendo questo account verranno eliminati credenziali e sessione, e "
    "<b>{watches}</b> monitoraggi verranno eliminati.\n\nVuoi procedere?"
)
PATIENTS_SYNCED: Final[str] = (
    "Pazienti sincronizzati: <b>{count}</b>. Non più disponibili: <b>{deactivated}</b>."
)
PATIENTS_EMPTY: Final[str] = "Nessun paziente disponibile per questo account. Prova ad aggiornare."

# --- booking -----------------------------------------------------------------
BOOK_CHOOSE_ACCOUNT: Final[str] = "<b>Prenotazione</b>\nScegli l'account del portale."
BOOK_CHOOSE_PATIENT: Final[str] = "<b>Prenotazione</b>\nScegli il paziente."
BOOK_CHOOSE_SEARCH_TYPE: Final[str] = (
    "<b>Prenotazione</b>\nVuoi cercare una prestazione o uno specifico medico?"
)
BOOK_SEARCH_SERVICE: Final[str] = "🔎 Cerca prestazione"
BOOK_SEARCH_DOCTOR: Final[str] = "🩺 Cerca medico"
BOOK_ASK_SERVICE_QUERY: Final[str] = (
    "Scrivi il nome della prestazione da cercare (almeno 3 caratteri).\n"
    "Esempio: <i>visita oculistica</i>."
)
BOOK_ASK_DOCTOR_QUERY: Final[str] = "Scrivi il cognome del medico da cercare (almeno 3 caratteri)."
BOOK_NO_RESULTS: Final[str] = "Nessun risultato per <b>{query}</b>. Prova con un altro testo."
BOOK_RESULTS_TITLE: Final[str] = "<b>Risultati per «{query}»</b>\nSeleziona la voce esatta."
BOOK_SERVICE_NOT_ONLINE: Final[str] = (
    "⚠️ Questa prestazione non risulta prenotabile online dal portale. "
    "Puoi comunque provare, ma potrebbe non mostrare disponibilità."
)
BOOK_CHOOSE_LOCATIONS: Final[str] = (
    "<b>Sedi</b>\nSeleziona una o più sedi, oppure scegli tutte le sedi."
)
#: The selection marker is added by the keyboard, never baked in here: a label
#: carrying its own tick shows one while switched off, and two while on.
BOOK_ALL_LOCATIONS: Final[str] = "Tutte le sedi"
BOOK_LOCATIONS_NONE: Final[str] = "Seleziona almeno una sede per continuare."
BOOK_NO_LOCATIONS: Final[str] = (
    "Il portale non ha restituito sedi per questa ricerca. "
    "Verranno usate tutte le sedi disponibili."
)
BOOK_FILTERS_TITLE: Final[str] = (
    "<b>Filtri</b>\nImposta i filtri oppure mostra tutte le disponibilità."
)
BTN_FILTER_PERIOD: Final[str] = "📆 Periodo"
BTN_FILTER_WEEKDAYS: Final[str] = "🗓 Giorni settimana"
BTN_FILTER_TIMES: Final[str] = "🕐 Fasce orarie"
BTN_FILTER_EXCLUDED: Final[str] = "🚫 Date escluse"
BTN_FILTER_SORT: Final[str] = "↕️ Ordinamento"
BTN_FILTER_LEAD: Final[str] = "⏱ Anticipo minimo"
BTN_SHOW_AVAILABILITY: Final[str] = "✅ Mostra disponibilità"
PORTAL_WORKING: Final[str] = (
    "⏳ Sto interrogando il portale…\n\n"
    "Può richiedere fino a un minuto: il portale è spesso lento e i tentativi "
    "vengono ripetuti in automatico."
)
BOOK_DOCTOR_SERVICE_TITLE: Final[str] = (
    "<b>Prestazione</b>\n"
    "Il medico <b>{doctor}</b> esegue più prestazioni.\n"
    "Scegli quale ti serve: il portale non mostra le disponibilità senza."
)
BOOK_NO_SLOTS: Final[str] = (
    "Nessuna disponibilità compatibile con i filtri attuali.\n"
    "Puoi modificare i filtri oppure creare un monitoraggio."
)
BOOK_NOT_ONLINE: Final[str] = (
    "Questa prestazione non è prenotabile online.\n\n"
    "Il portale non offre sedi per questa scelta: la prenotazione va fatta "
    "telefonicamente o allo sportello. Prova con un'altra prestazione."
)
BOOK_SLOTS_TITLE: Final[str] = (
    "<b>Disponibilità</b>\nTrovate <b>{count}</b> proposte compatibili. Seleziona uno slot."
)
BOOK_SUMMARY_TITLE: Final[str] = "<b>Riepilogo prenotazione</b>"
BOOK_CONFIRM_STEP1: Final[str] = "➡️ Procedi"
BOOK_CONFIRM_STEP2: Final[str] = "✅ Conferma prenotazione"
BOOK_CONFIRM_PROMPT: Final[str] = (
    "Confermi la prenotazione? La conferma finale scade tra <b>{seconds} secondi</b>."
)
BOOK_SUCCESS: Final[str] = (
    "✅ <b>Prenotazione confermata</b>\n\n{details}\n\n"
    "Il pagamento avviene allo sportello. Porta il promemoria il giorno della visita."
)
BOOK_SLOT_LOST: Final[str] = "Lo slot selezionato non è più disponibile. Ho aggiornato l'elenco."
BOOK_AMBIGUOUS: Final[str] = (
    "⚠️ <b>Esito incerto</b>\n\nLa conferma è stata inviata ma non è stato possibile "
    "verificarla nell'elenco appuntamenti. <b>Non ho ritentato</b>.\n"
    "Controlla <b>Appuntamenti</b> prima di prenotare di nuovo.\n"
    "Codice di riferimento: <code>{cid}</code>"
)
BOOK_REJECTED: Final[str] = "Il portale ha rifiutato la prenotazione."
BTN_DOWNLOAD_PDF: Final[str] = "📄 Scarica promemoria"
BTN_MY_APPOINTMENTS: Final[str] = "📅 I miei appuntamenti"
BTN_CREATE_WATCH: Final[str] = "🔔 Crea monitoraggio"

# --- filters -----------------------------------------------------------------
FILTER_PERIOD_TITLE: Final[str] = (
    "<b>Periodo</b>\nScegli una finestra mobile oppure imposta un intervallo fisso."
)
FILTER_PERIOD_ROLLING: Final[str] = "Da oggi a +{days} giorni"
FILTER_PERIOD_FIXED: Final[str] = "📅 Intervallo fisso"
FILTER_PERIOD_ASK_RANGE: Final[str] = (
    "Invia l'intervallo nel formato <code>GG/MM/AAAA - GG/MM/AAAA</code>.\n"
    "Esempio: <code>01/09/2026 - 30/09/2026</code>."
)
FILTER_PERIOD_INVALID: Final[str] = "Formato non valido. Usa <code>GG/MM/AAAA - GG/MM/AAAA</code>."
FILTER_WEEKDAYS_TITLE: Final[str] = (
    "<b>Giorni della settimana</b>\nTocca un giorno per attivarlo o disattivarlo."
)
FILTER_TIMES_TITLE: Final[str] = (
    "<b>Fasce orarie</b>\nAttualmente: {current}\n"
    "Aggiungi una fascia oppure scegli «qualsiasi ora»."
)
FILTER_TIMES_ANY: Final[str] = "🕒 Qualsiasi ora"
FILTER_TIMES_ADD: Final[str] = "➕ Aggiungi fascia"
FILTER_TIMES_ASK: Final[str] = (
    "Invia la fascia nel formato <code>HH:MM-HH:MM</code>.\nEsempio: <code>08:00-12:30</code>."
)
FILTER_TIMES_INVALID: Final[str] = "Formato non valido. Usa <code>HH:MM-HH:MM</code>."
FILTER_EXCLUDED_TITLE: Final[str] = (
    "<b>Date escluse</b>\nAttualmente: {current}\nAggiungi o rimuovi una data."
)
FILTER_EXCLUDED_ADD: Final[str] = "➕ Aggiungi data"
FILTER_EXCLUDED_ASK: Final[str] = "Invia la data da escludere nel formato <code>GG/MM/AAAA</code>."
FILTER_EXCLUDED_INVALID: Final[str] = "Data non valida. Usa <code>GG/MM/AAAA</code>."
FILTER_SORT_TITLE: Final[str] = "<b>Ordinamento</b>\nScegli come ordinare le proposte."
FILTER_SORT_LABELS: Final[dict[str, str]] = {
    "EARLIEST": "Prima disponibile",
    "PREFERRED_LOCATION_FIRST": "Sede preferita, poi data",
    "DATE_TIME_ASC": "Data e ora crescenti",
}
FILTER_LEAD_TITLE: Final[str] = (
    "<b>Anticipo minimo</b>\nNon proporre slot prima di questo intervallo da adesso."
)
FILTER_CLEARED: Final[str] = "Filtri reimpostati."

# --- watches -----------------------------------------------------------------
WATCHES_EMPTY: Final[str] = (
    "Non hai monitoraggi attivi.\nCreane uno dal flusso di prenotazione quando "
    "non trovi disponibilità."
)
WATCHES_TITLE: Final[str] = "<b>Monitoraggi</b>\nSeleziona un monitoraggio per gestirlo."
WATCH_MODE_LABELS: Final[dict[str, str]] = {
    "NOTIFY": "solo notifica",
    "AUTOBOOK": "auto-prenotazione",
    "AUTOBOOK_NOTIFY": "auto-prenotazione + notifica",
}
WATCH_STATUS_LABELS: Final[dict[str, str]] = {
    "DRAFT": "bozza",
    "ACTIVE": "attivo",
    "PAUSED": "in pausa",
    "COMPLETED": "completato",
    "ERROR": "in errore",
    "DELETED": "eliminato",
}
WATCH_ASK_NAME: Final[str] = "Come vuoi chiamare questo monitoraggio?"
WATCH_ASK_MODE: Final[str] = (
    "<b>Modalità</b>\n"
    "• <b>Solo notifica</b>: ti avviso, prenoti tu.\n"
    "• <b>Auto-prenota</b>: prenoto io il primo slot compatibile.\n"
    "• <b>Auto-prenota + notifica</b>: entrambe."
)
WATCH_ASK_INTERVAL: Final[str] = (
    "<b>Frequenza di controllo</b>\nIntervallo minimo consentito: {minimum} secondi."
)
WATCH_ASK_NOTIFY_MODE: Final[str] = (
    "<b>Notifiche</b>\nVuoi essere avvisato solo dei nuovi slot oppure a ogni controllo?"
)
WATCH_ASK_COMPLETION: Final[str] = (
    "<b>Dopo una prenotazione riuscita</b>\nCosa deve fare il monitoraggio?"
)
WATCH_AUTOBOOK_WARNING: Final[str] = (
    "⚠️ <b>Attenzione</b>\nCon l'auto-prenotazione il bot prenoterà "
    "<b>automaticamente</b> il primo slot compatibile con i filtri, senza "
    "chiederti conferma. Confermi?"
)
WATCH_CREATED: Final[str] = "Monitoraggio creato e attivo."
WATCH_PAUSED: Final[str] = "Monitoraggio in pausa. Controlli e auto-prenotazione sospesi."
WATCH_RESUMED: Final[str] = "Monitoraggio riattivato."
WATCH_DELETED: Final[str] = "Monitoraggio eliminato."
WATCH_DELETE_WARNING: Final[str] = (
    "Vuoi eliminare definitivamente il monitoraggio <b>{name}</b>?\n"
    "L'operazione non è reversibile dall'interfaccia."
)
WATCH_NOTIFICATIONS_ON: Final[str] = "Notifiche attivate per questo monitoraggio."
WATCH_NOTIFICATIONS_OFF: Final[str] = (
    "Notifiche disattivate. Attenzione: l'auto-prenotazione resta attiva se configurata."
)
WATCH_DUPLICATED: Final[str] = (
    "Monitoraggio duplicato. La copia è <b>in pausa</b>: controllala e attivala."
)
WATCH_RUN_SCHEDULED: Final[str] = "Controllo richiesto. Verrà eseguito entro pochi secondi."
WATCH_RUN_TOO_SOON: Final[str] = (
    "Hai già richiesto un controllo di recente. Riprova tra {seconds} secondi."
)
WATCH_RENAMED: Final[str] = "Nome aggiornato."

# --- appointments ------------------------------------------------------------
APPOINTMENTS_EMPTY: Final[str] = "Non ci sono appuntamenti in programma."
APPOINTMENTS_TITLE: Final[str] = "<b>Appuntamenti</b>"
APPOINTMENTS_SYNCED: Final[str] = (
    "Sincronizzazione completata: <b>{created}</b> nuovi, <b>{updated}</b> aggiornati, "
    "<b>{cancelled}</b> non più presenti."
)
APPOINTMENT_CANCEL_WARNING: Final[str] = (
    "Vuoi disdire questo appuntamento?\n\n{details}\n\n"
    "L'operazione viene inviata al portale una sola volta."
)
APPOINTMENT_CANCEL_CONFIRM: Final[str] = (
    "Conferma definitiva: disdire l'appuntamento? La conferma scade tra <b>{seconds} secondi</b>."
)
APPOINTMENT_CANCELLED: Final[str] = "✅ Disdetta confermata e verificata sul portale."
APPOINTMENT_CANCEL_AMBIGUOUS: Final[str] = (
    "⚠️ Esito incerto: l'appuntamento risulta ancora presente sul portale.\n"
    "Non ho ritentato. Aggiorna l'elenco e verifica.\n"
    "Codice di riferimento: <code>{cid}</code>"
)
APPOINTMENT_PDF_SENT: Final[str] = "Promemoria inviato."
APPOINTMENT_PDF_UNAVAILABLE: Final[str] = "Il promemoria non è disponibile per questo appuntamento."
BTN_CANCEL_APPOINTMENT: Final[str] = "🗑 Disdici"
BTN_APPOINTMENT_DETAILS: Final[str] = "🔍 Dettagli"

# --- settings ----------------------------------------------------------------
SETTINGS_TITLE: Final[str] = "<b>Impostazioni</b>"
SETTINGS_NOTIFICATIONS_ON: Final[str] = "Notifiche globali attivate."
SETTINGS_NOTIFICATIONS_OFF: Final[str] = (
    "Notifiche globali disattivate. Riceverai comunque gli esiti di "
    "prenotazione e disdetta e le richieste di nuova autenticazione."
)
SETTINGS_QUIET_ASK: Final[str] = (
    "Invia l'intervallo di silenzio nel formato <code>HH:MM-HH:MM</code>, "
    "oppure <code>off</code> per disattivarlo."
)
SETTINGS_QUIET_SET: Final[str] = "Orari silenziosi aggiornati."
SETTINGS_QUIET_OFF: Final[str] = "Orari silenziosi disattivati."
SETTINGS_PAGE_SIZE_ASK: Final[str] = "Quante voci per pagina? (da 3 a 20)"
SETTINGS_MAX_SLOTS_ASK: Final[str] = "Quante proposte mostrare al massimo? (da 1 a 200)"
SETTINGS_SAVED: Final[str] = "Impostazione salvata."
SETTINGS_EXPORT_TITLE: Final[str] = "<b>Le tue impostazioni</b>"
SETTINGS_DELETE_WARNING: Final[str] = (
    "⚠️ Verranno eliminati definitivamente: account del portale collegati, "
    "credenziali, pazienti, monitoraggi e appuntamenti in cache.\n\n"
    "Vuoi procedere?"
)
SETTINGS_DELETE_CONFIRM: Final[str] = (
    "Conferma definitiva: eliminare tutti i tuoi dati? La conferma scade tra "
    "<b>{seconds} secondi</b>."
)
SETTINGS_DELETED: Final[str] = (
    "Tutti i tuoi dati sono stati eliminati. Per tornare a usare il servizio serve un nuovo invito."
)

# --- status ------------------------------------------------------------------
STATUS_TEMPLATE: Final[str] = (
    "<b>Il tuo profilo</b>\n"
    "Stato: <b>{status}</b>\n"
    "Ruolo: <b>{role}</b>\n"
    "Account collegati: <b>{accounts}</b>\n"
    "Monitoraggi attivi: <b>{watches}</b>\n"
    "Appuntamenti in programma: <b>{appointments}</b>\n"
    "Notifiche: <b>{notifications}</b>\n"
    "Orari silenziosi: <b>{quiet}</b>\n"
    "Informativa accettata: <b>{consent}</b>"
)

# --- admin -------------------------------------------------------------------
ADMIN_TITLE: Final[str] = "<b>Amministrazione</b>\nScegli una sezione."
ADMIN_ONLY: Final[str] = "Sezione riservata agli amministratori."
ADMIN_ASK_REASON: Final[str] = (
    "Indica il motivo di questa operazione (minimo 5 caratteri). Verrà registrato nel log di audit."
)
ADMIN_REASON_TOO_SHORT: Final[str] = "Motivo troppo corto: servono almeno 5 caratteri."
ADMIN_INVITE_CREATED: Final[str] = (
    "Invito creato.\n\nCodice: <code>{code}</code>\n"
    "Link diretto: {link}\n"
    "Usi: <b>{uses}</b> · Scadenza: <b>{expires}</b>\n\n"
    "Il codice è mostrato una sola volta: copialo o inoltra il link."
)
ADMIN_INVITE_REVOKED: Final[str] = "Invito revocato."
ADMIN_INVITE_ASK_USES: Final[str] = "Quanti utilizzi deve avere l'invito? (1-50)"
ADMIN_REVEAL_NOTICE: Final[str] = "🔓 Dati completi (accesso registrato nel log di audit)."
ADMIN_ACTION_DONE: Final[str] = "Operazione completata e registrata nell'audit."
ADMIN_NO_SECRETS: Final[str] = (
    "Password, cookie, token e chiavi non sono accessibili da questa interfaccia."
)
ADMIN_SYSTEM_TITLE: Final[str] = "<b>Stato sistema</b>"
ADMIN_BREAKER_TITLE: Final[str] = "<b>Circuit breaker</b>"
ADMIN_DELEGATED_WARNING: Final[str] = (
    "⚠️ Stai per operare <b>per conto di un altro utente</b>. "
    "L'operazione richiede motivo e doppia conferma."
)

EMPTY_PAGE: Final[str] = "Nessun elemento da mostrare."
PAGE_INDICATOR: Final[str] = "Pagina {page}/{pages}"

BTN_FILTER_LOCATIONS: Final[str] = "🏥 Sedi"
BTN_FILTER_LOCATION_ORDER: Final[str] = "🔢 Ordine preferenza"
BTN_SAVE_WATCH: Final[str] = "💾 Salva modifiche"
FILTER_ORDER_TITLE: Final[str] = (
    "<b>Ordine di preferenza delle sedi</b>\n"
    "Tocca una sede per spostarla più in alto. Le sedi successive restano "
    "comunque incluse: l'ordine conta solo se l'ordinamento è «Sede preferita»."
)
FILTER_ORDER_UNAVAILABLE: Final[str] = (
    "L'ordine di preferenza è disponibile solo quando selezioni sedi specifiche."
)
WATCH_UPDATED: Final[str] = "Monitoraggio aggiornato."
WATCH_UPDATED_HINT: Final[str] = "I nuovi filtri saranno applicati dal prossimo controllo."
WATCH_EDIT_TITLE: Final[str] = "<b>Modifica monitoraggio</b>\nAggiorna i filtri e salva."
