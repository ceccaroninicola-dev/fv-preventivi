#!/usr/bin/env python3
"""
Step 3 della pipeline preventivi FV: scan dei tetti con Google Solar API.

- Legge clienti_geocodificati.csv (output dello step 2)
- Per ogni riga con LAT/LNG valide e STATO != SCARTA chiama buildingInsights
- Salva il JSON GREZZO completo di ogni tetto in solar_raw/<ID_CLIENTE>.json
- Estrae un riepilogo leggibile in clienti_solar.csv
- Cache implicita: se il file solar_raw/<id>.json esiste, NON richiama l'API
- Solo libreria standard: nessun pip install

NB: in area SEE (Italia) la Solar API NON restituisce i campi indirizzo:
    a noi non serve, usiamo le coordinate dello step 2.

USO:
    set GOOGLE_MAPS_KEY=AIza...
    py step3_solar.py
    py step3_solar.py --mock     (test a vuoto, dati finti, nessuna chiamata)
"""
import os
import sys
import csv
import json
import time
import random
import urllib.parse
import urllib.request

INPUT_CSV  = "clienti_geocodificati.csv"
OUTPUT_CSV = "clienti_solar.csv"
RAW_DIR    = "solar_raw"
PAUSA_SEC  = 0.05
TIMEOUT    = 20
MAX_RETRY  = 4

# qualita' minima dei dati Google: HIGH = ottima, MEDIUM = ok, LOW = imprecisa
QUALITA_ACCETTABILE = {"HIGH", "MEDIUM"}

MOCK = "--mock" in sys.argv


def solar_reale(lat, lng, key):
    url = "https://solar.googleapis.com/v1/buildingInsights:findClosest?" + urllib.parse.urlencode({
        "location.latitude": lat,
        "location.longitude": lng,
        "requiredQuality": "LOW",   # accetta anche LOW, poi filtriamo noi
        "key": key,
    })
    for tentativo in range(MAX_RETRY):
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
                return json.load(r), 200
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")
            if e.code == 404:
                # nessun edificio trovato a quelle coordinate: caso normale
                return {"error": "NOT_FOUND", "raw": body}, 404
            if e.code == 429:
                wait = (2 ** tentativo) + random.random()
                print(f"   ! limite quota - pausa {wait:.1f}s")
                time.sleep(wait)
                continue
            if e.code in (400, 403):
                raise SystemExit(
                    f"\nERRORE Solar API (HTTP {e.code}): {body[:300]}\n"
                    "Controlla: Solar API abilitata, fatturazione attiva, restrizioni chiave."
                )
            wait = (2 ** tentativo) + random.random()
            time.sleep(wait)
        except Exception as e:
            wait = (2 ** tentativo) + random.random()
            print(f"   ! rete: {e} - ritento tra {wait:.1f}s")
            time.sleep(wait)
    return {"error": "RETRY_FALLITO"}, 0


def solar_mock(lat, lng):
    h = abs(hash((lat, lng)))
    if h % 11 == 0:
        return {"error": "NOT_FOUND"}, 404
    n_pannelli = 8 + h % 40
    kwh = round(n_pannelli * 420 * (0.95 + (h % 10) / 100), 1)
    return {
        "name": "buildings/MOCK",
        "imageryDate": {"year": 2023, "month": 5, "day": 1},
        "imageryQuality": ["HIGH", "MEDIUM", "LOW"][h % 3],
        "solarPotential": {
            "maxArrayPanelsCount": n_pannelli,
            "maxArrayAreaMeters2": round(n_pannelli * 1.9, 1),
            "maxSunshineHoursPerYear": 1500 + h % 400,
            "panelCapacityWatts": 400,
            "wholeRoofStats": {"areaMeters2": round(n_pannelli * 3.0, 1)},
            "roofSegmentStats": [{"pitchDegrees": 18 + h % 15, "azimuthDegrees": h % 360}] * (1 + h % 3),
            "solarPanelConfigs": [{
                "panelsCount": n_pannelli,
                "yearlyEnergyDcKwh": kwh,
            }],
        },
    }, 200


def estrai_riepilogo(data, http):
    """data Solar -> dict di campi chiave per il CSV"""
    if http == 404 or data.get("error") == "NOT_FOUND":
        return {"SOLAR_STATO": "NESSUN_EDIFICIO"}
    if "error" in data:
        return {"SOLAR_STATO": "ERRORE:" + str(data.get("error"))}

    sp = data.get("solarPotential", {})
    configs = sp.get("solarPanelConfigs", [])
    best = configs[-1] if configs else {}     # ultima = piu' pannelli
    qual = data.get("imageryQuality", "")
    img = data.get("imageryDate", {})
    return {
        "SOLAR_STATO": "OK" if qual in QUALITA_ACCETTABILE else "QUALITA_BASSA",
        "QUALITA_IMG": qual,
        "DATA_IMG": f"{img.get('year','')}-{img.get('month','')}",
        "MAX_PANNELLI": sp.get("maxArrayPanelsCount", ""),
        "AREA_TETTO_M2": sp.get("wholeRoofStats", {}).get("areaMeters2", ""),
        "AREA_UTILE_M2": sp.get("maxArrayAreaMeters2", ""),
        "ORE_SOLE_ANNO": sp.get("maxSunshineHoursPerYear", ""),
        "POT_PANNELLO_W": sp.get("panelCapacityWatts", ""),
        "N_FALDE": len(sp.get("roofSegmentStats", [])),
        "KWH_ANNO_MAX_DC": best.get("yearlyEnergyDcKwh", ""),
        "PANNELLI_CONFIG_MAX": best.get("panelsCount", ""),
    }


def main():
    if not os.path.exists(INPUT_CSV):
        raise SystemExit(f"Non trovo {INPUT_CSV}. Lancia prima lo step 2.")
    key = os.environ.get("GOOGLE_MAPS_KEY")
    if not MOCK and not key:
        raise SystemExit("Manca GOOGLE_MAPS_KEY. Impostala o lancia con --mock.")
    os.makedirs(RAW_DIR, exist_ok=True)

    with open(INPUT_CSV, encoding="utf-8") as f:
        righe = list(csv.DictReader(f))

    stat = {"ok": 0, "no_edificio": 0, "qual_bassa": 0, "errore": 0, "saltati": 0, "da_cache": 0, "chiamate": 0}
    extra_cols = ["SOLAR_STATO", "QUALITA_IMG", "DATA_IMG", "MAX_PANNELLI", "AREA_TETTO_M2",
                  "AREA_UTILE_M2", "ORE_SOLE_ANNO", "POT_PANNELLO_W", "N_FALDE",
                  "KWH_ANNO_MAX_DC", "PANNELLI_CONFIG_MAX"]

    for i, row in enumerate(righe, 1):
        for c in extra_cols:
            row.setdefault(c, "")
        if row.get("STATO") == "SCARTA" or not row.get("LAT"):
            stat["saltati"] += 1
            row["SOLAR_STATO"] = "SALTATO"
            continue

        raw_path = os.path.join(RAW_DIR, f"{row['ID_CLIENTE']}.json")
        if os.path.exists(raw_path):
            with open(raw_path, encoding="utf-8") as f:
                data = json.load(f)
            http = 404 if data.get("error") == "NOT_FOUND" else 200
            stat["da_cache"] += 1
        else:
            if MOCK:
                data, http = solar_mock(float(row["LAT"]), float(row["LNG"]))
            else:
                data, http = solar_reale(row["LAT"], row["LNG"], key)
                time.sleep(PAUSA_SEC)
            with open(raw_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            stat["chiamate"] += 1
            if stat["chiamate"] % 25 == 0:
                print(f"   ...{i} righe, {stat['chiamate']} chiamate Solar")

        ries = estrai_riepilogo(data, http)
        row.update(ries)
        s = ries["SOLAR_STATO"]
        if s == "OK": stat["ok"] += 1
        elif s == "NESSUN_EDIFICIO": stat["no_edificio"] += 1
        elif s == "QUALITA_BASSA": stat["qual_bassa"] += 1
        else: stat["errore"] += 1

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        cols = list(righe[0].keys())
        for c in extra_cols:
            if c not in cols: cols.append(c)
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader(); w.writerows(righe)

    print("\n=== SCAN SOLAR COMPLETATO ===" + ("  [MOCK]" if MOCK else ""))
    print(f"Da cache (gia' scansiti): {stat['da_cache']}  |  chiamate API nuove: {stat['chiamate']}")
    print(f"Tetti OK (qualita' buona):   {stat['ok']}")
    print(f"Qualita' immagine bassa:     {stat['qual_bassa']}")
    print(f"Nessun edificio trovato:     {stat['no_edificio']}")
    print(f"Errori:                      {stat['errore']}")
    print(f"Saltati (scartati/no coord): {stat['saltati']}")
    print(f"\nRiepilogo: {OUTPUT_CSV}  |  JSON grezzi in: {RAW_DIR}/")


if __name__ == "__main__":
    main()
