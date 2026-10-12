# Sicurezza

Il bot custodisce credenziali del portale e dati di pazienti, quindi le
segnalazioni di sicurezza hanno la precedenza su tutto il resto.

## Come segnalare una vulnerabilità

**Non aprire una issue pubblica.** Usa la segnalazione privata di GitHub:
[Report a vulnerability](https://github.com/LucaTomei/opbg-telegram-bot/security/advisories/new).

Indica, se puoi:

- versione o commit (`OPBG_TAG`, oppure `sha-<commit>` dell'immagine);
- passi per riprodurre il problema;
- impatto stimato: quali dati o azioni diventano accessibili e a chi.

Non includere credenziali reali, codici fiscali, PDF o altri dati personali:
se servono per la prova, usa dati sintetici.

## Cosa aspettarsi

- conferma di ricezione entro qualche giorno;
- una valutazione e, se la segnalazione è confermata, una correzione su `main`
  con una nuova immagine pubblicata;
- un riconoscimento nell'advisory, se lo desideri.

## Versioni supportate

Solo l'ultima versione su `main` e l'immagine `latest` ricevono correzioni.

## Fuori ambito

- problemi del portale dell'ospedale, che vanno segnalati all'ospedale;
- attacchi che richiedono accesso al server o al file `.env`;
- la mancata cifratura end-to-end delle chat Telegram, che è un limite noto e
  dichiarato nell'informativa del bot.
