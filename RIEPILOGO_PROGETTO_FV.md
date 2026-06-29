# PIPELINE PREVENTIVI FOTOVOLTAICI — STATO PROGETTO
**Ultimo aggiornamento:** 29 giugno 2026
**Scopo di questo file:** punto di ripartenza. I parametri commerciali sono ARRIVATI (vedi §7).
Prossimo passo: costruire lo STEP 4 (motore di calcolo + PDF) partendo dal segmento PRIVATI. Non serve rileggere la chat.

---

## 1. COS'È / CHI FA COSA
- **Lavoro che dà reddito** a Nicola: vendite efficienza energetica. PRIORITÀ ASSOLUTA tra i suoi progetti.
- **Cliente/agenzia:** SMART ENERGY SRL (fornitore luce/gas, area Rimini / Emilia-Romagna).
- **Divisione del lavoro:** Smart Energy gestisce consensi + invio (mailing list marketing già aggiornate
  e a norma — Nicola NON deve occuparsi di GDPR/invio, solo farsi dare conferma scritta che il DB è usabile).
  Nicola + Claude costruiscono SOLO il **motore** che dal database CRM genera il PDF di preventivo.
- **Architettura:** pipeline DETERMINISTICA, non "agent AI". I calcoli stanno nel codice (matematica riproducibile).
  L'LLM serve solo per: (a) testo narrativo personalizzato nel PDF, (b) futuro ragionamento caldaia/pompa di calore.
  → Mai far calcolare numeri di preventivo a un LLM.

## 2. DOVE GIRA / AMBIENTE
- **PC ufficio Windows**, cartella `C:\Users\Cecca\fv-preventivi`. NON sul VPS (la pipeline è un batch, non un servizio).
- **Python 3.14.6**, si lancia con il launcher `py` (l'alias `python` NON funziona su questo PC → usare sempre `py`).
- **Google Cloud:** progetto `fv-preventivi`, chiave "Maps Platform API Key".
  API abilitate: Geocoding API + Solar API. Fatturazione attiva. Free tier: 10k geocoding/mese gratis.
  Chiave passata via variabile d'ambiente `GOOGLE_MAPS_KEY` (set GOOGLE_MAPS_KEY=... per sessione terminale).
  Restrizioni: API (Geocoding+Solar). NIENTE restrizione IP (PC con IP dinamico). Quota giornaliera bassa consigliata.
- Gli script usano SOLO libreria standard tranne lo step 1 (pandas/openpyxl per leggere xlsx).

## 3. DATI CRM (SMART ENERGY)
- Due estratti campione reali ricevuti (43 privati + 43 aziende = 86 record). Schema colonne CSV/xlsx:
  CD_ACCOUNT_CRM_FORNITURA, TIPO_ACCOUNT, NOME_BENEFICIARIO, COMUNE_LEGALE_CONTRACT_FORNITURA, TIPO_CLIENTE,
  STATO_FORNITURA_CRM, COMUNE_POD_FORNITURA, POTENZA_DISPONIBILE_POD_CRM_FORNITURA,
  POTENZA_CONTRATTUALE_POD_CRM_FORNITURA, CONSUMO_ANNUO_PRESUNTO, AGENZIA_ATTIVAZIONE, AGENTE_ATTIVAZIONE,
  RAPPRESENTANTE_LEGALE, CODICE_ATECO, CATEGORIA_ATECO, DESCRIZIONE_CATEGORIA, INDIRIZZO_LEGALE.
- **Due segmenti = due prodotti diversi:**
  - PRIVATI: consumo 11–64k kWh/anno. Detrazione 50%.
  - AZIENDE: 81–369k kWh/anno, in gran parte HOTEL riviera adriatica → opportunità commerciale chiave
    (autoconsumo estivo alto = payback corto). Niente detrazione 50%: ammortamento, crediti d'imposta, IVA.
- **PROBLEMA CRITICO:** l'indirizzo nel DB è `INDIRIZZO_LEGALE`, NON quello del POD/tetto.
  ~7% mismatch comune (es. Hotel sede legale Milano, POD Rimini) → scartati per non fare il preventivo sul tetto sbagliato.
  TODO: chiedere a Smart Energy se nel CRM esiste un campo indirizzo-di-fornitura/POD; se sì usarlo al posto del legale.
- Encoding export sporco (mojibake tipo "AttivitÃ ") → gestito in lettura.

## 4. STEP COMPLETATI (codice già scritto, testato su dati reali)
Tutti gli script sono nella cartella di lavoro. Ordine di esecuzione:

- **step1_normalizza.py** — legge i due xlsx, normalizza indirizzi (abbreviazioni, frazioni→comune, encoding),
  separa via/civico/CAP/comune, assegna FLAGS e STATO (OK / DA_RIVEDERE / SCARTA).
  Logica scarto: MISMATCH_POD, SAN_MARINO (copertura Solar da verificare), CIVICO_ALTO, CONSUMO_ANOMALO.
  Output: `clienti_normalizzati.csv` (con colonne vuote già predisposte: LAT, LNG, GEOCODE_TYPE,
  ANNO_COSTRUZIONE, ETA_CALDAIA per il modulo termico futuro).
  Risultato campione: 86 → ~74 OK, 6 da rivedere, 6 scartati.

- **step2_geocoding.py** — stdlib-only. Geocodifica indirizzo→lat/lng via Google. Controlla location_type:
  ROOFTOP=ok, APPROXIMATE=scarta (centro paese), interpolato/centro-via=da rivedere.
  Cache su `geocode_cache.json` (rilanci NON ripagano). Output: `clienti_geocodificati.csv`.
  Run reale: 75/80 ROOFTOP al primo colpo (~87% geo-pronti). Costo zero (dentro free tier).

- **step3_solar.py** — stdlib-only. Per ogni tetto chiama Solar API buildingInsights.
  Salva JSON GREZZO completo in `solar_raw/<ID_CLIENTE>.json` (= cache: se il file esiste non richiama).
  Riepilogo in `clienti_solar.csv`. Run reale: 79/79 tetti OK, qualità HIGH/MEDIUM, 0 non trovati, 0 errori.
  → Copertura Solar API sulla riviera adriatica PERFETTA.

- **estrai_solar.py** — stdlib-only, NON chiama Google (legge solo i file locali). Apre i solar_raw/ ed estrae
  il dettaglio completo per cliente in `clienti_solar_completo.csv`: orientamento/esposizione falda principale,
  % tetto a sud, pannelli installabili, produzione stimata, e COPERTURA_PERC (produzione vs consumo cliente).
  Stampa esempio dettagliato + aggregato (copertura media, tetti che coprono <80% = bacino accumulo/PdC).

## 5. PUNTI APERTI / DA SISTEMARE NEL CODICE (noti, non ancora fatti)
- **TETTO PIANO non riconosciuto.** Solar API dà pitchDegrees~0 e azimuth nominale per i tetti piani
  (capannoni, hotel). estrai_solar.py li bolla come "SCARSO", ma è FUORVIANTE: su tetto piano i pannelli si
  montano su strutture inclinate verso sud a piacere. DA AGGIUNGERE: se pendenza ~0 → etichetta
  "PIANO (orientabile)" invece di valutare l'azimuth. Vale soprattutto per il segmento AZIENDE/hotel.
- **Pannello placeholder Google.** La Solar API usa un pannello di default (~400W, ~1,95 m²) e dà
  maxArrayPanelsCount / yearlyEnergyDcKwh su quella base. NON usare questi numeri nel preventivo.
  Lo step 4 deve usare SOLO la geometria fisica del tetto (mq utili, falde, esposizione, ore di sole) e
  ricalcolare con il pannello REALE dell'azienda.
- **Limite potenza contatore.** Su tetti grandi la produzione può superare di molto POTENZA_DISP_KW del POD
  (visto caso reale: azienda Savignano, tetto piano 1858 m², copertura 400%). Il motore deve segnalare quando
  l'impianto dimensionato eccede la potenza disponibile → serve adeguamento connessione (info commerciale).
- **Indirizzo legale vs POD** (vedi §3): verificare con Smart Energy.

## 6. STEP 4 — DA FARE (bloccato in attesa dei parametri commerciali)
Il motore di calcolo + generazione PDF. NON costruibile finché mancano i numeri sotto.
Quando arrivano → si parte da qui.
Componenti previsti:
  a) Dimensionamento: da mq tetto utile + pannello reale → quanti pannelli, kWp, produzione kWh/anno reale
     (correggendo DC→AC, perdite, esposizione). Riconoscere tetto piano.
  b) DUE motori finanziari distinti: PRIVATI (detrazione 50%) vs AZIENDE/HOTEL (ammortamento, crediti, IVA).
  c) Calcolo autoconsumo, risparmio in bolletta, payback, proiezione 10/20 anni.
  d) Rendering pannelli su immagine satellitare (Static Maps + geometria roofSegmentStats) — opzionale/dopo.
  e) DUE template PDF distinti (privati / aziende), brandizzati Smart Energy.
  f) Quality gate: stato per riga OK/DA_RIVEDERE/FALLITO, report finale.
  g) Predisposizione moduli futuri (pompe di calore, ibrido) — campi ANNO_COSTRUZIONE/ETA_CALDAIA già pronti.

## 7. PARAMETRI COMMERCIALI — RICEVUTI da Smart Energy il 29/06/2026

### FOTOVOLTAICO (dati completi per i PRIVATI)
- **Pannello CONFERMATO da datasheet:** Trina Solar Vertex S+ **TSM-460NEG9R.28**.
  460 Wp · dimensioni reali **1.762 × 1.134 × 30 mm (= 2,00 m²)** · efficienza **23,0%** · peso 21 kg ·
  144 celle N-type i-TOPCon, doppio vetro · degradazione 1% il 1° anno poi 0,4%/anno · garanzia prodotto
  25 anni / potenza 30 anni · coeff. temp. Pmax -0,29%/°C · NOCT 43°C.
  → Per il motore contano due numeri: **460 Wp** e **area 2,00 m²** (entrambi ora certi).
  NB: i valori sulla scheda compilata (1,75×1,13 m, 21%) erano approssimati/errati; valgono questi del datasheet.
- **Listino chiavi-in-mano, IVA esclusa:** 3 kWp 5.009€ · 4 kWp 5.982€ · 5 kWp 6.864€ · 6 kWp 7.664€.
- **Dimensionamento:** coprire il consumo, MAX 6 kWp sui privati.
- **Scarto tetto:** sotto 15 m² utili, oppure falda esposta solo a nord → non si propone.

### ACCUMULO / BATTERIA
- **Prezzi IVA esclusa:** 5 kWh 2.636€ · 10 kWh 4.409€ · 15 kWh 6.191€.
- **Autoconsumo:** 35% senza batteria, 70% con batteria.
- **Profondità scarica / garanzia:** 90% DoD, 10 anni o 6.000 cicli.

### NUMERI ECONOMICI (per tutto)
- Energia elettrica: **0,28 €/kWh**. Gas: **1,10 €/Smc**. Inflazione energetica: **+3%/anno**.
- PDF: logo aziendale + disclaimer "stima soggetta a sopralluogo".

### DETRAZIONI
- Privati: **50% in 10 anni**.
- Aziende/hotel: niente 50% → ammortamento, crediti d'imposta, IVA (motore finanziario distinto).

### POMPE DI CALORE (modulo futuro) — aria-acqua
- 8 kW 12.409,09€ SCOP 4,5 · 9 kW 13.181,82€ SCOP 4,82 · 10 kW 13.545,45€ SCOP 4,73.
- Criterio PdC vs caldaia: case dopo il 2005 o con pavimento radiante.

### CALDAIE IBRIDE (modulo futuro) — pompa + caldaia a condensazione
- 4 kW + caldaia 28 kW = 7.090,91€ · 6 kW + 24 kW = 9.181,82€ · 8 kW + 24 kW = 9.772,73€ ·
  8 kW + 28 kW = 9.954,55€ · 8 kW + 35 kW = 10.136,36€.
- Criterio ibrido vs PdC pura: case vecchie e poco isolate.
- Dati cliente per i moduli termici (anno casa, mq riscaldati, radiatori/pavimento, tipo+età caldaia):
  ANCORA DA INSERIRE nel CRM Smart Energy a monte.

### ⚠️ GAP NOTI DA CHIARIRE CON SMART ENERGY
1. **Listino FV solo fino a 6 kWp.** Perfetto per i privati. Ma il segmento AZIENDE/HOTEL (consumi
   80–370k kWh) richiede impianti molto più grandi: manca un **prezzo €/kWp per impianti commerciali >6 kWp**.
   → Per ora: motore PRIVATI completo; per le aziende calcoliamo il dimensionamento tecnico ma il prezzo
   va estrapolato o lasciato "su richiesta". CHIEDERE listino commerciale.
   (Il dubbio sul pannello è RISOLTO: datasheet Trina ricevuto, vedi sopra.)

## 8. NUMERI/FATTI UTILI
- Costo Solar API: 0–150 USD per 20.000 preventivi (geocoding+buildingInsights). $0 se spalmato ≤10k/mese.
  Da EVITARE: dataLayers per ogni casa (~$1.425). Per il rendering usare Static Maps (~$20/20k).
- Area SEE (Italia): Solar API non restituisce i campi indirizzo — irrilevante, l'indirizzo ce l'abbiamo dal DB.
- Calibrazione qualità DB reale: ~87% geo-pronti, copertura Solar ~100% sul campione riviera.
  Su 20.000 le percentuali caleranno (più frazioni, periferie): normale, lo script flagga invece di forzare.
