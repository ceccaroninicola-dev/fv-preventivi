#!/usr/bin/env python3
"""
Step 2 della pipeline preventivi FV: geocoding indirizzi -> lat/lng.

- Legge clienti_normalizzati.csv (output dello step 1)
- Geocodifica solo le righe con STATO != SCARTA
- Controlla location_type: ROOFTOP = buono, APPROXIMATE = scarta
- Cache su file: rilanciare NON ripaga le chiamate gia' fatte
- Scrive LAT / LNG / GEOCODE_TYPE e aggiorna FLAGS / STATO
- Solo libreria standard Python: nessun pip install necessario

USO:
    set GOOGLE_MAPS_KEY=AIza...        (Windows, una volta per sessione)
    python step2_geocoding.py

    python step2_geocoding.py --mock   (test a vuoto, senza chiave e senza chiamate)
"""
import os
import sys
import csv
import json
import time
import random
import urllib.parse
import urllib.request

# ---------- CONFIG (modificabile) ----------
INPUT_CSV  = "clienti_normalizzati.csv"
OUTPUT_CSV = "clienti_geocodificati.csv"
CACHE_FILE = "geocode_cache.json"
PAUSA_SEC  = 0.05            # pausa tra chiamate (Google regge molto, restiamo gentili)
TIMEOUT    = 15
MAX_RETRY  = 4

# location_type -> (stato_minimo, flag)
# ROOFTOP            = punto sull'edificio        -> ok
# RANGE_INTERPOLATED = interpolato sulla via       -> accettabile, da rivedere
# GEOMETRIC_CENTER   = centro via/poligono         -> rischioso
# APPROXIMATE        = centro paese                -> scarta (tetto sbagliato)
DECISIONE = {
    "ROOFTOP":            ("MANTIENI",    None),
    "RANGE_INTERPOLATED": ("DA_RIVEDERE", "GEO_INTERPOLATO"),
    "GEOMETRIC_CENTER":   ("DA_RIVEDERE", "GEO_CENTRO_VIA"),
    "APPROXIMATE":        ("SCARTA",      "GEO_APPROSSIMATO"),
}

MOCK = "--mock" in sys.argv


def carica_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def salva_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)


def geocode_reale(indirizzo, key):
    url = "https://maps.googleapis.com/maps/api/geocode/json?" + urllib.parse.urlencode(
        {"address": indirizzo, "key": key, "region": "it", "language": "it"}
    )
    for tentativo in range(MAX_RETRY):
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
                data = json.load(r)
        except Exception as e:
            wait = (2 ** tentativo) + random.random()
            print(f"   ! rete: {e} - ritento tra {wait:.1f}s")
            time.sleep(wait)
            continue

        status = data.get("status")
        if status == "OK":
            return data
        if status == "ZERO_RESULTS":
            return data
        if status == "OVER_QUERY_LIMIT":
            wait = (2 ** tentativo) + random.random()
            print(f"   ! limite quota - pausa {wait:.1f}s")
            time.sleep(wait)
            continue
        if status in ("REQUEST_DENIED", "INVALID_REQUEST"):
            # errore di configurazione: inutile insistere, fermiamo tutto
            raise SystemExit(
                f"\nERRORE Google: {status} - {data.get('error_message','')}\n"
                "Controlla: fatturazione attiva, Geocoding API abilitata, "
                "restrizioni della chiave."
            )
        time.sleep(1)
    return {"status": "RETRY_FALLITO", "results": []}


def geocode_mock(indirizzo):
    """Finto geocoder per test offline: coordinate plausibili area Rimini."""
    h = abs(hash(indirizzo))
    lat = 44.05 + (h % 1000) / 10000
    lng = 12.55 + (h // 1000 % 1000) / 10000
    tipo = ["ROOFTOP", "ROOFTOP", "ROOFTOP", "RANGE_INTERPOLATED", "APPROXIMATE"][h % 5]
    if "ZZZ" in indirizzo:
        return {"status": "ZERO_RESULTS", "results": []}
    return {"status": "OK", "results": [{
        "geometry": {"location": {"lat": lat, "lng": lng}, "location_type": tipo},
        "formatted_address": indirizzo,
    }]}


def estrai(data):
    """data Google -> (lat, lng, location_type, status, n_risultati)"""
    status = data.get("status")
    res = data.get("results", [])
    if status != "OK" or not res:
        return "", "", "", status, 0
    g = res[0]["geometry"]
    loc = g["location"]
    return loc["lat"], loc["lng"], g.get("location_type", ""), status, len(res)


def main():
    if not os.path.exists(INPUT_CSV):
        raise SystemExit(f"Non trovo {INPUT_CSV} nella cartella corrente.")

    key = os.environ.get("GOOGLE_MAPS_KEY")
    if not MOCK and not key:
        raise SystemExit(
            "Manca la chiave. Imposta GOOGLE_MAPS_KEY oppure lancia con --mock per il test a vuoto."
        )

    cache = carica_cache()
    with open(INPUT_CSV, encoding="utf-8") as f:
        righe = list(csv.DictReader(f))

    stat = {"da_cache": 0, "chiamate": 0, "rooftop": 0, "scartati": 0, "da_rivedere": 0, "zero": 0}

    for i, row in enumerate(righe, 1):
        if row.get("STATO") == "SCARTA":
            continue
        ind = row["INDIRIZZO_GEOCODING"]

        if ind in cache:
            data = cache[ind]
            stat["da_cache"] += 1
        else:
            data = geocode_mock(ind) if MOCK else geocode_reale(ind, key)
            cache[ind] = data
            stat["chiamate"] += 1
            if not MOCK:
                time.sleep(PAUSA_SEC)
            if stat["chiamate"] % 50 == 0:
                salva_cache(cache)
                print(f"   ...{i} righe, {stat['chiamate']} chiamate")

        lat, lng, tipo, status, n = estrai(data)
        row["LAT"], row["LNG"], row["GEOCODE_TYPE"] = lat, lng, tipo

        flags = [x for x in row.get("FLAGS", "").split("|") if x]
        if status == "ZERO_RESULTS" or not lat:
            flags.append("GEO_NESSUN_RISULTATO")
            row["STATO"] = "SCARTA"
            stat["zero"] += 1
        else:
            if n > 1:
                flags.append("GEO_MULTIPLO")
            stato_min, flag = DECISIONE.get(tipo, ("DA_RIVEDERE", "GEO_TIPO_SCONOSCIUTO"))
            if flag:
                flags.append(flag)
            if stato_min == "SCARTA":
                row["STATO"] = "SCARTA"; stat["scartati"] += 1
            elif stato_min == "DA_RIVEDERE" and row["STATO"] == "OK":
                row["STATO"] = "DA_RIVEDERE"; stat["da_rivedere"] += 1
            if tipo == "ROOFTOP":
                stat["rooftop"] += 1
        row["FLAGS"] = "|".join(flags)

    salva_cache(cache)
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=righe[0].keys())
        w.writeheader(); w.writerows(righe)

    print("\n=== GEOCODING COMPLETATO ===" + ("  [MOCK]" if MOCK else ""))
    print(f"Righe processate: {sum(1 for r in righe if r['STATO']!='SCARTA' or r['GEOCODE_TYPE'])}")
    print(f"Da cache: {stat['da_cache']}  |  chiamate API nuove: {stat['chiamate']}")
    print(f"ROOFTOP (ottimi): {stat['rooftop']}")
    print(f"Declassati a DA_RIVEDERE: {stat['da_rivedere']}")
    print(f"Scartati (approssimati/centro paese): {stat['scartati']}")
    print(f"Indirizzo non trovato: {stat['zero']}")
    print(f"\nOutput: {OUTPUT_CSV}  |  Cache: {CACHE_FILE}")


if __name__ == "__main__":
    main()
