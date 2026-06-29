#!/usr/bin/env python3
"""
Estrattore completo dati Solar: legge i JSON grezzi in solar_raw/ e produce
un estratto leggibile con TUTTI i campi utili per il preventivo.

- Input:  solar_raw/<ID>.json  (grezzi dello step 3) + clienti_solar.csv
          + clienti_geocodificati.csv (per propagare LAT/LNG, match per ID_CLIENTE)
- Output: clienti_solar_completo.csv  (un cliente per riga, campi espansi)
          + stampa a video un esempio dettagliato per i primi clienti
- Solo libreria standard.

USO:  py estrai_solar.py
"""
import os
import csv
import json
import glob

RAW_DIR = "solar_raw"
BASE_CSV = "clienti_solar.csv"
GEO_CSV = "clienti_geocodificati.csv"   # da qui vengono LAT/LNG (scritte dallo step 2)
OUT_CSV = "clienti_solar_completo.csv"


def orientamento(az):
    """azimut gradi (0=Nord, orario) -> punto cardinale + se e' buono per FV"""
    if az is None or az == "":
        return "", ""
    az = float(az) % 360
    dirs = ["Nord", "Nord-Est", "Est", "Sud-Est", "Sud", "Sud-Ovest", "Ovest", "Nord-Ovest"]
    card = dirs[round(az / 45) % 8]
    # 135-225 = quadrante sud = ottimo per fotovoltaico
    bonta = "OTTIMO" if 135 <= az <= 225 else ("BUONO" if (90 <= az < 135 or 225 < az <= 270) else "SCARSO")
    return card, bonta


def estrai_cliente(data):
    """JSON buildingInsights -> dict piatto con tutti i campi utili"""
    out = {}
    if data.get("error") in ("NOT_FOUND",) or "solarPotential" not in data:
        out["SOLAR_STATO"] = data.get("error", "NO_SOLAR_POTENTIAL")
        return out

    sp = data["solarPotential"]
    img = data.get("imageryDate", {})
    out.update({
        "SOLAR_STATO": "OK",
        "QUALITA_IMG": data.get("imageryQuality", ""),
        "DATA_IMG": f"{img.get('year','')}-{img.get('month','')}-{img.get('day','')}",
        "MAX_PANNELLI": sp.get("maxArrayPanelsCount", ""),
        "POT_PANNELLO_W": sp.get("panelCapacityWatts", ""),
        "PANNELLO_H_M": sp.get("panelHeightMeters", ""),
        "PANNELLO_W_M": sp.get("panelWidthMeters", ""),
        "VITA_PANNELLO_ANNI": sp.get("panelLifetimeYears", ""),
        "AREA_UTILE_M2": sp.get("maxArrayAreaMeters2", ""),
        "ORE_SOLE_ANNO_MAX": sp.get("maxSunshineHoursPerYear", ""),
        "CO2_KG_PER_MWH": sp.get("carbonOffsetFactorKgPerMwh", ""),
    })

    roof = sp.get("wholeRoofStats", {})
    out["AREA_TETTO_TOT_M2"] = roof.get("areaMeters2", "")
    out["AREA_TERRENO_M2"] = sp.get("buildingStats", {}).get("areaMeters2", "") or roof.get("groundAreaMeters2", "")

    # falde (roof segments)
    segs = sp.get("roofSegmentStats", [])
    out["N_FALDE"] = len(segs)
    if segs:
        # falda piu' grande e sua esposizione
        segs_sorted = sorted(segs, key=lambda s: s.get("stats", {}).get("areaMeters2", 0) or 0, reverse=True)
        big = segs_sorted[0]
        card, bonta = orientamento(big.get("azimuthDegrees"))
        out["FALDA_PRINCIPALE_ORIENT"] = card
        out["FALDA_PRINCIPALE_BONTA"] = bonta
        out["FALDA_PRINCIPALE_AZIMUT"] = round(big.get("azimuthDegrees", 0), 0) if big.get("azimuthDegrees") is not None else ""
        out["FALDA_PRINCIPALE_PENDENZA"] = round(big.get("pitchDegrees", 0), 0) if big.get("pitchDegrees") is not None else ""
        # quanta superficie e' esposta a sud (ottimo)
        area_sud = sum((s.get("stats", {}).get("areaMeters2", 0) or 0)
                       for s in segs if s.get("azimuthDegrees") is not None and 135 <= s["azimuthDegrees"] <= 225)
        area_tot = sum((s.get("stats", {}).get("areaMeters2", 0) or 0) for s in segs)
        out["PERC_TETTO_SUD"] = round(100 * area_sud / area_tot, 0) if area_tot else ""

    # configurazioni pannelli pre-calcolate da Google
    configs = sp.get("solarPanelConfigs", [])
    if configs:
        best = configs[-1]   # massimo numero di pannelli
        out["CONFIG_MAX_PANNELLI"] = best.get("panelsCount", "")
        out["CONFIG_MAX_KWH_DC_ANNO"] = best.get("yearlyEnergyDcKwh", "")
        out["N_CONFIG_DISPONIBILI"] = len(configs)
    return out


def main():
    files = glob.glob(os.path.join(RAW_DIR, "*.json"))
    if not files:
        raise SystemExit(f"Nessun JSON in {RAW_DIR}/. Lancia prima lo step 3.")

    base = {}
    if os.path.exists(BASE_CSV):
        for r in csv.DictReader(open(BASE_CSV, encoding="utf-8")):
            base[r["ID_CLIENTE"]] = r

    # LAT/LNG: dallo step 2 (clienti_geocodificati.csv), match per ID_CLIENTE
    geo = {}
    if os.path.exists(GEO_CSV):
        for r in csv.DictReader(open(GEO_CSV, encoding="utf-8")):
            geo[r["ID_CLIENTE"]] = r

    righe = []
    for fp in files:
        cid = os.path.splitext(os.path.basename(fp))[0]
        data = json.load(open(fp, encoding="utf-8"))
        ext = estrai_cliente(data)
        b = base.get(cid, {})
        g = geo.get(cid, {})
        riga = {
            "ID_CLIENTE": cid,
            "NOME": b.get("NOME", ""),
            "SEGMENTO": b.get("SEGMENTO", ""),
            "COMUNE": b.get("COMUNE", ""),
            "LAT": g.get("LAT", b.get("LAT", "")),
            "LNG": g.get("LNG", b.get("LNG", "")),
            "CONSUMO_KWH_ANNO": b.get("CONSUMO_KWH_ANNO", ""),
            "POTENZA_DISP_KW": b.get("POTENZA_DISP_KW", ""),
        }
        riga.update(ext)
        # rapporto copertura: quanto coprirebbe la produzione max vs consumo
        try:
            prod = float(ext.get("CONFIG_MAX_KWH_DC_ANNO") or 0)
            cons = float(b.get("CONSUMO_KWH_ANNO") or 0)
            riga["COPERTURA_PERC"] = round(100 * prod / cons, 0) if cons else ""
        except (ValueError, TypeError):
            riga["COPERTURA_PERC"] = ""
        righe.append(riga)

    cols = list(righe[0].keys())
    for r in righe:
        for k in r:
            if k not in cols:
                cols.append(k)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(righe)

    print(f"Estratto: {OUT_CSV}  ({len(righe)} clienti)\n")
    print("=" * 60)
    print("ESEMPIO DETTAGLIATO (primi 3 clienti con tetto valido):")
    print("=" * 60)
    mostrati = 0
    for r in righe:
        if r.get("SOLAR_STATO") != "OK":
            continue
        print(f"\n--- {r['NOME']} ({r['SEGMENTO']}, {r['COMUNE']}) ---")
        print(f"  Consumo annuo:        {r['CONSUMO_KWH_ANNO']} kWh")
        print(f"  Tetto totale:         {r.get('AREA_TETTO_TOT_M2','?')} m2  ({r.get('N_FALDE','?')} falde)")
        print(f"  Falda principale:     {r.get('FALDA_PRINCIPALE_ORIENT','?')} "
              f"(azimut {r.get('FALDA_PRINCIPALE_AZIMUT','?')}, pendenza {r.get('FALDA_PRINCIPALE_PENDENZA','?')}) "
              f"-> {r.get('FALDA_PRINCIPALE_BONTA','?')}")
        print(f"  % tetto esposto sud:  {r.get('PERC_TETTO_SUD','?')}%")
        print(f"  Pannelli max:         {r.get('MAX_PANNELLI','?')} da {r.get('POT_PANNELLO_W','?')}W")
        print(f"  Produzione max:       {r.get('CONFIG_MAX_KWH_DC_ANNO','?')} kWh/anno (DC)")
        print(f"  Copertura consumo:    {r.get('COPERTURA_PERC','?')}%")
        print(f"  Ore di sole/anno:     {r.get('ORE_SOLE_ANNO_MAX','?')}")
        print(f"  Qualita' dato Google: {r.get('QUALITA_IMG','?')} (foto {r.get('DATA_IMG','?')})")
        mostrati += 1
        if mostrati >= 3:
            break

    # statistiche aggregate
    validi = [r for r in righe if r.get("SOLAR_STATO") == "OK"]
    print("\n" + "=" * 60)
    print(f"AGGREGATO ({len(validi)} tetti validi):")
    cop = [float(r["COPERTURA_PERC"]) for r in validi if r.get("COPERTURA_PERC") not in ("", None)]
    if cop:
        sotto = sum(1 for c in cop if c < 80)
        print(f"  Copertura media consumo:  {round(sum(cop)/len(cop))}%")
        print(f"  Tetti che coprono <80%:   {sotto}  (candidati ad accumulo/altri prodotti)")
    sud = [r for r in validi if r.get("FALDA_PRINCIPALE_BONTA") == "OTTIMO"]
    print(f"  Tetti con falda OTTIMA (sud): {len(sud)} su {len(validi)}")


if __name__ == "__main__":
    main()
