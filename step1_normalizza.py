#!/usr/bin/env python3
"""
Step 1 della pipeline preventivi FV: normalizzazione indirizzi + quality flags.
Input:  estratti CRM xlsx (privati / aziende)
Output: clienti_normalizzati.csv pronto per lo step di geocoding,
        con colonna FLAGS e STATO (OK / DA_RIVEDERE / SCARTA).
"""
import re
import sys
import pandas as pd

FILES = {
    "PRIVATO": "/mnt/user-data/uploads/P01_Privato_Fornitura_Attiva_Consumi_Alti_-_Estratto.xlsx",
    "AZIENDA": "/mnt/user-data/uploads/AZ01_Aziende_Fornitura_Attiva_Consumi_Alti_-_Estratti.xlsx",
}

# CAP -> range note: 478xx / 479xx = Rimini area, 4752x = Cesena, 471xx-472xx = Forlì, 4789x = San Marino
SAN_MARINO_CAPS = {str(c) for c in range(47890, 47900)}
RSM_HINTS = ("RSM", "SAN MARINO", "DOGANA", "DOMAGNANO", "SERRAVALLE", "BORGO MAGGIORE")

ABBREV = {
    r"\bS\.ARCANGELO\b": "SANTARCANGELO DI ROMAGNA",
    r"\bP\.LE\b": "PIAZZALE",
    r"\bP\.ZZA\b": "PIAZZA",
    r"\bV\.LE\b": "VIALE",
}

# frazioni note -> comune (estendibile; serve per geocoding più pulito)
FRAZIONI = {
    "SAN FORTUNATO RIMINI": "RIMINI",
    "SANTA GIUSTINA": "RIMINI",
    "VERGIANO": "RIMINI",
    "MIRAMARE DI RIMINI": "RIMINI",
    "SCACCIANO MISANO": "MISANO ADRIATICO",
    "STRADONE CIOLA S.ARCANGELO": "SANTARCANGELO DI ROMAGNA",
    "CAMERANO POGGIO BERNI": "POGGIO TORRIANA",
    "POGGIO BERNI": "POGGIO TORRIANA",
    "BELLARIA IGEA MARINA": "BELLARIA-IGEA MARINA",
    "IGEA MARINA": "BELLARIA-IGEA MARINA",
    "TORRE PEDRERA": "RIMINI",
    "SAN GIOVANNI MARIGNANO": "SAN GIOVANNI IN MARIGNANO",
}

STOPWORDS = {"DI", "IN", "AL", "DEL", "DELLA"}


def stesso_comune(a: str, b: str) -> bool:
    """Confronto tollerante: ignora trattini, stopword e frazioni note."""
    a, b = FRAZIONI.get(a, a), FRAZIONI.get(b, b)
    tok = lambda s: {t for t in re.split(r"[\s\-']+", s) if t and t not in STOPWORDS}
    ta, tb = tok(a), tok(b)
    return ta == tb or ta <= tb or tb <= ta

CONSUMO_MAX_PLAUSIBILE = {"PRIVATO": 30000, "AZIENDA": 1000000}


def norm_text(s: str) -> str:
    if not isinstance(s, str):
        return ""
    s = s.encode("latin-1", "ignore").decode("utf-8", "ignore") if "Ã" in s else s
    s = re.sub(r"\s+", " ", s).strip().upper()
    for pat, rep in ABBREV.items():
        s = re.sub(pat, rep, s)
    return s


def parse_indirizzo(raw: str):
    """'VIA X 12,COMUNE 47838' -> (via_civico, comune, cap, flags)"""
    flags = []
    raw = norm_text(raw)
    parts = [p.strip() for p in raw.split(",")]
    street = parts[0] if parts else ""
    rest = ",".join(parts[1:]).strip() if len(parts) > 1 else ""

    cap = ""
    m = re.search(r"\b(\d{5})\b\s*$", rest)
    if m:
        cap = m.group(1)
        rest = rest[: m.start()].strip()
    comune = rest

    if not comune:
        flags.append("COMUNE_MANCANTE")
    if not cap:
        flags.append("CAP_MANCANTE")
    if comune in FRAZIONI:
        flags.append(f"FRAZIONE({comune})")
        comune = FRAZIONI[comune]
    elif comune and FRAZIONI.get(comune, comune) != comune:
        comune = FRAZIONI[comune]

    # civico presente nello street?
    if not re.search(r"\d", street):
        flags.append("CIVICO_MANCANTE")
    else:
        mciv = re.search(r"(\d{3,5})\s*$", street)
        if mciv and int(mciv.group(1)) > 500:
            flags.append("CIVICO_ALTO")  # tipico strade provinciali: geocoding da verificare

    if cap in SAN_MARINO_CAPS or any(h in raw for h in RSM_HINTS):
        flags.append("SAN_MARINO")

    return street, comune, cap, flags


def process(segmento: str, path: str) -> pd.DataFrame:
    df = pd.read_excel(path)
    rows = []
    for _, r in df.iterrows():
        street, comune, cap, flags = parse_indirizzo(r["INDIRIZZO_LEGALE"])
        comune_pod = norm_text(r["COMUNE_POD_FORNITURA"])
        comune_legale = norm_text(r["COMUNE_LEGALE_CONTRACT_FORNITURA"])

        # comune mancante nell'indirizzo: prova recupero dal POD
        if "COMUNE_MANCANTE" in flags and comune_pod:
            comune = comune_pod
            flags.append("COMUNE_RECUPERATO_DA_POD")

        # mismatch indirizzo legale vs comune POD -> il tetto potrebbe essere altrove
        if comune and comune_pod and not stesso_comune(comune, comune_pod):
            flags.append(f"MISMATCH_POD({comune_pod})")

        consumo = float(r["CONSUMO_ANNUO_PRESUNTO"])
        if consumo > CONSUMO_MAX_PLAUSIBILE[segmento]:
            flags.append("CONSUMO_ANOMALO")

        # stato finale
        if "SAN_MARINO" in flags:
            stato = "SCARTA"  # copertura Solar API da verificare separatamente
        elif any(f.startswith("MISMATCH_POD") for f in flags):
            stato = "SCARTA"  # rischio preventivo sul tetto sbagliato
        elif any(f in flags for f in ("CIVICO_MANCANTE",)) or "CONSUMO_ANOMALO" in flags or "CIVICO_ALTO" in flags:
            stato = "DA_RIVEDERE"
        else:
            stato = "OK"

        rows.append({
            "ID_CLIENTE": r["CD_ACCOUNT_CRM_FORNITURA"],
            "SEGMENTO": segmento,
            "NOME": norm_text(r["NOME_BENEFICIARIO"]),
            "TIPO_CLIENTE": r["TIPO_CLIENTE"],
            "VIA_CIVICO": street,
            "COMUNE": comune,
            "CAP": cap,
            "INDIRIZZO_GEOCODING": f"{street}, {cap} {comune}, Italia".strip(),
            "COMUNE_POD": comune_pod,
            "POTENZA_DISP_KW": r["POTENZA_DISPONIBILE_POD_CRM_FORNITURA"],
            "CONSUMO_KWH_ANNO": consumo,
            "ATECO": r.get("CODICE_ATECO", ""),
            "DESCR_ATTIVITA": norm_text(r.get("DESCRIZIONE_CATEGORIA", "")) if segmento == "AZIENDA" else "",
            "FLAGS": "|".join(flags) if flags else "",
            "STATO": stato,
            # campi predisposti per i moduli successivi (lat/lng, solar, modulo 2)
            "LAT": "", "LNG": "", "GEOCODE_TYPE": "",
            "ANNO_COSTRUZIONE": "", "ETA_CALDAIA": "",
        })
    return pd.DataFrame(rows)


def main():
    out = pd.concat([process(seg, p) for seg, p in FILES.items()], ignore_index=True)
    out.to_csv("/home/claude/clienti_normalizzati.csv", index=False)

    print(f"Totale record: {len(out)}")
    print(out.groupby(["SEGMENTO", "STATO"]).size().to_string())
    print("\nFlag più frequenti:")
    allflags = out["FLAGS"].str.split("|").explode()
    print(allflags[allflags != ""].str.replace(r"\(.*\)", "", regex=True).value_counts().to_string())
    print("\nRecord SCARTA / DA_RIVEDERE:")
    cols = ["SEGMENTO", "NOME", "INDIRIZZO_GEOCODING", "FLAGS", "STATO"]
    print(out[out["STATO"] != "OK"][cols].to_string(index=False))


if __name__ == "__main__":
    main()
