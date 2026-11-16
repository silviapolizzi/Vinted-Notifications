# Guida all'uso di Vinted-Notifications

Questa guida spiega come installare, configurare e usare il progetto per ricevere
notifiche in tempo reale sui nuovi annunci Vinted che corrispondono alle tue ricerche.

> Il README ufficiale (in inglese) resta il riferimento per la panoramica delle
> funzionalità. Questa guida è pensata per chi deve **mettere in funzione** e
> **usare** l'applicazione giorno per giorno.

---

## 1. Cosa fa il progetto

Vinted-Notifications monitora una o più ricerche Vinted e, quando compare un nuovo
annuncio, lo salva nel database e invia una notifica su Telegram e/o lo pubblica in
un feed RSS.

In sintesi:

1. lo **scraper** interroga a intervalli regolari le URL di ricerca salvate;
2. gli annunci nuovi vengono filtrati (allowlist paesi, parole vietate, duplicati);
3. la notifica viene messa in una **coda persistente** (`outbox`) e consegnata da
   Telegram; in parallelo viene aggiunta al feed RSS (se attivo);
4. la **Web UI** permette di gestire query, filtri, configurazione e log.

Il progetto usa:

- **SQLite** come database, in `./data/vinted_notifications.db`;
- **Patchright** (Chromium) per ottenere i cookie di Vinted e, se necessario,
  leggere i risultati direttamente dalla pagina (l'API JSON di Vinted è spesso
  bloccata);
- **Flask** per la Web UI (porta 8000) e per il feed RSS (porta 8080);
- **python-telegram-bot** per il bot Telegram;
- più processi Python (scraper, estrattore, dispatcher, Web UI, plugin) coordinati
  tramite il database e delle code.

---

## 2. Requisiti

- **Docker + Docker Compose** (modo consigliato), oppure **Python 3.11+** per
  l'installazione manuale;
- un **token di bot Telegram** se vuoi le notifiche Telegram (si crea con
  [@BotFather](https://t.me/BotFather));
- spazio disco per il database, i log e il profilo del browser;
- con l'installazione manuale serve anche **Chromium scaricato da Patchright**
  (vedi §3.3).

Telegram e RSS sono opzionali: puoi usarne uno solo, entrambi o nessuno (in quel
caso userai solo la Web UI per consultare gli annunci trovati).

---

## 3. Installazione e avvio

### 3.1 Docker Compose (consigliato)

In una cartella vuota:

```bash
curl -O https://raw.githubusercontent.com/Fuyucch1/Vinted-Notifications/main/docker-compose.yml
docker compose up -d
```

Poi apri <http://localhost:8000>.

Il compose pubblicato:

- espone la Web UI sulla porta **8000** e il feed RSS sulla **8080**;
- salva dati e log in volumi Docker nominati (`VN_data`, `VN_logs`), quindi
  sopravvivono a riavvii e aggiornamenti del container;
- esegue il container con filesystem in sola lettura, con `/tmp` in tmpfs e
  `shm_size` dedicato a Chromium.

**Attenzione se lavori su un checkout del repository:** il file
`docker-compose.override.yml` viene caricato automaticamente e fa **compilare
l'immagine locale** invece di scaricare quella pubblicata. Per usare l'immagine
pubblicata ignora l'override:

```bash
docker compose -f docker-compose.yml up -d
```

Per compilare localmente e cambiare le porte sull'host:

```bash
VN_WEB_PORT=8010 VN_RSS_PORT=8081 docker compose up -d --build
```

`VN_WEB_PORT` e `VN_RSS_PORT` cambiano solo il mapping sulla macchina host: le
porte interne al container restano 8000 e 8080.

### 3.2 Docker run (senza Compose)

```bash
mkdir -p data logs
docker run -d \
  --name vinted-notifications \
  -p 8000:8000 \
  -p 8080:8080 \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/logs:/app/logs" \
  --restart unless-stopped \
  fuyucch1/vinted-notifications:latest
```

I due volumi sono importanti: senza di essi perdi database e log quando il
container viene ricreato.

### 3.3 Installazione manuale (self-build)

```bash
git clone https://github.com/Fuyucch1/Vinted-Notifications.git
cd Vinted-Notifications

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Scarica il Chromium usato da Patchright (una sola volta)
patchright install chromium
# su un host Linux "nudo" aggiungi --with-deps

python vinted_notifications.py
```

L'applicazione crea da sola `./data`, il database e `./logs`, applica le
migrazioni e avvia tutti i processi. La Web UI è su <http://localhost:8000>.

Per fermarla: `Ctrl+C` (il programma chiude i processi figli e il browser).

---

## 4. Prima configurazione (Web UI)

Apri <http://localhost:8000> e usa la voce **Configuration** nel menu.

### 4.1 Impostazioni di sistema

| Campo | Cosa significa | Default |
|---|---|---|
| **Items Per Query** | quanti annunci leggere per ogni ricerca a ogni ciclo | `20` |
| **Query Refresh Delay** | secondi tra un ciclo di scraping e il successivo | `60` |
| **Web UI Port** | porta della Web UI (richiede riavvio) | `8000` |
| **Banwords** | parole che, se presenti nel titolo, scartano l'annuncio | vuoto |

Consigli pratici:

- con il browser attivo (default) ogni ricerca può richiedere il rendering di una
  pagina: tieni `Query Refresh Delay` **≥ 120 secondi**, soprattutto con molte
  query;
- la modifica del delay viene applicata automaticamente (il processo di scraping
  viene riavviato dal monitor);
- la modifica della **Web UI Port** richiede invece un riavvio dell'applicazione
  (in Docker cambia solo il mapping della porta host).

### 4.2 Telegram

1. Crea un bot con **@BotFather** e copia il **token**.
2. Recupera il tuo **chat ID** (ad esempio scrivendo a un bot che lo mostra, come
   `@userinfobot`, oppure leggendo `getUpdates` dell'API Telegram).
3. In **Configuration → Telegram Bot** inserisci token e chat ID.
4. Attiva **Auto Start** se vuoi che il bot parta insieme all'applicazione, poi
   premi **Save Configuration**.
5. Dalla dashboard (o dalla sezione di controllo) avvia il processo Telegram se
   non è già attivo.

Altre opzioni:

- **Send the item photo**: invia la foto dell'annuncio come immagine;
- **Add a "Buy" button**: aggiunge un pulsante di acquisto;
- **Photo Caption Template**: testo della didascalia (`{title}`, `{price}`,
  `{brand}`);
- **Quiet Hours**: intervallo in cui le notifiche vengono accodate invece di
  essere inviate, formato `23:00-07:00` (vuoto = disattivato).

Il bot risponde **solo alla chat configurata**: i comandi da altre chat vengono
ignorati.

### 4.3 RSS

In **Configuration → RSS Feed**:

- attiva **Auto Start**;
- imposta la **Port** (default `8080`) e il numero massimo di elementi;
- salva e avvia il processo RSS.

Il feed sarà disponibile su <http://localhost:8080> (o sulla porta scelta) e puoi
aggiungerlo a un lettore RSS. Cambiare la porta richiede il riavvio del processo
RSS (o dell'applicazione).

### 4.4 Sessione browser (Patchright)

La sezione **Browser Session** controlla come vengono ottenuti i cookie di Vinted:

| Opzione | Significato |
|---|---|
| **Use Patchright** | attiva il browser (consigliato, è ciò che fa funzionare lo scraping) |
| **Mode** | `essential` = browser solo per i cookie; `full` = chiamate API dentro il browser |
| **Session TTL** | durata della sessione in secondi prima del rinnovo (default `1800`) |
| **Channel** | vuoto = Chromium incluso; `chrome` = Chrome di sistema |
| **Profile Directory** | cartella del profilo browser (default `./data/browser_profile`) |
| **Headless** | browser senza finestra (default attivo) |
| **Disable the browser sandbox** | necessario nella maggior parte dei container Docker |

La pagina mostra anche **Status** e l'ultimo errore. Il pulsante **Refresh Browser
Session** forza il rinnovo della sessione (utile dopo errori 401/403/429).

Note:

- il browser viene avviato **solo dal processo di scraping**; gli altri processi
  riusano i cookie salvati nel database;
- la cartella del profilo e `HOME` devono essere scrivibili;
- `Headless = off` è più "stealth" ma richiede un server X11 (ad esempio
  `xvfb-run`);
- con `full` il carico è maggiore: alza il refresh delay.

### 4.5 Proxy (opzionale)

Sempre in **Configuration → Proxy Settings** puoi:

- incollare una lista di proxy separati da `;` (`http://ip:porta` o `ip:porta`);
- indicare un **URL** da cui scaricare la lista (un proxy per riga);
- attivare **Check Proxies** per verificare che i proxy funzionino prima dell'uso.

I proxy servono a ridurre blocchi e rate limit di Vinted.

### 4.6 Impostazioni avanzate

- **Notification Message Template**: formato del messaggio Telegram; variabili
  disponibili `{title}`, `{price}`, `{brand}`, `{image}`;
- **User Agents**: lista JSON di user agent per le richieste HTTP;
- **Default Headers**: header HTTP aggiuntivi in JSON.

---

## 5. Aggiungere e gestire le ricerche

Le ricerche ("query") si aggiungono da **Queries** nella Web UI oppure dal bot
Telegram. Il modo più semplice è partire da una ricerca fatta sul sito Vinted e
copiare l'URL completo, filtri inclusi.

Esempio:

```text
https://www.vinted.fr/catalog?search_text=nike%20shoes&price_to=50&currency=EUR&brand_id[]=53
```

Regole e comportamenti utili:

- **deve essere un URL Vinted completo**, non solo parole chiave;
- il progetto aggiunge automaticamente `order=newest_first` e rimuove parametri
  temporanei come `page`, `time`, `search_id`;
- gli URL di marca nella forma `/brand/id-nome` vengono convertiti in una ricerca
  `/catalog?brand_ids[]=...`;
- puoi assegnare un **nome** alla query per riconoscerla nella UI;
- ogni query può essere **eliminata** dalla Web UI, oppure messa in pausa o
  eliminata dai pulsanti inline di `/queries`;

### Dal bot Telegram

```text
/add_query https://www.vinted.it/catalog?search_text=switch&price_to=200
```

Con un nome (senza spazi, perché viene letto solo il primo argomento):

```text
/add_query switch-oled=https://www.vinted.it/catalog?search_text=switch%20oled
```

(Il formato `nome=URL` viene riconosciuto quando compare `=http`.)

Altri comandi:

| Comando | Effetto |
|---|---|
| `/queries` | elenca le query con pulsanti per pausa/eliminazione |
| `/remove_query 2` | elimina la query numero 2 |
| `/remove_query all` | elimina tutte le query |
| `/test URL` | esegue una ricerca una volta e mostra i risultati, senza salvare nulla |

### Dalla Web UI

- **Queries**: elenco, aggiunta, modifica del nome/URL, eliminazione;
- **Items**: annunci trovati, filtrabili per query;
- **Dashboard**: stato dei processi, statistiche e ultimi annunci;
- **Allowlist**: paesi ammessi;
- **Logs**: log dell'applicazione.

---

## 6. Ricevere le notifiche

### Telegram

Comandi principali (tutti verificati nel codice):

| Comando | Funzione |
|---|---|
| `/start`, `/help`, `/hello` | aiuto e verifica che il bot risponda |
| `/add_query`, `/remove_query`, `/queries` | gestione ricerche |
| `/pause`, `/resume` | sospende/riprende gli invii (gli annunci restano in coda) |
| `/quiet HH:MM-HH:MM`, `/quiet off` | fasce di silenzio |
| `/status` | stato sessione browser, notifiche in coda, ultimo annuncio |
| `/stats` | annunci trovati per query e per giorno |
| `/add_country XX`, `/remove_country XX`, `/clear_allowlist`, `/allowlist` | filtro paesi |
| `/banwords`, `/add_banword parola`, `/remove_banword parola` | parole escluse |
| `/test URL` | prova una ricerca senza salvare |
| `/refresh_session` | rinnova la sessione Vinted |

Le notifiche vengono messe in una **coda persistente** (`outbox`) e ritentate: un
riavvio dell'applicazione non fa perdere gli annunci già trovati. Con `/pause` o
durante le quiet hours la coda cresce e viene svuotata alla ripresa.

Dettagli utili:

- la coda viene svuotata **dal processo Telegram**: se Telegram non è configurato
  o non è in esecuzione, le notifiche restano in `outbox`; quando il bot riparte,
  quelle più vecchie di circa 24 ore vengono scartate come scadute;
- una notifica che continua a fallire viene ritentata fino a 5 volte, poi viene
  scartata;
- il RSS è indipendente da Telegram: riceve gli annunci anche se Telegram è spento.

### RSS

Dopo aver avviato il processo RSS, apri <http://localhost:8080> e aggiungi l'URL
al tuo lettore. Il feed contiene gli annunci più recenti (fino al massimo
configurato) e viene popolato mano a mano che lo scraper trova elementi nuovi.

---

## 7. Filtri: paesi e parole vietate

### Allowlist dei paesi

- i codici sono in formato **ISO 3166-1 alpha-2** (es. `IT`, `FR`, `DE`);
- con una allowlist non vuota vengono accettati solo i venditori dei paesi
  elencati (più `XX`, usato come "sconosciuto");
- se la allowlist è vuota, **tutti** i paesi sono ammessi;
- `/clear_allowlist` ripristina "tutti i paesi".

Limite importante: quando gli annunci vengono letti dal DOM (fallback senza API),
il paese del venditore non è esposto dalla pagina. In quel caso il filtro può
applicarsi solo ai venditori già presenti nella cache `users`; gli altri vengono
lasciati passare. Non è un bug, ma un limite della sorgente dati.

### Banwords

Le parole vietate vengono confrontate con il **titolo** dell'annuncio: se una è
presente, l'elemento viene scartato. Puoi gestirle dalla UI o con
`/banwords`, `/add_banword`, `/remove_banword`.

### Pausa globale e quiet hours

- `/pause` sospende gli invii; `/resume` li riattiva;
- `/quiet 23:00-07:00` accoda le notifiche nella fascia indicata; `/quiet off`
  disattiva la funzione. La stessa impostazione è in Configuration.

---

## 8. Dati, log e aggiornamenti

### File importanti

| Percorso | Contenuto |
|---|---|
| `./data/vinted_notifications.db` | database SQLite (query, annunci, impostazioni, coda) |
| `./data/browser_profile/` | profilo Chromium usato da Patchright |
| `./data/browser_state.json` | stato/cookie della sessione browser |
| `./logs/vinted.log` | log applicativi (rotazione a 10 MB, 5 file) |

In Docker questi percorsi vivono nei volumi `VN_data` e `VN_logs`.

### Backup

Ferma l'applicazione (o almeno evita scritture) e copia il file
`./data/vinted_notifications.db`. In Docker:

```bash
# trova il nome esatto del volume dati (es. vinted-notifications_vn_data)
docker volume ls
docker compose stop
docker run --rm \
  -v <nome_volume_dati>:/data \
  -v "$(pwd)":/backup \
  alpine cp /data/vinted_notifications.db /backup/
docker compose start
```

Fermare prima i container evita di copiare un database SQLite mentre è in
scrittura.

### Log

- dalla Web UI: sezione **Logs** (con ricerca e filtro);
- da file: `logs/vinted.log`;
- da Docker: `docker compose logs -f`.

### Aggiornamento

**Docker Compose:**

```bash
docker compose pull
docker compose up -d
```

**Docker run:** ferma e rimuovi il container, poi rilancialo con lo stesso comando
di §3.2 (i volumi conservano i dati).

**Installazione manuale:** fai un backup del database, sostituisci i file con
quelli della nuova release e riavvia. Le **migrazioni del database vengono
applicate automaticamente all'avvio**, in base al parametro `version`.

---

## 9. Sviluppo e test

Il progetto include test unitari che lavorano su database temporanei (non toccano
`./data`).

```bash
# test
python -m unittest discover -s tests -v

# lint (configurazione in ruff.toml)
ruff check .

# formattazione (la CI usa black)
black --check .
```

La CI (`.github/workflows`) esegue su Python 3.11 e 3.12: `ruff check .`,
`black --check .`, i test unitari e `pip-audit -r requirements.txt`.

`requirements.txt` contiene solo le dipendenze runtime; `ruff`, `black` e
`pip-audit` sono strumenti di sviluppo e vanno installati a parte (la CI li
installa con versioni fissate).

Struttura essenziale:

| File / cartella | Ruolo |
|---|---|
| `vinted_notifications.py` | entrypoint: bootstrap DB, migrazioni, processi |
| `core.py` | scraping, filtri, formattazione messaggi, coda |
| `db.py` | accesso SQLite, query, allowlist, outbox |
| `browser_session.py` | sessione Patchright e fallback DOM |
| `proxies.py` | gestione proxy |
| `telegram_bot_plugin/` | bot Telegram |
| `rss_feed_plugin/` | server RSS |
| `web_ui_plugin/` | Web UI Flask |
| `migrations/` | migrazioni SQL versionate |
| `tests/` | test unitari |

---

## 10. Problemi comuni

### Non arrivano notifiche

1. Controlla in **Dashboard** che i processi Telegram/RSS siano attivi.
2. Verifica token e chat ID; il bot ignora le chat diverse da quella configurata.
   Se Telegram non è attivo, la coda `outbox` non viene svuotata (usa RSS o
   avvia il bot).
3. Controlla di non aver usato `/pause`, di non essere in quiet hours e che le
   query non siano in pausa.
4. Verifica che le banwords o l'allowlist non stiano scartando gli annunci.
5. Guarda **Logs** e lo stato della sessione browser.

### Errori 401/403/429 da Vinted

- premi **Refresh Browser Session** (o usa `/refresh_session`);
- alza `Query Refresh Delay`;
- configura dei proxy;
- verifica lo stato del browser in Configuration.

### Il browser non parte

- assicurati che Chromium di Patchright sia installato
  (`patchright install chromium`, nel container c'è già);
- in Docker lascia attivo **Disable the browser sandbox**;
- controlla che `./data/browser_profile` e `HOME` siano scrivibili;
- se usi `Headless = off`, serve un display X11.

### La porta 8000 o 8080 è occupata

- con Compose: `VN_WEB_PORT=8010 VN_RSS_PORT=8081 docker compose up -d`;
- con Docker run: cambia il lato host del mapping (`-p 8010:8000`);
- con installazione manuale: cambia **Web UI Port** e riavvia.

### Il feed RSS è vuoto

- il processo RSS deve essere avviato dopo averlo abilitato;
- il feed riceve solo gli annunci trovati **dopo** l'avvio del processo;
- controlla la porta configurata e il massimo di elementi.

### Ho cambiato la configurazione ma non succede nulla

- `Query Refresh Delay` viene applicato automaticamente;
- `Web UI Port`, `RSS Port` e token Telegram richiedono il riavvio del processo
  o dell'applicazione;
- dopo la modifica dei proxy la cache viene azzerata al salvataggio.

---

## 11. Riferimenti

- Repository e release: <https://github.com/Fuyucch1/Vinted-Notifications>
- README ufficiale: `README.md`
- Licenza: GNU Affero General Public License (`LICENSE`)
