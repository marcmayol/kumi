"""Canales de Kumi: por dónde entra el trabajo y por dónde habla con las personas.

Un canal sirve para cuatro cosas: entrar tareas, avisar, preguntar y pedir la aprobación de una PR humana.
En `kumi.toml`, `[enrutado]` dice por qué canales va cada tipo de mensaje:

    aviso      publicado, parado, rollback…
    pr_humana  algo listo para producción que espera un sí
    pregunta   una decisión que solo puede tomar una persona (propuestas de regla, choques…)
    cliente    el aviso al cliente, si el proyecto lo pide

Canales de serie (sin dependencias):
    escritorio  notificación del sistema (Windows, macOS, Linux)                      solo avisa
    telegram    bot: avisa, con botones Aprobar/Rechazar; `kumi escuchar` recibe   ida y vuelta
    discord     webhook de un canal                                                    solo avisa
    ntfy        un tema de ntfy.sh o de un servidor propio                             solo avisa
    correo      SMTP                                                                   solo avisa
    notion      bases de datos de tareas e ideas (recepcion.py)                        ida y vuelta

Seguridad: un mensaje que entra por un canal es un dato, no una orden. Aprobar o rechazar solo vale si
viene de un id que esté en `aprobadores` de ese canal.
"""
from __future__ import annotations

import json
import os
import smtplib
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage

from .config import CFG


def _secreto(c: dict, clave: str) -> str:
    """Los secretos no van en el toml: el toml dice en qué variable de entorno están (`<clave>_env`)."""
    if c.get(f"{clave}_env"):
        return os.environ.get(c[f"{clave}_env"], "")
    return str(c.get(clave, ""))


def _http(url: str, datos: dict | bytes | None = None, cabeceras: dict | None = None, timeout: int = 40) -> dict:
    cuerpo = datos if isinstance(datos, bytes) or datos is None else json.dumps(datos).encode()
    req = urllib.request.Request(url, data=cuerpo, method="POST" if cuerpo is not None else "GET",
                                 headers={"content-type": "application/json", **(cabeceras or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        texto = r.read().decode("utf-8", errors="replace")
    try:
        return json.loads(texto) if texto else {}
    except json.JSONDecodeError:
        return {"texto": texto}


# --- Envío ----------------------------------------------------------------------------------

def escritorio(c: dict, asunto: str, cuerpo: str, **_) -> None:
    titulo = f"Kumi: {asunto[:80]}"
    if sys.platform == "win32":
        t = titulo.replace("'", "''")
        ps = ("[void][Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime];"
              "$x=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(1);"
              f"$x.GetElementsByTagName('text')[0].AppendChild($x.CreateTextNode('{t}'))|Out-Null;"
              "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Kumi').Show([Windows.UI.Notifications.ToastNotification]::new($x))")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
    elif sys.platform == "darwin":
        subprocess.run(["osascript", "-e", f"display notification {json.dumps(cuerpo[:200])} with title {json.dumps(titulo)}"],
                       capture_output=True)
    else:
        subprocess.run(["notify-send", titulo, cuerpo[:300]], capture_output=True)


def telegram(c: dict, asunto: str, cuerpo: str, tipo: str = "aviso", tarea: str | None = None, **_) -> None:
    token = _secreto(c, "token")
    if not token or not c.get("chat"):
        raise RuntimeError("telegram: falta `token_env` o `chat`")
    datos = {"chat_id": c["chat"], "text": f"*{asunto}*\n\n{cuerpo}"[:4000], "parse_mode": "Markdown",
             "disable_web_page_preview": True}
    if tipo == "pr_humana" and tarea:
        datos["reply_markup"] = {"inline_keyboard": [[
            {"text": "✅ Aprobar y publicar", "callback_data": f"aprobar:{tarea}"},
            {"text": "✏️ Pedir cambios", "callback_data": f"cambios:{tarea}"}]]}
    try:
        _http(f"https://api.telegram.org/bot{token}/sendMessage", datos)
    except urllib.error.HTTPError:
        datos.pop("parse_mode")                   # un * o _ suelto en el texto rompe el Markdown
        _http(f"https://api.telegram.org/bot{token}/sendMessage", datos)


def discord(c: dict, asunto: str, cuerpo: str, tipo: str = "aviso", tarea: str | None = None, **_) -> None:
    url = _secreto(c, "webhook")
    if not url:
        raise RuntimeError("discord: falta `webhook_env`")
    pie = (f"\n\nAprobar: `kumi aprobar {tarea}` · Cambios: `kumi rechazar {tarea} \"…\"`"
           if tipo == "pr_humana" and tarea else "")
    _http(url, {"username": "Kumi", "content": f"**{asunto}**\n{cuerpo}{pie}"[:1990]})


def ntfy(c: dict, asunto: str, cuerpo: str, tipo: str = "aviso", **_) -> None:
    url = f"{c.get('servidor', 'https://ntfy.sh').rstrip('/')}/{c['tema']}"
    cab = {"Title": f"Kumi: {asunto}"[:200].encode("ascii", "ignore").decode(), "content-type": "text/plain",
           **({"Priority": "high"} if tipo in ("pr_humana", "pregunta") else {})}
    tok = _secreto(c, "token")
    if tok:
        cab["Authorization"] = f"Bearer {tok}"
    _http(url, cuerpo.encode("utf-8"), cab)


def correo(c: dict, asunto: str, cuerpo: str, para: str | None = None, **_) -> None:
    m = EmailMessage()
    m["Subject"], m["From"], m["To"] = f"Kumi: {asunto}", c["de"], para or c["a"]
    m.set_content(cuerpo)
    with smtplib.SMTP(c["servidor"], int(c.get("puerto", 587)), timeout=30) as s:
        s.starttls()
        if c.get("usuario"):
            s.login(c["usuario"], _secreto(c, "clave"))
        s.send_message(m)


ENVIO = {"escritorio": escritorio, "telegram": telegram, "discord": discord, "ntfy": ntfy, "correo": correo}


def avisar(tipo: str, asunto: str, cuerpo: str, tarea: str | None = None, para: str | None = None) -> list[str]:
    """Manda el mensaje por los canales que `[enrutado]` asigna a su tipo. Devuelve los que fallaron."""
    fallos = []
    for nombre in CFG.enrutado.get(tipo, []):
        c = CFG.canales.get(nombre)
        clase = (c or {}).get("tipo", nombre)
        if c is None or clase not in ENVIO:
            fallos.append(f"{nombre}: canal no configurado")
            continue
        try:
            ENVIO[clase](c, asunto, cuerpo, tipo=tipo, tarea=tarea, para=para)
        except Exception as e:  # noqa: BLE001 — un canal caído no para la tarea
            fallos.append(f"{nombre}: {e}")
    if fallos:
        print("[kumi] avisos que no salieron: " + "; ".join(fallos), file=sys.stderr)
    return fallos


# --- Recepción por Telegram ------------------------------------------------------------------

def escuchar_telegram(manejar, una_vez: bool = False) -> None:
    """Long polling de un bot de Telegram (no necesita IP pública). `manejar(orden, args, responder)`.

    Órdenes que entiende (solo de los ids de `aprobadores`):
        botones Aprobar / Pedir cambios de una PR humana
        /aprobar <id>          /rechazar <id> <qué cambiar>
        /tarea <proyecto> <texto>       /estado
        respuesta (reply) a un mensaje de «Pedir cambios» con lo que hay que cambiar
    """
    nombre = next((n for n, c in CFG.canales.items() if c.get("tipo", n) == "telegram"), None)
    if not nombre:
        raise SystemExit("No hay ningún canal de Telegram en kumi.toml")
    c = CFG.canales[nombre]
    token = _secreto(c, "token")
    base = f"https://api.telegram.org/bot{token}"
    aprobadores = {str(x) for x in c.get("aprobadores", [])}
    desplazamiento = None
    pendiente_cambios: dict[str, str] = {}       # chat → tarea a la que se le piden cambios

    def responder(chat, texto):
        try:
            _http(f"{base}/sendMessage", {"chat_id": chat, "text": texto[:4000]})
        except Exception:  # noqa: BLE001
            pass

    while True:
        q = {"timeout": 50, **({"offset": desplazamiento} if desplazamiento else {})}
        try:
            d = _http(f"{base}/getUpdates?{urllib.parse.urlencode(q)}", timeout=70)
        except Exception as e:  # noqa: BLE001
            print(f"[kumi] telegram: {e}", file=sys.stderr)
            if una_vez:
                return
            continue
        for u in d.get("result", []):
            desplazamiento = u["update_id"] + 1
            if "callback_query" in u:
                cq = u["callback_query"]
                quien, chat = str(cq["from"]["id"]), cq["message"]["chat"]["id"]
                _http(f"{base}/answerCallbackQuery", {"callback_query_id": cq["id"]})
                if quien not in aprobadores:
                    responder(chat, "No estás en la lista de aprobadores de Kumi.")
                    continue
                accion, _, tid = cq.get("data", "").partition(":")
                if accion == "aprobar":
                    manejar("aprobar", [tid], lambda t, ch=chat: responder(ch, t))
                elif accion == "cambios":
                    pendiente_cambios[str(chat)] = tid
                    responder(chat, f"¿Qué hay que cambiar en {tid}? Contesta con el texto.")
                continue
            msg = u.get("message") or {}
            texto, chat = (msg.get("text") or "").strip(), msg.get("chat", {}).get("id")
            quien = str(msg.get("from", {}).get("id"))
            if not texto or chat is None:
                continue
            if quien not in aprobadores:
                responder(chat, "No estás en la lista de aprobadores de Kumi.")
                continue
            r = lambda t, ch=chat: responder(ch, t)  # noqa: E731
            if str(chat) in pendiente_cambios and not texto.startswith("/"):
                manejar("rechazar", [pendiente_cambios.pop(str(chat)), texto], r)
            elif texto.startswith("/aprobar"):
                manejar("aprobar", texto.split()[1:2], r)
            elif texto.startswith("/rechazar"):
                partes = texto.split(maxsplit=2)
                manejar("rechazar", partes[1:3], r)
            elif texto.startswith("/tarea"):
                partes = texto.split(maxsplit=2)
                manejar("tarea", partes[1:3], r)
            elif texto.startswith("/estado"):
                manejar("estado", [], r)
            else:
                responder(chat, "Órdenes: /tarea <proyecto> <texto> · /aprobar <id> · /rechazar <id> <qué cambiar> · /estado")
        if una_vez:
            return
