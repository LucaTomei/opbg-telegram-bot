# Integrazione sanitizzata con il portale OPBG

## Premessa

Base URL osservato:

```text
https://prenotazioni.ospedalebambinogesu.it
```

L'integrazione è basata su form HTML, sessione cookie e alcuni endpoint JSON. Non è stata identificata un'API pubblica stabile. Tutto deve essere isolato dietro `PortalClient` e parser versionati.

Il codice non deve contenere valori reali provenienti dalle catture.

## Sessione e CSRF

- Cookie di sessione osservato: `opbg-prenotazione`.
- Le pagine includono un token CSRF in `<input name="_token">` e/o `<meta name="csrf-token">`.
- I POST form inviano `_token`.
- La richiesta AJAX disponibilità usa `X-CSRF-TOKEN`.
- Cookie jar, token e stato di avanzamento appartengono a un singolo account e non sono condivisibili.
- Il portale usa stato server-side tra i POST di `/appointments/create`: non ricreare una fase su una sessione diversa.

## Login

### Recupero form

```http
GET /login
```

Estrarre il form con action `/login` e i campi hidden correnti.

### Invio

```http
POST /login
Content-Type: application/x-www-form-urlencoded
```

Campi osservati:

```text
_token
email
password
confirm
to_service
```

Non hardcodare valori hidden: copiarli dal form e sovrascrivere solo email/password.

### Verifica

Una risposta `302` da sola non basta. Verificare almeno uno dei seguenti:

- `GET /appointments` restituisce 200 e pagina autenticata;
- `GET /user/info` restituisce 200 e JSON coerente.

`/user/info` può includere access/refresh token: non sono necessari al bot, non devono essere loggati né mostrati.

## Inizio prenotazione LAPI

```http
GET /appointments/create?service_type=lapi
```

La pagina contiene il form `#appointment-form`, il select `tax_code`, privacy/newsletter e CSRF. Le option rappresentano i pazienti disponibili per l'account.

Il parser pazienti deve produrre un modello interno con identificativo cifrato, etichetta cifrata e relazione, senza esporre il tax code nei log.

### Selezione paziente

```http
POST /appointments/create
```

Campi osservati:

```text
_token=<current>
tax_code=<selected option value>
privacy=1
newsletter=1
```

I valori di privacy/newsletter devono provenire dal form o essere validati; non modificare la preferenza newsletter in modo silenzioso. Se il portale richiede un valore e l'utente non lo ha mai scelto, gestire esplicitamente il caso.

## Ricerca prestazioni e medici

Entrambi gli endpoint sono POST form e restituiscono JSON.

### Prestazioni

```http
POST /service-autocomplete
```

Campi:

```text
_token
service=<query text>
tax_code=<selected patient>
```

Schema osservato, semplificato:

```json
[
  {
    "description": "string",
    "message": "string",
    "list": [
      {
        "costoPrestazione": "string|null",
        "descPrestazione": "string",
        "disponibilitaVisibileOnLine": true,
        "gruppoErogabile": 0,
        "idModalitaPreparazione": 0,
        "idPrestazione": 0,
        "idSpecificazione": null,
        "prenOnLine": true,
        "prenSportello": false,
        "prenTelefonica": false,
        "testoModalitaPreparazione": "string"
      }
    ]
  }
]
```

### Medici

```http
POST /medici-autocomplete
```

Campi:

```text
_token
service=<query text>
tax_code=<selected patient>
```

Schema osservato:

```json
[
  {
    "descrizione": "string",
    "cognome": "string",
    "nome": "string",
    "sesso": "MAS|FEM|string"
  }
]
```

Il bot deve mostrare risultati esatti e paginati. Non selezionare automaticamente il primo risultato.

## Invio della scelta

### Scelta per prestazione

```http
POST /appointments/create
```

Campi osservati:

```text
_token
id_specification
servicetype=lapi
service
service_name
```

`service` e `service_name` devono derivare dalla risposta/DOM della scelta, non da testo libero non validato.

### Scelta per medico

```http
POST /appointments/create
```

Campi osservati:

```text
_token
id_specification
servicetype=lapi
medico
```

Il valore medico è derivato dalla voce selezionata. Conservare query e identificativi in modo da poter rifare l'autocomplete quando il portale cambia valori interni.

## Disponibilità

Il portale può rendere le disponibilità direttamente nell'HTML della fase successiva oppure attraverso la chiamata osservata:

```http
POST /appointments/disponibilita
X-CSRF-TOKEN: <meta csrf-token>
Content-Type: application/x-www-form-urlencoded
```

Campi:

```text
idPrestazione=<portal service id>
idUnitaErogante[]=<one or more location ids>
```

Schema JSON semplificato:

```json
{
  "locations": {
    "<location-id>": "location label"
  },
  "locations_dates": [
    {
      "YYYY-MM-DD": [
        {}
      ]
    }
  ],
  "dates_locations": {
    "YYYY-MM-DD": {
      "HH:mm": [
        {
          "codiceProposta": "string",
          "dataProposta": "string",
          "descAmbulatorio": "string",
          "descStrutturaErogante": "string",
          "idAmbulatorio": 0,
          "idStrutturaErogante": 0,
          "costoPrestazione": "string",
          "descAgenda": "string",
          "idAgenda": 0,
          "idPrestazione": "string"
        }
      ]
    }
  },
  "doctors": {
    "<doctor-id>": "doctor label"
  }
}
```

Il parser deve normalizzare ogni proposta in `PortalSlot` con fingerprint deterministico basato su account/paziente/servizio o medico/sede/data/ora/codice proposta. Non assumere che tutte le chiavi siano sempre presenti.

## Selezione slot

La fase HTML include hidden fields per ogni proposta. L'invio osservato è:

```http
POST /appointments/create
```

Campi:

```text
_token
id_proposal
location_details
location_name
selected_date
session_id_prestazione
session_name_prestazione
```

Usare i valori del form/proposta restituita dal portale. Non ricostruirli da stringhe Telegram.

## Conferma finale

La pagina successiva contiene un form `#appointment-form` con bottone “Conferma”. Invio osservato:

```http
POST /appointments/create
```

Campi:

```text
_token=<current>
confirm=true
```

Il portale ha risposto 200. Per evitare doppie prenotazioni:

1. acquisire lock/idempotency key prima del POST;
2. inviare una sola volta;
3. chiamare `GET /appointments`;
4. confrontare snapshot precedente e successivo;
5. identificare il nuovo appuntamento coerente con paziente, prestazione/medico, sede e data/ora;
6. se l'esito resta ambiguo, registrare `AMBIGUOUS`, non ritentare automaticamente e avvisare utente/admin.

La cattura osservata indica pagamento allo sportello e promemoria da portare il giorno della visita. Il bot non gestisce pagamenti.

## Elenco appuntamenti

```http
GET /appointments
```

Struttura osservata:

- contenitore `#appointments-list`;
- elemento `.appointment` con stato tra le classi, ad esempio `pending`;
- coppie `.appointment-info__label` / `.appointment-info__text`;
- etichette osservate: prestazione, ora, medico, paziente e altre informazioni di data/sede/stato;
- azione disdetta `.btn-appointment-list--cancel` con `data-href`;
- link PDF `/appointments/{appointment_id}/pdf-download`.

Il parser deve basarsi sulle etichette normalizzate, non sull'indice fisso dei campi. Conservare l'ID esterno come stringa.

È stato osservato anche:

```http
GET /appointments/history
```

Se la pagina è disponibile, analizzarla con lo stesso parser; una sua indisponibilità non deve bloccare gli appuntamenti correnti.

## Disdetta

Route osservata nell'HTML e chiamata catturata:

```http
GET /appointments/{appointment_id}/cancel?tax_code=<patient-tax-code>
```

Esito osservato: redirect 302 verso `/appointments`.

Prima della chiamata il bot richiede doppia conferma e lock. Dopo la chiamata deve ricaricare l'elenco e verificare che l'appuntamento sia assente o abbia stato cancellato. Non considerare il solo redirect come successo.

## PDF

```http
GET /appointments/{appointment_id}/pdf-download
```

Esito osservato: `200 application/pdf`.

Validazioni obbligatorie:

- proprietario account e appuntamento;
- status 200;
- `Content-Type` PDF;
- contenuto inizia con `%PDF-`;
- dimensione non supera configurazione;
- nessuna persistenza permanente;
- nome file sanitizzato;
- cancellazione buffer/file temporaneo immediata dopo invio o scadenza link.

## Errori e cambi portale

Mappare in eccezioni tipizzate:

- `AuthenticationFailed`
- `ReauthenticationRequired`
- `PortalRateLimited`
- `PortalUnavailable`
- `PortalContractChanged`
- `NoAvailability`
- `SlotNoLongerAvailable`
- `BookingAmbiguous`
- `BookingRejected`
- `CancellationAmbiguous`
- `PdfUnavailable`

Un cambio di selettore/schema deve produrre correlation ID, risposta redatta e circuit breaker, mai fallback pericolosi o retry distruttivi.

## Dati che non devono entrare nei fixture

- email/password;
- cookie e token;
- CSRF reali;
- tax code;
- nomi reali di pazienti/utenti;
- ID appuntamenti catturati;
- PDF reali;
- HTML/JSON grezzi delle catture.

Creare fixture sintetici ma strutturalmente equivalenti per ogni fase e per varianti mancanti/errore.

