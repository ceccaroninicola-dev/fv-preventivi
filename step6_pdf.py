# -*- coding: utf-8 -*-
"""
step6_pdf.py - Generazione del PDF di preventivo fotovoltaico (segmento PRIVATI).

Input:  clienti_finanziario.csv  (output di step5_finanziario.py)
Output: preventivi_pdf/<ID_CLIENTE>.pdf

NON ricalcola nulla: tutti i numeri vengono letti dal CSV; i parametri commerciali
(modello pannello, inflazione, degradazione) da config.py/parametri.json.

Uso (Windows):
    py step6_pdf.py <ID_CLIENTE>     genera il PDF di quel cliente
    py step6_pdf.py                  genera il PDF del primo privato valido (prova rapida)

Librerie NON standard (le uniche del progetto): reportlab + svglib.
    pip install reportlab svglib --break-system-packages
Il logo e' un SVG: reportlab non legge SVG, quindi svglib lo converte in un
disegno vettoriale che viene disegnato sulla testata azzurra. Vedi README.md.

Solo i PRIVATI sono gestiti: le aziende vengono rifiutate con un messaggio.
"""
import os
import sys
import csv
import config as C

INPUT = "clienti_finanziario.csv"
OUTPUT_DIR = "preventivi_pdf"
LOGO_SVG = "logo.f586e6.svg"

BRAND = "#3BA9DD"          # azzurro SGR
DISCLAIMER = "Stima indicativa soggetta a sopralluogo tecnico."
ORIZZONTE_ANNI = 20

# parametri letti da config (per ricostruire SOLO la curva del grafico, con la
# stessa formula di step5: nessun importo viene ricalcolato)
INFLAZIONE_ENERGIA = C.INFLAZIONE_ENERGIA
DEGRADAZIONE_ANNUA = C.PANNELLO["degradazione_annua"]
MODELLO_PANNELLO = C.PANNELLO["modello"]


# --------------------------------------------------------------------------- #
# Utilita'
# --------------------------------------------------------------------------- #
def num(x):
    """float da stringa CSV (vuoto/virgola gestiti). None se non numerico."""
    if x is None:
        return None
    s = str(x).strip().replace(",", ".")
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def it_num(x, dec=0):
    """Numero in formato italiano: 1.234,5  (migliaia '.', decimali ',')."""
    if x is None:
        return "n/d"
    s = f"{x:,.{dec}f}"                       # formato US: 1,234.5
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def it_eur(x, dec=0):
    return "n/d" if x is None else it_num(x, dec) + " €"


def e_privato(seg):
    return (seg or "").upper().startswith("PRIV")


def e_azienda(seg):
    return (seg or "").upper().startswith("AZIEND")


def curva_guadagno_cumulato(risparmio_anno1, costo_netto):
    """Guadagno netto cumulato anno per anno (anni 1..20), stessa formula di step5.
    Ritorna None se mancano i dati di base."""
    if risparmio_anno1 is None or costo_netto is None:
        return None
    f = (1 + INFLAZIONE_ENERGIA) * (1 - DEGRADAZIONE_ANNUA)
    cum = 0.0
    out = []
    for anno in range(ORIZZONTE_ANNI):
        cum += risparmio_anno1 * (f ** anno)
        out.append(cum - costo_netto)
    return out


# --------------------------------------------------------------------------- #
# Logo
# --------------------------------------------------------------------------- #
def disegna_logo(c, x, y, altezza):
    """Disegna il logo SVG (bianco) alto 'altezza' pt con angolo in basso a (x, y).
    Se svglib non e' disponibile o l'SVG non si legge, ripiega su un testo bianco."""
    try:
        from svglib.svglib import svg2rlg
        from reportlab.graphics import renderPDF
        d = svg2rlg(LOGO_SVG)
        if d is None or not d.height:
            raise ValueError("SVG non leggibile")
        scala = altezza / d.height
        d.scale(scala, scala)
        d.width *= scala
        d.height *= scala
        renderPDF.draw(d, c, x, y)
        return
    except Exception as e:
        from reportlab.lib import colors
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 18)
        c.drawString(x, y + altezza / 2 - 6, "SGR")
        c.setFont("Helvetica", 8)
        c.drawString(x, y - 2, "Efficienza Energetica")
        sys.stderr.write(f"[avviso] logo SVG non reso ({e}); uso testo di ripiego.\n")


# --------------------------------------------------------------------------- #
# Grafico
# --------------------------------------------------------------------------- #
def disegna_grafico(c, x, y, w, h, gains):
    from reportlab.lib import colors
    az = colors.HexColor(BRAND)
    grigio = colors.HexColor("#999999")
    bordo = colors.HexColor("#cccccc")

    c.setStrokeColor(bordo)
    c.setLineWidth(0.5)
    c.rect(x, y, w, h, stroke=1, fill=0)

    vmin = min(0.0, min(gains))
    vmax = max(0.0, max(gains))
    span = (vmax - vmin) or 1.0
    n = len(gains)

    def px(i):
        return x + (w * i / (n - 1) if n > 1 else 0)

    def py(v):
        return y + (v - vmin) / span * h

    # linea dello zero
    zy = py(0.0)
    c.setStrokeColor(grigio)
    c.setDash(2, 2)
    c.line(x, zy, x + w, zy)
    c.setDash()
    c.setFont("Helvetica", 6)
    c.setFillColor(grigio)
    c.drawString(x + 2, zy + 2, "0")

    # spezzata del guadagno cumulato
    pts = [(px(i), py(g)) for i, g in enumerate(gains)]
    c.setStrokeColor(az)
    c.setLineWidth(1.6)
    p = c.beginPath()
    p.moveTo(*pts[0])
    for pt in pts[1:]:
        p.lineTo(*pt)
    c.drawPath(p, stroke=1, fill=0)

    # marcatori inizio/fine
    c.setFillColor(az)
    for i in (0, n - 1):
        c.circle(pts[i][0], pts[i][1], 2.2, stroke=0, fill=1)

    # etichette assi e valori
    c.setFont("Helvetica", 7)
    c.setFillColor(colors.HexColor("#555555"))
    c.drawString(x, y - 10, "Anno 1")
    c.drawRightString(x + w, y - 10, f"Anno {n}")
    c.drawString(min(pts[0][0] + 3, x + w - 40), pts[0][1] + 4, it_eur(gains[0]))
    c.drawRightString(x + w - 3, pts[-1][1] + 4, it_eur(gains[-1]))


# --------------------------------------------------------------------------- #
# Costruzione PDF
# --------------------------------------------------------------------------- #
def genera_pdf(row, percorso):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.pdfgen import canvas

    W, H = A4
    az = colors.HexColor(BRAND)
    scuro = colors.HexColor("#333333")
    c = canvas.Canvas(percorso, pagesize=A4)

    g = row.get  # scorciatoia

    # --- testata azzurra ---
    band_h = 80
    c.setFillColor(az)
    c.rect(0, H - band_h, W, band_h, stroke=0, fill=1)
    disegna_logo(c, 40, H - band_h + (band_h - 30) / 2, 30)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(220, H - band_h / 2 - 5, "Preventivo impianto fotovoltaico")

    margine = 40
    cur = H - band_h - 30

    def titolo_sezione(testo, y):
        c.setFillColor(az)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(margine, y, testo)
        c.setStrokeColor(az)
        c.setLineWidth(0.8)
        c.line(margine, y - 4, W - margine, y - 4)
        return y - 22

    def riga(label, valore, y):
        c.setFillColor(colors.HexColor("#666666"))
        c.setFont("Helvetica", 10)
        c.drawString(margine, y, label)
        c.setFillColor(scuro)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(margine + 200, y, valore)
        return y - 16

    # --- dati cliente ---
    cur = titolo_sezione("Dati cliente", cur)
    cur = riga("Nominativo", (g("NOME") or "n/d").strip() or "n/d", cur)
    cur = riga("Comune", (g("COMUNE") or "n/d").strip() or "n/d", cur)

    # --- impianto proposto ---
    cur -= 8
    cur = titolo_sezione("Impianto proposto", cur)
    kwp = num(g("KWP_CONSIGLIATO"))
    npan = num(g("N_PANNELLI"))
    prod = num(g("PRODUZIONE_KWH_ANNO"))
    cur = riga("Potenza impianto", it_num(kwp, 1) + " kWp" if kwp is not None else "n/d", cur)
    cur = riga("Numero pannelli",
               (it_num(npan, 0) if npan is not None else "n/d") + f"  -  {MODELLO_PANNELLO}", cur)
    cur = riga("Produzione annua stimata",
               it_num(prod, 0) + " kWh" if prod is not None else "n/d", cur)

    # --- box economico in evidenza ---
    cur -= 14
    box_x, box_w = margine, W - 2 * margine
    box_h = 110
    box_y = cur - box_h
    c.setFillColor(colors.HexColor("#eaf6fc"))
    c.setStrokeColor(az)
    c.setLineWidth(1)
    c.roundRect(box_x, box_y, box_w, box_h, 8, stroke=1, fill=1)
    c.setFillColor(az)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(box_x + 14, box_y + box_h - 20, "Riepilogo economico")

    prezzo = num(g("PREZZO_EUR"))
    detr = num(g("DETRAZIONE"))
    netto = num(g("COSTO_NETTO"))
    risp1 = num(g("RISPARMIO_ANNUO_ANNO1"))
    payback = num(g("PAYBACK_ANNI"))
    guad20 = num(g("GUADAGNO_NETTO_20ANNI"))

    voci = [
        ("Prezzo impianto", it_eur(prezzo)),
        ("Detrazione fiscale 50%", "- " + it_eur(detr) if detr is not None else "n/d"),
        ("Costo netto", it_eur(netto)),
        ("Risparmio primo anno", it_eur(risp1)),
        ("Tempo di rientro", it_num(payback, 1) + " anni" if payback is not None else "n/d"),
        ("Guadagno stimato a 20 anni", it_eur(guad20)),
    ]
    col_w = box_w / 2
    ry = box_y + box_h - 44
    for i, (lab, val) in enumerate(voci):
        cx = box_x + 14 + (i % 2) * col_w
        if i % 2 == 0 and i > 0:
            ry -= 22
        c.setFillColor(colors.HexColor("#555555"))
        c.setFont("Helvetica", 9)
        c.drawString(cx, ry, lab)
        # valori principali in azzurro grande
        principale = lab in ("Costo netto", "Tempo di rientro", "Guadagno stimato a 20 anni")
        c.setFillColor(az if principale else scuro)
        c.setFont("Helvetica-Bold", 12 if principale else 10)
        c.drawString(cx, ry - 14, val)
    cur = box_y - 24

    # --- grafico guadagno cumulato ---
    cur = titolo_sezione("Guadagno cumulato nei 20 anni", cur)
    gains = curva_guadagno_cumulato(risp1, netto)
    if gains:
        disegna_grafico(c, margine, cur - 130, W - 2 * margine, 120, gains)
        cur = cur - 130 - 26
    else:
        c.setFillColor(colors.HexColor("#888888"))
        c.setFont("Helvetica-Oblique", 9)
        c.drawString(margine, cur, "Dati insufficienti per il grafico.")
        cur -= 26

    # --- disclaimer ---
    c.setStrokeColor(colors.HexColor("#dddddd"))
    c.setLineWidth(0.5)
    c.line(margine, 56, W - margine, 56)
    c.setFillColor(colors.HexColor("#777777"))
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(margine, 44, DISCLAIMER)
    c.setFont("Helvetica", 8)
    c.drawString(margine, 30, "SGR Efficienza Energetica")
    c.drawRightString(W - margine, 30, f"Cliente: {g('ID_CLIENTE') or 'n/d'}")

    c.showPage()
    c.save()


# --------------------------------------------------------------------------- #
# Selezione cliente / main
# --------------------------------------------------------------------------- #
def seleziona(righe, id_cliente):
    if id_cliente is not None:
        for r in righe:
            if (r.get("ID_CLIENTE") or "").strip() == id_cliente.strip():
                return r
        return None
    # nessun ID: primo privato valido (calcolato da step5)
    for r in righe:
        if e_privato(r.get("SEGMENTO")) and num(r.get("RISPARMIO_ANNUO_ANNO1")) is not None:
            return r
    return None


def main():
    id_cliente = sys.argv[1] if len(sys.argv) > 1 else None

    if not os.path.exists(INPUT):
        print(f"File di input non trovato: {INPUT}. Esegui prima step5_finanziario.py.")
        return
    with open(INPUT, encoding="utf-8") as f:
        righe = list(csv.DictReader(f))

    row = seleziona(righe, id_cliente)
    if row is None:
        if id_cliente is not None:
            print(f"Nessun cliente con ID_CLIENTE = {id_cliente} in {INPUT}.")
        else:
            print("Nessun cliente privato valido trovato per la prova.")
        return

    if e_azienda(row.get("SEGMENTO")):
        print(f"ID {row.get('ID_CLIENTE')}: cliente AZIENDA. "
              "Il PDF e' previsto solo per i PRIVATI.")
        return
    if not e_privato(row.get("SEGMENTO")):
        print(f"ID {row.get('ID_CLIENTE')}: segmento '{row.get('SEGMENTO')}' non gestito "
              "(PDF solo privati).")
        return

    if num(row.get("KWP_CONSIGLIATO")) is None or num(row.get("PREZZO_EUR")) is None:
        print(f"[avviso] ID {row.get('ID_CLIENTE')}: dati impianto/prezzo incompleti, "
              "il PDF riportera' 'n/d' dove mancano.")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    cid = (row.get("ID_CLIENTE") or "cliente").strip() or "cliente"
    percorso = os.path.join(OUTPUT_DIR, f"{cid}.pdf")
    genera_pdf(row, percorso)
    print(f"PDF generato: {percorso}")
    print(f"  Cliente: {row.get('NOME', 'n/d')} ({row.get('COMUNE', 'n/d')}) - "
          f"{it_num(num(row.get('KWP_CONSIGLIATO')), 1)} kWp, "
          f"costo netto {it_eur(num(row.get('COSTO_NETTO')))}, "
          f"payback {it_num(num(row.get('PAYBACK_ANNI')), 1)} anni")


if __name__ == "__main__":
    main()
