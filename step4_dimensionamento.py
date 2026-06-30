# -*- coding: utf-8 -*-
"""
step4_dimensionamento.py — Dimensiona l'impianto FV e ne ricava il prezzo.

Input:  clienti_solar_completo.csv  (output di estrai_solar.py)
Output: clienti_dimensionati.csv
Usa:    config.py  (tutti i parametri commerciali)

Logica per cliente:
  1. Producibilita' del SUO tetto: dai kWh stimati da Google, normalizzati per kWp
     (incorpora irraggiamento/ombra reali). Fallback al valore medio di config.
  2. kWp necessari = consumo annuo / producibilita' AC.
  3. kWp che il tetto regge = area utile / area pannello Trina (2,00 m²) -> n pannelli -> kWp.
  4. kWp consigliato = min(necessari, tetto), con cap 6 kWp per i privati.
  5. Taglia di listino / fascia aziende -> prezzo (via config).
  6. Produzione, copertura del consumo, e flag (limiti, tetto piano, potenza contatore).

Solo libreria standard. py step4_dimensionamento.py
"""
import csv
import math
import config as C

INPUT = "clienti_solar_completo.csv"
OUTPUT = "clienti_dimensionati.csv"

DERATE_AC = C.DERATE_AC   # perdite sistema + conversione DC->AC (da config.py)
KWP_PER_PANNELLO = C.PANNELLO["potenza_wp"] / 1000.0   # 0.460
AREA_PANNELLO = C.PANNELLO["area_m2"]                  # 2.00


def num(x, default=0.0):
    try:
        return float(x)
    except (ValueError, TypeError):
        return default


def producibilita_tetto(row):
    """kWh/anno per kWp AC stimati per QUESTO tetto. (None -> uso media config)"""
    kwh_dc = num(row.get("CONFIG_MAX_KWH_DC_ANNO"))
    n_pan = num(row.get("CONFIG_MAX_PANNELLI"))
    pot_pan = num(row.get("POT_PANNELLO_W"))   # pannello placeholder di Google (~400W)
    if kwh_dc > 0 and n_pan > 0 and pot_pan > 0:
        kwp_google = n_pan * pot_pan / 1000.0
        prod_dc_per_kwp = kwh_dc / kwp_google
        return prod_dc_per_kwp * DERATE_AC, "tetto"
    return C.PRODUCIBILITA_KWH_PER_KWP * DERATE_AC, "media"


def dimensiona(row):
    flags = []
    seg = (row.get("SEGMENTO") or "").upper()
    consumo = num(row.get("CONSUMO_KWH_ANNO"))
    area_utile = num(row.get("AREA_UTILE_M2"))
    pot_disp = num(row.get("POTENZA_DISP_KW"))
    pendenza = row.get("FALDA_PRINCIPALE_PENDENZA")

    prod_per_kwp, fonte = producibilita_tetto(row)

    # tetto piano: pendenza ~0 -> orientabile, l'esposizione non penalizza
    try:
        if pendenza != "" and float(pendenza) <= 5:
            flags.append("TETTO_PIANO")
    except (ValueError, TypeError):
        pass

    e_priv = seg.startswith("PRIV")

    # 1) CAP DA CONTATORE (solo domestici), con limite invalicabile max_kwp_domestico.
    #    monofase (contatore <= 6 kW)  -> cap 6 kWp
    #    trifase  (contatore  > 6 kW)  -> cap = potenza contatore, comunque <= max_kwp_domestico
    trifase = False
    if e_priv:
        if pot_disp and pot_disp > 6:
            cap_contatore = min(pot_disp, C.MAX_KWP_DOMESTICO)
            trifase = True
        else:
            cap_contatore = min(C.MAX_KWP_PRIVATI, C.MAX_KWP_DOMESTICO)   # 6
    else:
        cap_contatore = None   # aziende: nessun cap domestico

    # 2) fabbisogno teorico
    kwp_necessario = consumo / prod_per_kwp if prod_per_kwp else 0

    # 3) quanto regge il tetto con i NOSTRI pannelli
    n_pannelli_tetto = math.floor(area_utile / AREA_PANNELLO) if area_utile else 0
    kwp_tetto = n_pannelli_tetto * KWP_PER_PANNELLO

    # 4) kWp consigliato = minimo tra necessario, tetto e cap contatore
    kwp_pre = min(kwp_necessario, kwp_tetto) if kwp_tetto else kwp_necessario
    kwp = kwp_pre if cap_contatore is None else min(kwp_pre, cap_contatore)

    if kwp_tetto and kwp_tetto < kwp_necessario:
        flags.append("LIMITATO_DA_TETTO")

    # flag sul cap contatore/domestico (solo se il cap e' davvero il vincolo che riduce)
    if cap_contatore is not None and cap_contatore < kwp_pre - 1e-9:
        if not trifase:
            flags.append("LIMITATO_CAP_6KWP")                 # monofase: cap 6 kWp
        elif cap_contatore >= C.MAX_KWP_DOMESTICO - 1e-9:
            flags.append("LIMITATO_CAP_DOMESTICO")            # tetto al limite invalicabile (20)

    if kwp <= 0:
        return {"KWP_CONSIGLIATO": 0, "FLAG_DIMENS": "NON_DIMENSIONABILE"}

    # n pannelli effettivi e potenza reale installata (senza superare il cap in potenza)
    n_pannelli = max(1, round(kwp / KWP_PER_PANNELLO))
    if cap_contatore is not None:
        n_max = int(cap_contatore / KWP_PER_PANNELLO)         # floor: resta <= cap
        if n_max >= 1 and n_pannelli > n_max:
            n_pannelli = n_max
    kwp_reale = round(n_pannelli * KWP_PER_PANNELLO, 2)

    # impianto domestico oltre 6 kWp grazie al contatore trifase
    if e_priv and trifase and kwp_reale > C.MAX_KWP_PRIVATI:
        flags.append("TRIFASE_OVER6")

    # 5) prezzo: domestici -> listino fino a 6 kWp, fasce aziende oltre; aziende -> fasce
    if e_priv:
        prezzo, taglia, nota = C.prezzo_domestico(kwp_reale)
    else:
        prezzo, taglia, nota = C.prezzo_impianto(seg, kwp_reale)
    if prezzo is None:
        flags.append("PREZZO_DA_DEFINIRE")

    # 6) produzione e copertura
    produzione = round(kwp_reale * prod_per_kwp)
    copertura = round(100 * produzione / consumo) if consumo else ""

    # 7) limite potenza contatore (es. monofase con contatore < 6 kW)
    if pot_disp and kwp_reale > pot_disp:
        flags.append(f"SUPERA_CONTATORE({pot_disp}kW)")

    return {
        "PROD_KWH_PER_KWP": round(prod_per_kwp),
        "PROD_FONTE": fonte,
        "KWP_NECESSARIO": round(kwp_necessario, 1),
        "KWP_TETTO_MAX": round(kwp_tetto, 1),
        "KWP_CONSIGLIATO": kwp_reale,
        "N_PANNELLI": n_pannelli,
        "TAGLIA_FASCIA": taglia,
        "PREZZO_EUR": prezzo if prezzo is not None else "",
        "PRODUZIONE_KWH_ANNO": produzione,
        "COPERTURA_PERC": copertura,
        "FLAG_DIMENS": "|".join(flags),
    }


def main():
    righe = list(csv.DictReader(open(INPUT, encoding="utf-8")))
    extra = ["PROD_KWH_PER_KWP", "PROD_FONTE", "KWP_NECESSARIO", "KWP_TETTO_MAX",
             "KWP_CONSIGLIATO", "N_PANNELLI", "TAGLIA_FASCIA", "PREZZO_EUR",
             "PRODUZIONE_KWH_ANNO", "COPERTURA_PERC", "FLAG_DIMENS"]
    stat = {"dimensionati": 0, "privati": 0, "aziende": 0, "no_prezzo": 0,
            "limitati_tetto": 0, "saltati": 0}

    for row in righe:
        for c in extra:
            row.setdefault(c, "")
        if row.get("SOLAR_STATO") != "OK":
            row["FLAG_DIMENS"] = "NO_SOLAR"
            stat["saltati"] += 1
            continue
        res = dimensiona(row)
        row.update(res)
        if res.get("KWP_CONSIGLIATO"):
            stat["dimensionati"] += 1
            if (row.get("SEGMENTO") or "").upper().startswith("PRIV"):
                stat["privati"] += 1
            else:
                stat["aziende"] += 1
            if "PREZZO_DA_DEFINIRE" in res.get("FLAG_DIMENS", ""):
                stat["no_prezzo"] += 1
            if "LIMITATO_DA_TETTO" in res.get("FLAG_DIMENS", ""):
                stat["limitati_tetto"] += 1

    cols = list(righe[0].keys())
    for c in extra:
        if c not in cols:
            cols.append(c)
    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(righe)

    print("=== DIMENSIONAMENTO COMPLETATO ===")
    print(f"Impianti dimensionati: {stat['dimensionati']}  "
          f"(privati {stat['privati']}, aziende {stat['aziende']})")
    print(f"Aziende senza prezzo (fasce da riempire): {stat['no_prezzo']}")
    print(f"Limitati dalla superficie del tetto: {stat['limitati_tetto']}")
    print(f"Saltati (tetto non valido): {stat['saltati']}")
    print(f"\nOutput: {OUTPUT}\n")

    print("Esempio (primi 4 privati con prezzo):")
    n = 0
    for r in righe:
        if (r.get("SEGMENTO") or "").upper().startswith("PRIV") and r.get("PREZZO_EUR") not in ("", None):
            print(f"  {r['NOME'][:22]:22} cons {r['CONSUMO_KWH_ANNO']:>7} kWh -> "
                  f"{r['KWP_CONSIGLIATO']} kWp ({r['N_PANNELLI']} pann., {r['TAGLIA_FASCIA']}), "
                  f"{r['PREZZO_EUR']}€, copre {r['COPERTURA_PERC']}%  {r['FLAG_DIMENS']}")
            n += 1
            if n >= 4:
                break


if __name__ == "__main__":
    main()
