# -*- coding: utf-8 -*-
"""
pannello.py - Pannello web locale per modificare parametri.json.

Caratteristiche:
  - Gira SOLO in locale: ascolta su 127.0.0.1:8000 (mai esposto in rete).
  - I campi sono GENERATI DINAMICAMENTE leggendo parametri.json: ogni nuovo
    parametro aggiunto al JSON compare da solo, senza toccare questo file.
  - Gestisce valori semplici, dizionari annidati (es. privati.listino) e liste
    di oggetti (es. aziende.fasce, ibrido), in modo ricorsivo.
  - Mantiene ESATTAMENTE struttura, chiavi e ordine del JSON: modifica solo i
    valori delle foglie, non aggiunge/rimuove chiavi.
  - I valori null (es. prezzi fasce aziende) sono evidenziati come "da compilare"
    e restano salvabili.
  - Validazione robusta: accetta virgola o punto come separatore decimale, non va
    in crash se si scrive testo dove serve un numero, messaggi d'errore chiari.
  - Conserva i tipi: interi restano interi, decimali restano float, campo vuoto
    torna null. Le stringhe restano stringhe.
  - Backup automatico di parametri.json a ogni salvataggio (parametri.bak.json).

Solo libreria standard.  Avvio su Windows:  py pannello.py
Poi apri il browser su  http://127.0.0.1:8000
"""
import os
import json
import html
import shutil
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

QUI = os.path.dirname(os.path.abspath(__file__))
PARAMETRI_FILE = os.path.join(QUI, "parametri.json")
BACKUP_FILE = os.path.join(QUI, "parametri.bak.json")
HOST = "127.0.0.1"
PORT = 8000

# Ordine di visualizzazione delle sezioni. Le sezioni eventualmente presenti nel
# JSON ma non elencate qui vengono mostrate comunque, in coda (future-proof).
SECTION_ORDER = ["economici", "tecnici", "pannello", "detrazioni", "privati",
                 "accumulo", "aziende", "pompe_calore", "ibrido"]

SEP = "."  # separatore del percorso nei name dei campi (le chiavi non contengono punti)


# --------------------------------------------------------------------------- #
# Lettura / scrittura JSON
# --------------------------------------------------------------------------- #
def load():
    with open(PARAMETRI_FILE, encoding="utf-8") as f:
        return json.load(f)


def backup():
    """Copia rolling di parametri.json prima di sovrascriverlo."""
    if os.path.exists(PARAMETRI_FILE):
        shutil.copy2(PARAMETRI_FILE, BACKUP_FILE)


def save(params):
    """Scrittura atomica: prima su file temporaneo, poi replace."""
    tmp = PARAMETRI_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(params, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, PARAMETRI_FILE)


# --------------------------------------------------------------------------- #
# Navigazione / tipizzazione
# --------------------------------------------------------------------------- #
def iter_leaves(node, prefix=None):
    """Genera (tokens, valore) per ogni foglia scalare, saltando le chiavi '_*'."""
    prefix = prefix or []
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str) and k.startswith("_"):
                continue
            yield from iter_leaves(v, prefix + [str(k)])
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from iter_leaves(v, prefix + [str(i)])
    else:
        yield prefix, node


def set_path(container, tokens, value):
    cur = container
    for t in tokens[:-1]:
        cur = cur[int(t)] if isinstance(cur, list) else cur[t]
    last = tokens[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


def coerce(raw, original_value):
    """Converte il testo del form nel tipo corretto in base al valore originale.

    Regole:
      - stringa  -> resta stringa (il testo cosi' com'e', vuoto incluso)
      - intero   -> resta intero se possibile (float solo se l'utente scrive decimali)
      - float    -> resta float
      - null     -> vuoto resta null; altrimenti int se numero intero senza
                    separatore, altrimenti float
    Solleva ValueError (messaggio in italiano) se serve un numero e il testo non lo e'.
    """
    s = raw.strip()

    if isinstance(original_value, str):
        return s  # campo testuale: nessuna conversione

    # campo numerico oppure null
    if s == "":
        return None

    typed_decimal = ("," in s) or ("." in s)
    s_norm = s.replace(",", ".")
    try:
        f = float(s_norm)
    except ValueError:
        raise ValueError("non e' un numero valido")

    # bool (non presente nel JSON attuale, ma gestito per sicurezza)
    if isinstance(original_value, bool):
        return f != 0

    if isinstance(original_value, int):  # intero originale
        if f.is_integer():
            return int(f)
        return f  # l'utente ha inserito decimali: non li perdiamo

    if isinstance(original_value, float):  # decimale originale: resta float
        return f

    # original_value is None -> deduco dal testo digitato
    if f.is_integer() and not typed_decimal:
        return int(f)
    return f


# --------------------------------------------------------------------------- #
# Rendering HTML
# --------------------------------------------------------------------------- #
def esc(x):
    return html.escape(str(x), quote=True)


def humanize(key):
    s = str(key).replace("_", " ").strip()
    return (s[:1].upper() + s[1:]) if s else s


def fmt(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def render_field(tokens, value, label, errors, raw_overrides):
    name = SEP.join(tokens)
    is_null = value is None
    classes = ["field"]
    if is_null and name not in raw_overrides:
        classes.append("todo")
    err = errors.get(name)
    if err:
        classes.append("error")

    if name in raw_overrides:
        display = raw_overrides[name]
    elif value is None:
        display = ""
    else:
        display = fmt(value)

    badge = '<span class="badge">da compilare</span>' if is_null else ""
    errmsg = f'<span class="errmsg">{esc(err)}</span>' if err else ""
    placeholder = "da compilare" if is_null else ""
    return (
        f'<div class="{" ".join(classes)}">'
        f'<label for="{esc(name)}">{esc(label)} {badge}</label>'
        f'<input id="{esc(name)}" name="{esc(name)}" value="{esc(display)}" '
        f'placeholder="{esc(placeholder)}" autocomplete="off" spellcheck="false">'
        f'{errmsg}</div>'
    )


def render_value(tokens, value, label, errors, raw_overrides):
    """Rende ricorsivamente una foglia, un dizionario o una lista (annidati)."""
    if isinstance(value, dict):
        rows = "".join(
            render_value(tokens + [str(k)], v, humanize(k), errors, raw_overrides)
            for k, v in value.items() if not (isinstance(k, str) and k.startswith("_"))
        )
        return f'<fieldset><legend>{esc(label)}</legend>{rows}</fieldset>'
    if isinstance(value, list):
        rows = "".join(
            render_value(tokens + [str(i)], item, f"{label} - voce {i + 1}", errors, raw_overrides)
            for i, item in enumerate(value)
        )
        return f'<fieldset><legend>{esc(label)}</legend>{rows}</fieldset>'
    return render_field(tokens, value, label, errors, raw_overrides)


def render_container_body(tokens, value, errors, raw_overrides):
    if isinstance(value, dict):
        return "".join(
            render_value(tokens + [str(k)], v, humanize(k), errors, raw_overrides)
            for k, v in value.items() if not (isinstance(k, str) and k.startswith("_"))
        )
    if isinstance(value, list):
        label = humanize(tokens[-1])
        return "".join(
            render_value(tokens + [str(i)], item, f"{label} - voce {i + 1}", errors, raw_overrides)
            for i, item in enumerate(value)
        )
    return render_field(tokens, value, humanize(tokens[-1]), errors, raw_overrides)


def render_sections(params, errors, raw_overrides):
    keys = [k for k in params if not (isinstance(k, str) and k.startswith("_"))]
    ordered = [k for k in SECTION_ORDER if k in keys] + [k for k in keys if k not in SECTION_ORDER]
    out = []
    for k in ordered:
        body = render_container_body([k], params[k], errors, raw_overrides)
        out.append(f'<section class="card"><h2>{esc(humanize(k))}</h2>{body}</section>')
    return "".join(out)


HTML_HEAD = """<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pannello parametri FV</title>
<style>
body{font-family:system-ui,Arial,sans-serif;background:#f4f6f8;color:#222;margin:0}
.wrap{max-width:920px;margin:0 auto;padding:24px}
h1{font-size:22px;margin:0 0 4px}
.note{color:#666;font-size:13px;margin:0 0 8px}
.card{background:#fff;border:1px solid #dde;border-radius:8px;padding:16px;margin:16px 0}
.card h2{margin:0 0 12px;font-size:18px;border-bottom:1px solid #eee;padding-bottom:6px}
fieldset{border:1px solid #e3e6ea;border-radius:6px;margin:10px 0;padding:10px 12px}
legend{font-weight:600;font-size:14px;color:#345;padding:0 6px}
.field{display:flex;align-items:center;gap:10px;margin:6px 0;flex-wrap:wrap}
.field label{flex:0 0 320px;font-size:14px}
.field input{flex:1;min-width:160px;padding:6px 8px;border:1px solid #ccd;border-radius:5px;font-size:14px}
.field.todo input{background:#fff8e1;border-color:#f0c040}
.field.error input{border-color:#e03030;background:#fff0f0}
.badge{background:#f0c040;color:#5a4500;font-size:11px;padding:1px 6px;border-radius:10px;margin-left:6px}
.errmsg{color:#c00;font-size:12px;flex:0 0 100%}
.msg{padding:10px 14px;border-radius:6px;margin:12px 0;font-size:14px}
.msg.ok{background:#e7f6e7;border:1px solid #7bc47b}
.msg.error{background:#fdeaea;border:1px solid #e08080}
.actions{position:sticky;bottom:0;background:#f4f6f8;padding:14px 0}
button{background:#1a8a5a;color:#fff;border:0;padding:10px 22px;border-radius:6px;font-size:15px;cursor:pointer}
button:hover{background:#15724a}
</style></head><body><div class="wrap">
"""

HTML_FOOT = "</div></body></html>"


def render_page(params, message=None, errors=None, raw_overrides=None):
    errors = errors or {}
    raw_overrides = raw_overrides or {}
    msg_html = ""
    if message:
        kind, text = message
        msg_html = f'<div class="msg {esc(kind)}">{esc(text)}</div>'
    sections = render_sections(params, errors, raw_overrides)
    return (
        HTML_HEAD
        + "<h1>Pannello parametri FV</h1>"
        + f'<p class="note">In ascolto solo su {HOST}:{PORT} - non esposto in rete. '
          "I dati cliente non passano da qui: si modificano solo prezzi e parametri commerciali.</p>"
        + msg_html
        + '<form method="post" action="/salva">'
        + sections
        + '<div class="actions"><button type="submit">Salva parametri</button></div>'
        + "</form>"
        + HTML_FOOT
    )


def render_error_page(text):
    return (HTML_HEAD + "<h1>Pannello parametri FV</h1>"
            + f'<div class="msg error">{esc(text)}</div>' + HTML_FOOT)


# --------------------------------------------------------------------------- #
# Salvataggio
# --------------------------------------------------------------------------- #
def handle_save(form):
    """Ritorna l'HTML da mostrare dopo un POST. Non solleva: gli errori finiscono a video."""
    params = load()
    working = json.loads(json.dumps(params))  # deep copy preservando ordine
    errors = {}

    for tokens, original_value in iter_leaves(params):
        name = SEP.join(tokens)
        if name not in form:
            continue  # campo non inviato: lascio il valore originale
        try:
            new_value = coerce(form[name], original_value)
        except ValueError as e:
            errors[name] = f'"{form[name]}": {e}'
            continue
        set_path(working, tokens, new_value)

    if errors:
        raw_overrides = {n: form[n] for n in errors}
        return render_page(
            working,
            message=("error", f"Salvataggio annullato: correggi i {len(errors)} campo/i evidenziati."),
            errors=errors,
            raw_overrides=raw_overrides,
        )

    backup()
    save(working)
    return render_page(
        load(),
        message=("ok", "Parametri salvati correttamente. Backup creato in parametri.bak.json."),
    )


# --------------------------------------------------------------------------- #
# Server HTTP
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    def _send_html(self, html_text, code=200):
        data = html_text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index", "/index.html"):
            try:
                self._send_html(render_page(load()))
            except Exception as e:
                self._send_html(render_error_page(f"Impossibile leggere parametri.json: {e}"), 500)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/salva":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            form = {k: v[-1] for k, v in
                    urllib.parse.parse_qs(body, keep_blank_values=True).items()}
            try:
                self._send_html(handle_save(form))
            except Exception as e:
                self._send_html(render_error_page(f"Errore durante il salvataggio: {e}"), 500)
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass  # niente log di accesso sulla console


def main():
    server = HTTPServer((HOST, PORT), Handler)
    print(f"Pannello parametri attivo su http://{HOST}:{PORT}  (solo locale, non esposto in rete)")
    print("Apri quell'indirizzo nel browser. Premi CTRL+C per fermare.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nChiuso.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
