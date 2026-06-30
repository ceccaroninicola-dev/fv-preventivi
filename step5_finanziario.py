# -*- coding: utf-8 -*-
"""
step5_finanziario.py - Analisi finanziaria dei preventivi FV (segmento PRIVATI).

Input:  clienti_dimensionati.csv  (output di step4_dimensionamento.py)
Output: clienti_finanziario.csv   (aggiunge le colonne economiche sotto)
Usa:    config.py  (tutti i parametri commerciali; nessun valore e' hardcoded qui)

Per ogni cliente PRIVATO gia' dimensionato (KWP_CONSIGLIATO > 0 e PREZZO_EUR
valorizzato) calcola autoconsumo, energia immessa, risparmio anno 1, detrazione,
costo netto, payback semplice e la proiezione del risparmio a 20 anni.

Le AZIENDE vengono saltate (prezzo per fascia non ancora disponibile) e marcate
con una nota nella colonna NOTA_FINANZIARIO: nessun calcolo viene fatto per loro.

Solo libreria standard.  Avvio su Windows:  py step5_finanziario.py
"""
import csv
import config as C

INPUT = "clienti_dimensionati.csv"
OUTPUT = "clienti_finanziario.csv"

ORIZZONTE_ANNI = 20

# --- parametri commerciali: tutti letti da config.py / parametri.json ---
AUTOCONSUMO_SENZA_BATT = C.AUTOCONSUMO["senza_batteria"]   # quota di produzione autoconsumata
PREZZO_ELETTRICITA = C.PREZZO_ELETTRICITA                  # euro/kWh risparmiati in autoconsumo
VALORE_IMMISSIONE = C.VALORE_IMMISSIONE                    # euro/kWh per l'energia immessa in rete
DETRAZIONE_ALIQUOTA = C.DETRAZIONE_PRIVATI["aliquota"]     # 0.50 per i privati
INFLAZIONE_ENERGIA = C.INFLAZIONE_ENERGIA                  # crescita annua del risparmio
DEGRADAZIONE_ANNUA = C.PANNELLO["degradazione_annua"]      # calo annuo della produzione

# colonne aggiunte da questo step
COLONNE = [
    "AUTOCONSUMO_KWH", "ENERGIA_IMMESSA_KWH", "RISPARMIO_ANNUO_ANNO1",
    "DETRAZIONE", "COSTO_NETTO", "PAYBACK_ANNI",
    "RISPARMIO_20ANNI", "GUADAGNO_NETTO_20ANNI", "NOTA_FINANZIARIO",
]


def num(x):
    """Converte in float gestendo vuoti e virgola decimale. None se non numerico."""
    if x is None:
        return None
    s = str(x).strip().replace(",", ".")
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def e_privato(seg):
    return (seg or "").upper().startswith("PRIV")


def e_azienda(seg):
    return (seg or "").upper().startswith("AZIEND")


def calcola(produzione, prezzo):
    """Colonne economiche per un privato dimensionato. produzione in kWh/anno, prezzo in euro."""
    autoconsumo = produzione * AUTOCONSUMO_SENZA_BATT
    immessa = produzione - autoconsumo
    risparmio_autoconsumo = autoconsumo * PREZZO_ELETTRICITA
    valore_immissione = immessa * VALORE_IMMISSIONE
    risparmio_anno1 = risparmio_autoconsumo + valore_immissione

    # Detrazione 50% per TUTTI i privati: vale anche per i domestici trifase sopra 6 kWp il
    # cui PREZZO arriva dalle fasce aziende. Il prezzo viene dalle fasce, ma il trattamento
    # fiscale resta privato. Nessuna condizione sui 20 kWp: il dimensionamento (step4) ferma
    # gia' i domestici a max_kwp_domestico, quindi non arrivano mai sopra.
    detrazione = prezzo * DETRAZIONE_ALIQUOTA
    costo_netto = prezzo - detrazione
    payback = costo_netto / risparmio_anno1 if risparmio_anno1 > 0 else None

    # Proiezione a 20 anni, anno per anno:
    #   - il risparmio cresce con l'inflazione energia (+INFLAZIONE/anno)
    #   - la produzione cala con la degradazione (-DEGRADAZIONE/anno)
    # anno 0 -> fattore 1 (= anno 1 reale, niente inflazione/degradazione ancora applicate)
    risparmio_20 = 0.0
    for anno in range(ORIZZONTE_ANNI):
        fattore = ((1 + INFLAZIONE_ENERGIA) ** anno) * ((1 - DEGRADAZIONE_ANNUA) ** anno)
        risparmio_20 += risparmio_anno1 * fattore
    guadagno_20 = risparmio_20 - costo_netto

    return {
        "AUTOCONSUMO_KWH": round(autoconsumo, 1),
        "ENERGIA_IMMESSA_KWH": round(immessa, 1),
        "RISPARMIO_ANNUO_ANNO1": round(risparmio_anno1, 2),
        "DETRAZIONE": round(detrazione, 2),
        "COSTO_NETTO": round(costo_netto, 2),
        "PAYBACK_ANNI": round(payback, 1) if payback is not None else "",
        "RISPARMIO_20ANNI": round(risparmio_20, 2),
        "GUADAGNO_NETTO_20ANNI": round(guadagno_20, 2),
        "NOTA_FINANZIARIO": "",
    }


def vuote(nota=""):
    d = {c: "" for c in COLONNE}
    d["NOTA_FINANZIARIO"] = nota
    return d


def main():
    with open(INPUT, encoding="utf-8") as f:
        righe = list(csv.DictReader(f))
    if not righe:
        print(f"Nessuna riga in {INPUT}: niente da elaborare.")
        return

    stat = {"privati_calcolati": 0, "aziende_saltate": 0,
            "privati_saltati": 0, "altro_saltato": 0}
    payback_list, risp20_list, guad20_list = [], [], []

    for row in righe:
        for c in COLONNE:
            row.setdefault(c, "")
        seg = row.get("SEGMENTO")
        kwp = num(row.get("KWP_CONSIGLIATO")) or 0
        prezzo = num(row.get("PREZZO_EUR"))
        produzione = num(row.get("PRODUZIONE_KWH_ANNO")) or 0

        if e_azienda(seg):
            row.update(vuote("AZIENDA: prezzo per fascia non disponibile, calcolo saltato"))
            stat["aziende_saltate"] += 1
            continue

        if not e_privato(seg):
            row.update(vuote(f"segmento non gestito ({seg or 'vuoto'}), calcolo saltato"))
            stat["altro_saltato"] += 1
            continue

        # da qui in poi: PRIVATO
        if kwp <= 0 or prezzo is None or prezzo <= 0 or produzione <= 0:
            motivi = []
            if kwp <= 0:
                motivi.append("non dimensionato")
            if prezzo is None or prezzo <= 0:
                motivi.append("prezzo assente")
            if produzione <= 0:
                motivi.append("produzione assente")
            row.update(vuote("PRIVATO saltato: " + ", ".join(motivi)))
            stat["privati_saltati"] += 1
            continue

        res = calcola(produzione, prezzo)
        row.update(res)
        stat["privati_calcolati"] += 1
        payback_list.append(res["PAYBACK_ANNI"] if res["PAYBACK_ANNI"] != "" else None)
        risp20_list.append(res["RISPARMIO_20ANNI"])
        guad20_list.append(res["GUADAGNO_NETTO_20ANNI"])

    # scrittura output: colonne originali + nuove (in coda, senza toccare le esistenti)
    cols = list(righe[0].keys())
    for c in COLONNE:
        if c not in cols:
            cols.append(c)
    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(righe)

    # --- riepilogo a video ---
    print("=== ANALISI FINANZIARIA (PRIVATI) ===")
    print(f"Privati calcolati: {stat['privati_calcolati']}")
    print(f"Aziende saltate (prezzo non disponibile): {stat['aziende_saltate']}")
    print(f"Privati saltati (non dimensionabili / senza prezzo): {stat['privati_saltati']}")
    if stat["altro_saltato"]:
        print(f"Altri segmenti saltati: {stat['altro_saltato']}")
    print(f"\nParametri: autoconsumo {AUTOCONSUMO_SENZA_BATT:.0%}, "
          f"elettricita {PREZZO_ELETTRICITA} euro/kWh, immissione {VALORE_IMMISSIONE} euro/kWh, "
          f"detrazione {DETRAZIONE_ALIQUOTA:.0%}, inflazione {INFLAZIONE_ENERGIA:.1%}/anno, "
          f"degradazione {DEGRADAZIONE_ANNUA:.1%}/anno, orizzonte {ORIZZONTE_ANNI} anni")
    print(f"\nOutput: {OUTPUT}\n")

    print("Esempio (primi 4 privati calcolati):")
    n = 0
    for r in righe:
        if e_privato(r.get("SEGMENTO")) and r.get("RISPARMIO_ANNUO_ANNO1") not in ("", None):
            nome = (r.get("NOME") or "")[:22]
            print(f"  {nome:22} {r['KWP_CONSIGLIATO']} kWp | prezzo {r['PREZZO_EUR']} euro | "
                  f"costo netto {r['COSTO_NETTO']} euro | risp.anno1 {r['RISPARMIO_ANNUO_ANNO1']} euro | "
                  f"payback {r['PAYBACK_ANNI']} anni | guadagno 20a {r['GUADAGNO_NETTO_20ANNI']} euro")
            n += 1
            if n >= 4:
                break
    if n == 0:
        print("  (nessun privato con calcolo disponibile)")

    # --- aggregato ---
    if stat["privati_calcolati"]:
        pb = [p for p in payback_list if p is not None]
        def media(L):
            return round(sum(L) / len(L), 2) if L else 0
        print("\nRiepilogo privati:")
        print(f"  Payback medio:                 {media(pb)} anni")
        print(f"  Risparmio 20 anni (medio):     {media(risp20_list)} euro")
        print(f"  Guadagno netto 20 anni (medio):{media(guad20_list)} euro")
        print(f"  Guadagno netto 20 anni (tot.): {round(sum(guad20_list), 2)} euro")


if __name__ == "__main__":
    main()
