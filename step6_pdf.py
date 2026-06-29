# -*- coding: utf-8 -*-
"""
step6_pdf.py - PDF di preventivo fotovoltaico ad alto impatto commerciale (PRIVATI).

Input:  clienti_finanziario.csv  (output di step5_finanziario.py)
Output: preventivi_pdf/<ID_CLIENTE>.pdf

NON ricalcola nulla: gli importi vengono letti dal CSV; i parametri commerciali
(modello pannello, garanzia, inflazione, degradazione, prezzo elettricita) da
config.py/parametri.json.

Uso (Windows):
    py step6_pdf.py <ID_CLIENTE>     genera il PDF di quel cliente
    py step6_pdf.py                  genera il PDF del primo privato valido (prova)

Librerie non standard (le uniche del progetto): reportlab + svglib.
    pip install reportlab svglib --break-system-packages

Immagine satellitare del tetto: scaricata dalla Google Static Maps API usando
LAT/LNG del CSV e la chiave os.environ["GOOGLE_MAPS_KEY"]; messa in cache in
tetti_cache/<ID>.png per non ripagarla. Se chiave/coordinate mancano o il
download fallisce, si usa un placeholder grigio (nessun crash).

Solo PRIVATI: le aziende vengono rifiutate con un messaggio.
"""
import os
import sys
import csv
import urllib.request
import urllib.parse
import config as C

INPUT = "clienti_finanziario.csv"
OUTPUT_DIR = "preventivi_pdf"
TETTI_DIR = "tetti_cache"
LOGO_SVG = "logo.f586e6.svg"

DISCLAIMER = "Stima indicativa soggetta a sopralluogo tecnico."
ORIZZONTE_ANNI = 20

# parametri da config (per ricostruire SOLO la curva del grafico e la spesa "stima")
INFLAZIONE_ENERGIA = C.INFLAZIONE_ENERGIA
DEGRADAZIONE_ANNUA = C.PANNELLO["degradazione_annua"]
MODELLO_PANNELLO = C.PANNELLO["modello"]
GARANZIA_ANNI = C.PANNELLO.get("garanzia_potenza_anni")
PREZZO_ELETTRICITA = C.PREZZO_ELETTRICITA

# Static Maps
SAT_ZOOM = 20
SAT_SIZE = "600x400"

# --- colori brand ---
BRAND = "#3BA9DD"
BRAND_DARK = "#1C7FAE"
GREEN = "#2E9E5B"
GREEN_LIGHT = "#E3F5EA"
RED = "#C9544F"
RED_LIGHT = "#FBE6E5"
INK = "#2B2B2B"
MUTE = "#6B7785"
HAIR = "#D9E1E7"


def _hex(s):
    from reportlab.lib.colors import HexColor
    return HexColor(s)


# --------------------------------------------------------------------------- #
# Utilita' numeri
# --------------------------------------------------------------------------- #
def num(x):
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
    if x is None:
        return "n/d"
    s = f"{x:,.{dec}f}"
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def it_eur(x, dec=0):
    return "n/d" if x is None else it_num(x, dec) + " €"


def e_privato(seg):
    return (seg or "").upper().startswith("PRIV")


def e_azienda(seg):
    return (seg or "").upper().startswith("AZIEND")


def curva_guadagno_cumulato(risparmio_anno1, costo_netto):
    """Guadagno netto cumulato anni 1..20 (stessa formula di step5). None se mancano dati."""
    if risparmio_anno1 is None or costo_netto is None:
        return None
    f = (1 + INFLAZIONE_ENERGIA) * (1 - DEGRADAZIONE_ANNUA)
    cum, out = 0.0, []
    for anno in range(ORIZZONTE_ANNI):
        cum += risparmio_anno1 * (f ** anno)
        out.append(cum - costo_netto)
    return out


def anno_pareggio(gains):
    """Anno (float) in cui la curva cumulata attraversa lo zero. None se non attraversa."""
    if not gains:
        return None
    for i in range(1, len(gains)):
        if gains[i - 1] < 0 <= gains[i]:
            frac = (0 - gains[i - 1]) / (gains[i] - gains[i - 1])
            return i + frac  # gains[i-1]=anno i, gains[i]=anno i+1
    return None


# --------------------------------------------------------------------------- #
# Immagine satellitare del tetto (Google Static Maps) + cache
# --------------------------------------------------------------------------- #
def scarica_tetto(lat, lng, cid):
    """Ritorna il percorso PNG del tetto (da cache o scaricato), oppure None."""
    if not cid:
        return None
    os.makedirs(TETTI_DIR, exist_ok=True)
    path = os.path.join(TETTI_DIR, f"{cid}.png")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    key = os.environ.get("GOOGLE_MAPS_KEY")
    if not key or lat is None or lng is None:
        return None
    params = urllib.parse.urlencode({
        "center": f"{lat},{lng}",
        "zoom": SAT_ZOOM,
        "size": SAT_SIZE,
        "maptype": "satellite",
        "key": key,
    })
    url = "https://maps.googleapis.com/maps/api/staticmap?" + params
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            if resp.status != 200:
                return None
            if not resp.headers.get("Content-Type", "").startswith("image"):
                return None
            data = resp.read()
        if not data:
            return None
        with open(path, "wb") as f:
            f.write(data)
        return path
    except Exception as e:
        sys.stderr.write(f"[avviso] immagine tetto non scaricata ({e}).\n")
        return None


# --------------------------------------------------------------------------- #
# Logo
# --------------------------------------------------------------------------- #
def disegna_logo(c, x, y, altezza):
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
    except Exception as e:
        from reportlab.lib import colors
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 18)
        c.drawString(x, y + altezza / 2 - 6, "SGR")
        c.setFont("Helvetica", 8)
        c.drawString(x, y - 2, "Efficienza Energetica")
        sys.stderr.write(f"[avviso] logo SVG non reso ({e}); uso testo di ripiego.\n")


# --------------------------------------------------------------------------- #
# Pezzi grafici
# --------------------------------------------------------------------------- #
def img_o_placeholder(c, path, x, y, w, h, caption):
    from reportlab.lib.utils import ImageReader
    r = 8
    if path:
        try:
            c.saveState()
            clip = c.beginPath()
            clip.roundRect(x, y, w, h, r)
            c.clipPath(clip, stroke=0, fill=0)
            c.drawImage(ImageReader(path), x, y, w, h,
                        preserveAspectRatio=True, anchor="c", mask="auto")
            c.restoreState()
        except Exception:
            path = None
    if not path:
        c.setFillColor(_hex("#E9EDF0"))
        c.roundRect(x, y, w, h, r, stroke=0, fill=1)
        c.setFillColor(_hex("#9AA6B0"))
        c.setFont("Helvetica", 9)
        c.drawCentredString(x + w / 2, y + h / 2 + 4, "Anteprima satellitare")
        c.drawCentredString(x + w / 2, y + h / 2 - 8, "non disponibile")
    c.setStrokeColor(_hex(BRAND))
    c.setLineWidth(1)
    c.roundRect(x, y, w, h, r, stroke=1, fill=0)
    if caption:
        c.setFillColor(_hex(MUTE))
        c.setFont("Helvetica", 8)
        c.drawString(x + 2, y - 11, caption)


def disegna_grafico(c, x, y, w, h, gains):
    from reportlab.lib import colors  # noqa: F401
    az, green, red = _hex(BRAND), _hex(GREEN), _hex(RED)

    lo = min(0.0, min(gains))
    hi = max(0.0, max(gains))
    pad = 0.10 * ((hi - lo) or 1.0)
    vmin, vmax = lo - pad, hi + pad
    span = (vmax - vmin) or 1.0
    n = len(gains)

    def px(i):
        return x + (w * i / (n - 1) if n > 1 else 0)

    def py(v):
        return y + (v - vmin) / span * h

    zy = py(0.0)
    pts = [(px(i), py(g)) for i, g in enumerate(gains)]

    def poligono():
        p = c.beginPath()
        p.moveTo(pts[0][0], zy)
        for px_, py_ in pts:
            p.lineTo(px_, py_)
        p.lineTo(pts[-1][0], zy)
        p.close()
        return p

    # area verde sopra lo zero
    c.saveState()
    cp = c.beginPath()
    cp.rect(x, zy, w, (y + h) - zy)
    c.clipPath(cp, stroke=0, fill=0)
    c.setFillColor(_hex(GREEN_LIGHT))
    c.drawPath(poligono(), stroke=0, fill=1)
    c.restoreState()

    # area rossa sotto lo zero
    c.saveState()
    cp = c.beginPath()
    cp.rect(x, y, w, zy - y)
    c.clipPath(cp, stroke=0, fill=0)
    c.setFillColor(_hex(RED_LIGHT))
    c.drawPath(poligono(), stroke=0, fill=1)
    c.restoreState()

    # linea zero
    c.setStrokeColor(_hex("#AAB4BC"))
    c.setLineWidth(0.6)
    c.setDash(2, 2)
    c.line(x, zy, x + w, zy)
    c.setDash()

    # curva
    line = c.beginPath()
    line.moveTo(*pts[0])
    for pt in pts[1:]:
        line.lineTo(*pt)
    c.setStrokeColor(az)
    c.setLineWidth(2)
    c.drawPath(line, stroke=1, fill=0)

    # marcatori inizio/fine
    c.setFillColor(red)
    c.circle(pts[0][0], pts[0][1], 2.6, stroke=0, fill=1)
    c.setFillColor(green)
    c.circle(pts[-1][0], pts[-1][1], 2.6, stroke=0, fill=1)

    # break-even
    be = anno_pareggio(gains)
    if be is not None:
        t = (be - 1) / (n - 1)
        bx = x + w * t
        c.setStrokeColor(_hex(BRAND_DARK))
        c.setLineWidth(0.8)
        c.setDash(1, 2)
        c.line(bx, y, bx, y + h)
        c.setDash()
        c.setFillColor(_hex(BRAND_DARK))
        c.circle(bx, zy, 3, stroke=0, fill=1)
        et = f"Pareggio anno {it_num(be, 1)}"
        c.setFont("Helvetica-Bold", 8)
        tw = c.stringWidth(et, "Helvetica-Bold", 8)
        ex = min(max(bx - tw / 2, x), x + w - tw)
        c.drawString(ex, zy + 6, et)

    # etichette valori inizio/fine (sopra i punti, per non toccare l'asse)
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(red)
    c.drawString(x + 4, pts[0][1] + 6, it_eur(gains[0]))
    c.setFillColor(green)
    c.drawRightString(x + w, pts[-1][1] + 6, it_eur(gains[-1]))

    # asse x
    c.setFont("Helvetica", 7)
    c.setFillColor(_hex(MUTE))
    c.drawString(x, y - 10, "Oggi")
    c.drawRightString(x + w, y - 10, f"Anno {n}")


# --------------------------------------------------------------------------- #
# Costruzione PDF
# --------------------------------------------------------------------------- #
def genera_pdf(row, percorso):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.pdfgen import canvas

    W, H = A4
    M = 40
    CW = W - 2 * M
    c = canvas.Canvas(percorso, pagesize=A4)
    g = row.get

    nome = (g("NOME") or "n/d").strip() or "n/d"
    comune = (g("COMUNE") or "").strip()
    cid = (g("ID_CLIENTE") or "cliente").strip() or "cliente"
    kwp = num(g("KWP_CONSIGLIATO"))
    npan = num(g("N_PANNELLI"))
    prod = num(g("PRODUZIONE_KWH_ANNO"))
    consumo = num(g("CONSUMO_KWH_ANNO"))
    detr = num(g("DETRAZIONE"))
    netto = num(g("COSTO_NETTO"))
    risp1 = num(g("RISPARMIO_ANNUO_ANNO1"))
    payback = num(g("PAYBACK_ANNI"))
    guad20 = num(g("GUADAGNO_NETTO_20ANNI"))
    lat, lng = num(g("LAT")), num(g("LNG"))

    # ---- TESTATA ----
    band_h = 72
    c.setFillColor(_hex(BRAND))
    c.rect(0, H - band_h, W, band_h, stroke=0, fill=1)
    disegna_logo(c, M, H - band_h + (band_h - 28) / 2, 28)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 15)
    c.drawRightString(W - M, H - 30, "Preventivo impianto fotovoltaico")
    c.setFont("Helvetica", 9)
    c.drawRightString(W - M, H - 46, "SGR Efficienza Energetica")

    y = H - band_h - 18

    # ---- NUMERO EROE ----
    hero_h = 96
    hero_y = y - hero_h
    c.setFillColor(_hex(BRAND))
    c.roundRect(M, hero_y, CW, hero_h, 10, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(M + 20, hero_y + hero_h - 22, "IL TUO INVESTIMENTO IN FOTOVOLTAICO")
    if payback is not None:
        c.setFont("Helvetica-Bold", 30)
        c.drawString(M + 20, hero_y + hero_h - 56, f"Rientri in {it_num(payback, 1)} anni")
    else:
        c.setFont("Helvetica-Bold", 26)
        c.drawString(M + 20, hero_y + hero_h - 54, "Investimento fotovoltaico")
    if guad20 is not None:
        c.setFont("Helvetica-Bold", 20)
        c.drawString(M + 20, hero_y + hero_h - 82, f"e in 20 anni guadagni {it_eur(guad20)}")
    y = hero_y - 24

    # ---- BANDA: confronto spesa (sx) + tetto (dx) ----
    band2_h = 168
    band2_y = y - band2_h
    col_gap = 18
    left_w = (CW - col_gap) * 0.46
    right_x = M + left_w + col_gap
    right_w = W - M - right_x

    c.setFillColor(_hex(INK))
    c.setFont("Helvetica-Bold", 12)
    c.drawString(M, y - 4, "La tua bolletta")

    # spesa oggi: nessuna colonna dedicata nel CSV -> stima da consumo * prezzo (config)
    spesa_oggi = consumo * PREZZO_ELETTRICITA if consumo is not None else None
    spesa_dopo = max(0.0, spesa_oggi - risp1) if (spesa_oggi is not None and risp1 is not None) else None

    card_h = 58
    gap = 26
    c0_y = y - 26 - card_h
    c1_y = c0_y - gap - card_h

    def card_spesa(cy, etichetta, valore, suffix, bg, fg):
        c.setFillColor(_hex(bg))
        c.roundRect(M, cy, left_w, card_h, 8, stroke=0, fill=1)
        c.setFillColor(_hex(MUTE))
        c.setFont("Helvetica", 9)
        c.drawString(M + 12, cy + card_h - 16, etichetta)
        c.setFillColor(_hex(fg))
        c.setFont("Helvetica-Bold", 20)
        c.drawString(M + 12, cy + 10, valore)
        if suffix:
            vw = c.stringWidth(valore, "Helvetica-Bold", 20)
            c.setFont("Helvetica", 8)
            c.setFillColor(_hex(MUTE))
            c.drawString(M + 12 + vw + 6, cy + 14, suffix)

    card_spesa(c0_y, "Spesa energia oggi",
               it_eur(spesa_oggi) + ("/anno" if spesa_oggi is not None else ""),
               "(stima)" if spesa_oggi is not None else "", RED_LIGHT, RED)
    card_spesa(c1_y, "Spesa con il fotovoltaico",
               it_eur(spesa_dopo) + ("/anno" if spesa_dopo is not None else ""),
               "", GREEN_LIGHT, GREEN)

    # badge risparmio % tra le due card
    if spesa_oggi and risp1 is not None and spesa_oggi > 0:
        pct = min(100, round(100 * risp1 / spesa_oggi))
        bw = 96
        by = (c0_y + c1_y + card_h) / 2 - 9
        c.setFillColor(_hex(GREEN))
        c.roundRect(M + left_w - bw, by, bw, 18, 9, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(M + left_w - bw / 2, by + 5, f"-{pct}% in bolletta")

    # immagine tetto a destra
    img_caption = "Il tuo tetto" + (f" - {comune}" if comune else "")
    tetto_path = scarica_tetto(lat, lng, cid)
    img_o_placeholder(c, tetto_path, right_x, band2_y + 14, right_w, band2_h - 14, img_caption)

    y = band2_y - 26

    # ---- IMPIANTO PROPOSTO (riga compatta) ----
    c.setFillColor(_hex(INK))
    c.setFont("Helvetica-Bold", 12)
    c.drawString(M, y, "Impianto proposto")
    y -= 16
    pezzi = []
    pezzi.append(f"{it_num(kwp, 1)} kWp" if kwp is not None else "potenza n/d")
    pezzi.append(f"{it_num(npan, 0)} pannelli {MODELLO_PANNELLO}" if npan is not None else MODELLO_PANNELLO)
    pezzi.append(f"{it_num(prod, 0)} kWh/anno stimati" if prod is not None else "produzione n/d")
    c.setFont("Helvetica", 10)
    c.setFillColor(_hex(MUTE))
    c.drawString(M, y, "   •   ".join(pezzi))
    y -= 22

    # ---- GRAFICO ----
    c.setFillColor(_hex(INK))
    c.setFont("Helvetica-Bold", 12)
    c.drawString(M, y, "Il tuo guadagno nel tempo")
    gains = curva_guadagno_cumulato(risp1, netto)
    ch_h = 200
    ch_y = y - 18 - ch_h
    if gains:
        disegna_grafico(c, M, ch_y, CW, ch_h, gains)
    else:
        c.setFillColor(_hex(MUTE))
        c.setFont("Helvetica-Oblique", 9)
        c.drawString(M, y - 26, "Dati insufficienti per il grafico.")
    y = ch_y - 56

    # ---- FASCIA FIDUCIA ----
    trust = []
    if GARANZIA_ANNI is not None:
        trust.append((f"{it_num(GARANZIA_ANNI, 0)} anni", "garanzia di potenza"))
    trust.append(("Trina Vertex S+", "moduli ad alta efficienza"))
    if detr is not None:
        trust.append(("50%", "detrazione fiscale"))
    if trust:
        tw = CW / len(trust)
        ty = max(y, 84)
        c.setStrokeColor(_hex(HAIR))
        c.setLineWidth(0.6)
        c.line(M, ty + 24, W - M, ty + 24)
        for i, (big, small) in enumerate(trust):
            cx = M + tw * i + tw / 2
            c.setFillColor(_hex(BRAND))
            c.setFont("Helvetica-Bold", 14)
            c.drawCentredString(cx, ty + 4, big)
            c.setFillColor(_hex(MUTE))
            c.setFont("Helvetica", 8)
            c.drawCentredString(cx, ty - 8, small)

    # ---- DISCLAIMER ----
    c.setStrokeColor(_hex(HAIR))
    c.setLineWidth(0.5)
    c.line(M, 52, W - M, 52)
    c.setFillColor(_hex(MUTE))
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(M, 40, DISCLAIMER)
    c.setFont("Helvetica", 8)
    c.drawString(M, 27, "SGR Efficienza Energetica")
    c.drawRightString(W - M, 27, f"Preventivo per {nome} - cod. {cid}")

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
        print(f"ID {row.get('ID_CLIENTE')}: cliente AZIENDA. Il PDF e' previsto solo per i PRIVATI.")
        return
    if not e_privato(row.get("SEGMENTO")):
        print(f"ID {row.get('ID_CLIENTE')}: segmento '{row.get('SEGMENTO')}' non gestito (PDF solo privati).")
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
          f"payback {it_num(num(row.get('PAYBACK_ANNI')), 1)} anni, "
          f"guadagno 20a {it_eur(num(row.get('GUADAGNO_NETTO_20ANNI')))}")


if __name__ == "__main__":
    main()
