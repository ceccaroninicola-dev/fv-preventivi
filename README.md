# fv-preventivi

Pipeline **deterministica** per generare preventivi fotovoltaici da un database clienti.
I calcoli stanno nel codice (mai delegati a un LLM); tutti i parametri commerciali sono
in `parametri.json` e vengono letti dal motore tramite `config.py`.

> **GDPR** — Il repository contiene SOLO codice e `parametri.json`. I dati cliente
> (CSV `clienti_*.csv`, `solar_raw/`, export `*.xlsx`, PDF in `preventivi_pdf/`) sono
> esclusi dal `.gitignore` e non vanno mai committati.

## Requisiti

- Python 3 (su Windows si lancia con il launcher `py`).
- Gli step da 1 a 5 usano **solo la libreria standard**: nessun `pip install`.
- Lo `step6_pdf.py` (generazione PDF) richiede `reportlab` e `svglib`:

  ```
  py -m pip install -r requirements.txt
  # oppure, se l'ambiente e' "gestito":
  py -m pip install -r requirements.txt --break-system-packages
  ```

## Pipeline

| Step | File | Output |
|------|------|--------|
| 1 | `step1_normalizza.py` | `clienti_normalizzati.csv` |
| 2 | `step2_geocoding.py` | indirizzi geocodificati |
| 3 | `step3_solar.py` | `solar_raw/<ID>.json` |
| - | `estrai_solar.py` | `clienti_solar_completo.csv` |
| 4 | `step4_dimensionamento.py` | `clienti_dimensionati.csv` |
| 5 | `step5_finanziario.py` | `clienti_finanziario.csv` |
| 6 | `step6_pdf.py` | `preventivi_pdf/<ID_CLIENTE>.pdf` |

I parametri commerciali si modificano comodamente dal pannello web locale:

```
py pannello.py        # http://127.0.0.1:8000 (solo locale)
```

## step6_pdf.py — PDF di preventivo (segmento PRIVATI)

Genera il PDF di **un cliente alla volta**, leggendo i valori gia' calcolati da
`clienti_finanziario.csv` (non ricalcola nulla).

```
py step6_pdf.py <ID_CLIENTE>     # PDF di quel cliente
py step6_pdf.py                  # PDF del primo privato valido (prova rapida)
```

Output in `preventivi_pdf/<ID_CLIENTE>.pdf`. Le **aziende** non sono gestite (PDF solo
privati): vengono rifiutate con un messaggio.

### Logo SVG e branding

- Colore brand SGR: **`#3BA9DD`** (testata e accenti).
- Il logo `logo.f586e6.svg` e' **bianco**, pensato per stare sulla fascia azzurra.
- `reportlab` **non legge gli SVG**. La conversione e' risolta con **`svglib`**
  (`svg2rlg`), che trasforma l'SVG in un disegno **vettoriale** disegnato direttamente
  sul canvas: nessuna conversione in PNG e, soprattutto, **nessuna dipendenza da Cairo**
  (`cairosvg` richiederebbe DLL native difficili da installare su Windows).
- Se `svglib` non fosse disponibile o l'SVG non fosse leggibile, lo step **non va in
  crash**: ripiega su una scritta bianca "SGR" nella testata e segnala l'avviso su
  `stderr`. In quel caso basta installare `svglib` (`py -m pip install svglib`).

Il PDF (una pagina) e' pensato per **vendere**, con gerarchia visiva forte:

- testata azzurra con logo SGR bianco + titolo;
- **numero eroe** in grande: tempo di rientro e guadagno a 20 anni;
- **confronto spesa** energia oggi vs con il fotovoltaico (vedi nota sotto) + badge di
  risparmio percentuale;
- **immagine satellitare del tetto** (vedi sotto);
- riga compatta dell'impianto proposto (kWp, n. pannelli + modello, produzione);
- **grafico** del guadagno cumulato a 20 anni: parte negativo (area rossa), attraversa
  lo zero al **punto di pareggio** (evidenziato) e sale (area verde) fino al guadagno
  finale;
- fascia fiducia (garanzia di potenza, modello, detrazione) e disclaimer obbligatorio
  "Stima indicativa soggetta a sopralluogo tecnico."

### Spesa "oggi"

Il CSV non contiene una colonna di spesa annua: la spesa attuale e' quindi **stimata**
come `CONSUMO_KWH_ANNO * prezzo_elettricita` (da `config.py`) e marcata "(stima)" sul
PDF. La spesa "dopo" e' `spesa attuale - RISPARMIO_ANNUO_ANNO1`. Se il consumo manca, il
blocco mostra "n/d" senza errori.

### Immagine aerea del tetto (Google Solar API)

In area europea (SEE) Google ha disabilitato `Static Maps maptype=satellite`
(HTTP 403). L'immagine del tetto viene quindi presa dalla **Google Solar API**, che in
Europa risponde. Flusso (in `step6_pdf.py`):

1. `GET https://solar.googleapis.com/v1/dataLayers:get` con `location.latitude`/
   `location.longitude` (dalle colonne `LAT`/`LNG` del CSV), `radiusMeters=35`,
   `view=IMAGERY_LAYERS` e la chiave `os.environ["GOOGLE_MAPS_KEY"]`.
2. Dalla risposta si legge il campo **`rgbUrl`** e si scarica quel layer (un **GeoTIFF**)
   appendendo `?key=...` all'URL.
2b. Dalla stessa risposta `dataLayers:get` si legge il **`boundingBox` dell'immagine**
   (estensione geografica del GeoTIFF), che serve per inquadrare l'edificio (punto 3b).
3. Il GeoTIFF viene convertito in **PNG** con **Pillow**.
   *Niente pannelli disegnati sopra: solo l'immagine.*
   - Se Pillow non legge quel GeoTIFF su Windows, lo step usa come **fallback**
     `tifffile` + `numpy` (per i TIFF compressi serve anche `imagecodecs`):
     `py -m pip install tifffile numpy imagecodecs`.
3b. **Inquadratura sull'edificio**: su edifici in centro storico o vicini tra loro, il
   raggio fisso rischia di centrare il punto sbagliato (es. un incrocio). Per evitarlo si
   **ritaglia e centra** l'immagine sul **`center` dell'edificio** (da
   `buildingInsights:findClosest`, gia' in `solar_raw/<ID>.json`), con dimensione data dal
   suo `boundingBox` + un margine di contorno (`MARGINE_EDIFICIO`, ~35%) e una finestra
   minima. La mappatura geo->pixel usa, in ordine:
   1. la **georeferenziazione embedded nel GeoTIFF** (tag `ModelPixelScale` +
      `ModelTiepoint`), che e' la fonte piu' affidabile (estensione reale del file);
   2. in fallback, il **`boundingBox` dell'immagine** restituito da `dataLayers:get`.
   Se la geometria dell'edificio non e' disponibile, si usa l'immagine intera (fallback,
   senza crash). Per diagnosticare l'inquadratura su un caso reale, esegui con la variabile
   d'ambiente **`STEP6_DEBUG_TETTO=1`**: viene stampato su stderr `img=WxH`,
   `center_geo`, `center_px` e il `crop` calcolato (il centro edificio deve cadere
   ~al centro dell'immagine). Nota: la cache va invalidata (cancella `tetti_cache/<ID>.png`)
   perche' un'immagine gia' in cache **non** viene rigenerata.
4. Nel PDF l'immagine viene resa in modalita' **"cover"**: scalata col fattore
   `max(w/iw, h/ih)` per **riempire sempre e completamente** il riquadro (la Solar API
   restituisce immagini quadrate, il box e' panoramico), con l'eccedenza ritagliata dal
   clip arrotondato. Sotto c'e' comunque uno sfondo neutro, cosi' che in nessun caso
   (immagine assente o non disegnabile) resti visibile lo sfondo scuro della pagina.

   Parametri di taratura in cima a `step6_pdf.py`: `RADIUS_METERS`, `MARGINE_EDIFICIO`.

Sotto l'immagine il PDF stampa la **riga indirizzo** (`VIA_CIVICO, CAP COMUNE`; fallback a
`INDIRIZZO_GEOCODING`). Se il comune della fornitura (`COMUNE_POD`) differisce dall'indirizzo
legale (`COMUNE`), viene segnalato in rosso ("comune fornitura diverso: ..."): utile per
accorgersi dei casi in cui il tetto inquadrato potrebbe non corrispondere all'indirizzo legale.

- **Cache obbligatoria** in `tetti_cache/<ID>.png`: se il PNG esiste gia', l'API **non**
  viene richiamata (ogni `dataLayers` costa ~0,075 €, da pagare una sola volta per cliente).
- Se la chiave o le coordinate mancano, la chiamata fallisce, oppure per quel punto **non
  c'e' copertura immagine** (nessun `rgbUrl`), il PDF si genera comunque con un
  **placeholder grigio** (nessun crash).

> I PDF (`preventivi_pdf/`) e le immagini dei tetti (`tetti_cache/`) contengono dati
> cliente: sono esclusi dal `.gitignore` e non vanno mai committati.
