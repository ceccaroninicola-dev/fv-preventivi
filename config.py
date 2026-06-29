# -*- coding: utf-8 -*-
"""
config.py — carica i parametri commerciali da parametri.json e li espone al motore.
I numeri NON stanno piu' qui: si modificano dal pannello web (pannello.py) o editando il JSON.
Le funzioni prezzo_impianto / prezzo_accumulo restano invariate: il motore non cambia.
"""
import os
import json

_QUI = os.path.dirname(os.path.abspath(__file__))
PARAMETRI_FILE = os.path.join(_QUI, "parametri.json")


def carica():
    """Rilegge il file dei parametri. Chiamala a inizio run per avere i valori freschi."""
    with open(PARAMETRI_FILE, encoding="utf-8") as f:
        return json.load(f)


_P = carica()

PANNELLO = _P["pannello"]
PRODUCIBILITA_KWH_PER_KWP = _P["tecnici"]["producibilita_kwh_per_kwp"]
PERDITE_SISTEMA = _P["tecnici"]["perdite_sistema"]
DERATE_AC = 1 - PERDITE_SISTEMA

LISTINO_PRIVATI = {int(k): v for k, v in _P["privati"]["listino"].items()}
MAX_KWP_PRIVATI = _P["privati"]["max_kwp"]
COPERTURA_MINIMA = _P["privati"]["copertura_minima"]

FASCE_AZIENDE = [(f["kwp_min"], f["kwp_max"], f["prezzo_per_kwp"]) for f in _P["aziende"]["fasce"]]
MODELLO_PREZZO_AZIENDE = _P["aziende"].get("modello_prezzo", "flat_per_fascia")

LISTINO_ACCUMULO = {int(k): v for k, v in _P["accumulo"]["listino"].items()}
ACCUMULO_DOD = _P["accumulo"]["dod"]

PREZZO_ELETTRICITA = _P["economici"]["prezzo_elettricita"]
PREZZO_GAS = _P["economici"]["prezzo_gas"]
INFLAZIONE_ENERGIA = _P["economici"]["inflazione_energia"]
VALORE_IMMISSIONE = _P["economici"]["valore_immissione_eur_kwh"]
AUTOCONSUMO = {
    "senza_batteria": _P["economici"]["autoconsumo_senza_batteria"],
    "con_batteria": _P["economici"]["autoconsumo_con_batteria"],
}
DETRAZIONE_PRIVATI = {"aliquota": _P["detrazioni"]["privati_aliquota"], "anni": _P["detrazioni"]["privati_anni"]}


def prezzo_impianto(segmento, kwp):
    """(prezzo_euro, taglia/fascia, nota). prezzo None = non ancora disponibile."""
    seg = (segmento or "").upper()
    if seg.startswith("PRIV"):
        taglie = sorted(LISTINO_PRIVATI)
        scelta = next((t for t in taglie if t >= kwp), MAX_KWP_PRIVATI)
        scelta = min(scelta, MAX_KWP_PRIVATI)
        return LISTINO_PRIVATI[scelta], f"{scelta} kWp", ""
    if seg.startswith("AZIEND"):
        for kmin, kmax, eur_kwp in FASCE_AZIENDE:
            if kmin <= kwp < kmax:
                fascia = f"{kmin}-{kmax} kWp"
                if eur_kwp is None:
                    return None, fascia, f"prezzo fascia {fascia} non ancora fornito"
                return round(eur_kwp * kwp, 2), fascia, ""
        return None, ">1000 kWp", "oltre l'ultima fascia prevista (500-1000)"
    return None, "?", f"segmento sconosciuto: {segmento}"


def prezzo_accumulo(kwh):
    taglie = sorted(LISTINO_ACCUMULO)
    scelta = next((t for t in taglie if t >= kwh), taglie[-1])
    return LISTINO_ACCUMULO[scelta], f"{scelta} kWh"


if __name__ == "__main__":
    print("Parametri caricati da:", PARAMETRI_FILE)
    print("Pannello:", PANNELLO["modello"], "-", PANNELLO["potenza_wp"], "Wp")
    print("Derate AC:", DERATE_AC, "| Elettricita':", PREZZO_ELETTRICITA, "euro/kWh")
    print("Listino privati:", LISTINO_PRIVATI)
    print("Fasce aziende con prezzo:",
          sum(1 for _, _, p in FASCE_AZIENDE if p is not None), "su", len(FASCE_AZIENDE))
