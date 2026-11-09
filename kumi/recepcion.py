"""Recepción de Kumi: mira las bases de Notion de tareas e ideas y mete en Kumi lo que toca.

Se lanza cada hora desde el programador del sistema (`kumi recepcion`). Si la tarea o la idea es de un repo,
entra en la megafactoría (encargado, factoría, tests, puerta, despliegue); lo que no es de código (posts,
investigación…) lo hace la oficina con un agente suelto.

Base de tareas (propiedades: Estado de tipo status, «humano» de tipo select con «sí»/«no»):
  - Se hacen las tareas con humano = «no» que estén «Sin empezar» o «En progreso». Una por pasada: la más
    antigua. Al cogerla pasa a «En progreso»; al acabar queda «Listo» o sigue «En progreso», y el resumen se
    añade al final de la página. Las que no tienen humano = «no» se anotan en el registro como saltadas.
  - Una tarea ya intentada no se repite hasta que el humano la edite o pasen 24 h.
  - Las tareas «Listo» con más de una semana sin tocar van a la papelera de Notion (30 días para recuperar).
Base de ideas (además, «Prioridad» Alta/Media/Baja y «Aplicación»):
  - Una por pasada: prioridad Alta, Media, Baja; a igualdad, la más antigua. Se implementa en una rama y NO
    se publica sin el humano: pasa siempre por PR humana. La idea queda «En progreso» hasta que la pase a
    «Listo». Ya hecha, no se vuelve a tocar salvo que se edite la página; a medias, una vez al día.
  - El humano contesta en la página con «Respuesta: …»: «sí / publica / adelante» aprueba la PR y publica;
    cualquier otra respuesta son cambios pedidos (tarea nueva con su texto).

    kumi recepcion            # una pasada
    kumi recepcion --simular  # dice qué haría y qué borraría, sin tocar nada
    kumi recepcion --token    # guarda el token de la integración de Notion
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from . import nucleo as kumi  # noqa: E402
from .config import CFG  # noqa: E402

DATOS = CFG.datos / "recepcion"
TOKEN = DATOS / "notion_token"
ESTADO = DATOS / "recepcion.json"
LOG = DATOS / "recepcion.log"

from . import motores  # noqa: E402

# Canal Notion de kumi.toml: [canales.notion] tareas = "<data source>", ideas = "<data source>", [canales.notion.apps]
NOTION = CFG.canales.get("notion", {})
BD_TAREAS = NOTION.get("tareas")      # «Tareas»
BD_IDEAS = NOTION.get("ideas")        # «Ideas de mejora»
H = CFG.humano
PENDIENTES = ("Sin empezar", "En progreso")
ORDEN_PRIORIDAD = {"Alta": 0, "Media": 1, "Baja": 2}
CADUCA = timedelta(days=7)
REINTENTO = timedelta(hours=24)
TIEMPO_OFICINA = 2 * 3600
NUEVO = "<proyecto nuevo>"          # proyecto_de(): la tarea pide crear un proyecto que aún no existe
APRUEBA = re.compile(r"^\s*(s[ií]|vale|ok|adelante|publ[ií]ca(la|lo)?|apruebo|aprobad[ao]|dale)\b", re.I)

# Dónde vive cada app de «Ideas de mejora» (propiedad «Aplicación» → carpeta; si falta, lo decide Kumi).
APPS = dict(NOTION.get("apps", {}))
# Apps con versión de escritorio y de Android: si la idea habla de Android, va a la segunda carpeta.
APPS_ANDROID = dict(NOTION.get("apps_android", {}))


def log(msg: str) -> None:
    linea = f"{datetime.now():%Y-%m-%d %H:%M} {msg}"
    print(linea, flush=True)
    DATOS.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(linea + "\n")


def estado() -> dict:
    try:
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"tareas": {}, "ideas": {}}


def guardar(e: dict) -> None:
    DATOS.mkdir(parents=True, exist_ok=True)
    ESTADO.write_text(json.dumps(e, ensure_ascii=False, indent=2), encoding="utf-8")


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def fecha(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


# --- Notion ----------------------------------------------------------------------------

def token() -> str:
    if os.environ.get("NOTION_TOKEN"):
        return os.environ["NOTION_TOKEN"]
    if TOKEN.exists():
        return TOKEN.read_text(encoding="utf-8").strip()
    raise SystemExit("falta el token de Notion: kumi recepcion --token (o la variable NOTION_TOKEN)")


def _notion(metodo: str, ruta: str, cuerpo: dict | None = None) -> dict:
    r = httpx.request(metodo, f"https://api.notion.com/v1/{ruta}", json=cuerpo, timeout=30, headers={
        "Authorization": f"Bearer {token()}", "Notion-Version": "2025-09-03"})
    r.raise_for_status()
    return r.json()


def _texto(rich: list) -> str:
    return "".join(t.get("plain_text", "") for t in rich).strip()


def _sel(pr: dict, nombre: str) -> str | None:
    return ((pr.get(nombre) or {}).get("select") or {}).get("name")


def leer_bd(bd: str, tipo: str) -> list[dict]:
    res, cursor = [], None
    while True:
        d = _notion("POST", f"data_sources/{bd}/query", {"page_size": 100, **({"start_cursor": cursor} if cursor else {})})
        for p in d["results"]:
            pr = p["properties"]
            res.append({"tipo": tipo, "id": p["id"], "url": p["url"], "creada": p["created_time"],
                        "editada": p["last_edited_time"], "titulo": _texto(pr["Descripción"]["title"]),
                        "estado": (pr["Estado"]["status"] or {}).get("name"), "humano": _sel(pr, "humano"),
                        "categoria": _sel(pr, "Categoría"), "app": _sel(pr, "Aplicación"), "prioridad": _sel(pr, "Prioridad")})
        if not d.get("has_more"):
            break
        cursor = d["next_cursor"]
    if tipo == "ideas":
        return sorted(res, key=lambda t: (ORDEN_PRIORIDAD.get(t["prioridad"], 3), t["creada"]))
    return sorted(res, key=lambda t: t["creada"])


def contenido(bloque: str, nivel: int = 0) -> str:
    lineas, cursor = [], None
    while True:
        d = _notion("GET", f"blocks/{bloque}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else ""))
        for b in d["results"]:
            datos = b.get(b["type"], {})
            texto = _texto(datos.get("rich_text", []))
            if b["type"] == "to_do":
                texto = f"[{'x' if datos.get('checked') else ' '}] {texto}"
            elif b["type"] == "child_page":
                texto = f"(subpágina: {datos.get('title', '')})"
            if texto:
                lineas.append("  " * nivel + texto)
            if b.get("has_children") and b["type"] not in ("child_page", "child_database"):
                lineas.append(contenido(b["id"], nivel + 1))
        if not d.get("has_more"):
            return "\n".join(x for x in lineas if x)
        cursor = d["next_cursor"]


def poner_estado(pagina: str, valor: str) -> None:
    _notion("PATCH", f"pages/{pagina}", {"properties": {"Estado": {"status": {"name": valor}}}})


def anotar(pagina: str, resumen: str) -> None:
    trozos = [resumen[i:i + 1900] for i in range(0, len(resumen), 1900)] or ["(sin resumen)"]
    bloques = [{"type": "heading_3", "heading_3": {"rich_text": [
        {"type": "text", "text": {"content": f"Kumi · {datetime.now():%d-%m-%Y %H:%M}"}}]}}]
    bloques += [{"type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": x}}]}} for x in trozos]
    _notion("PATCH", f"blocks/{pagina}/children", {"children": bloques})


def ultima_respuesta(cuerpo: str) -> str | None:
    """La última «Respuesta:» de el humano escrita después de la última nota del vigilante o de Kumi."""
    partes = re.split(r"(?m)^(?:Kumi|Vigilante) · \d\d-\d\d-\d{4} \d\d:\d\d\s*$", cuerpo)
    m = re.findall(r"(?mi)^\s*Respuesta:\s*(.+(?:\n(?!\s*(?:Kumi|Vigilante) · ).+)*)", partes[-1]) if len(partes) > 1 else []
    return m[-1].strip() if m else None


# --- Elegir ----------------------------------------------------------------------------

def elegir(todas: list[dict], intentos: dict) -> dict | None:
    for t in todas:
        if t["estado"] not in PENDIENTES or (t["tipo"] == "tareas" and t["humano"] != "no"):
            continue
        i = intentos.get(t["id"])
        if i and fecha(t["editada"]) <= fecha(i["editada"]):
            # el humano no la ha tocado desde la última vez.
            if t["tipo"] == "ideas" and i.get("resultado") in ("Hecho", "pr_humana", "publicada"):
                continue                     # idea ya implementada: espera a que él la revise o conteste
            if ahora() - fecha(i["cuando"]) < REINTENTO:
                continue
        return t
    return None


def limpiar(tareas: list[dict], simular: bool) -> None:
    for t in tareas:
        if t["estado"] == "Listo" and ahora() - fecha(t["editada"]) > CADUCA:
            log(f"{'borraría' if simular else 'a la papelera'}: {t['titulo'][:90]} (último update {fecha(t['editada']):%d-%m-%Y})")
            if not simular:
                _notion("PATCH", f"pages/{t['id']}", {"in_trash": True})


# --- ¿De qué repo es? ----------------------------------------------------------------------

def repos_conocidos() -> dict[str, str]:
    """Nombre → ruta de los repos del humano: tabla de <notas>/inventario.md cuya primera columna es la ruta
    absoluta del repo. Sin inventario, los repos con git que haya en la carpeta de proyectos."""
    out = {}
    from .config import nota_local
    inv = nota_local("inventario.md")
    if inv:
        for linea in inv.read_text(encoding="utf-8").splitlines():
            if linea.startswith("|"):
                ruta = linea.split("|")[1].strip().strip("`")
                if ruta and Path(ruta).is_absolute():
                    out[Path(ruta).name] = ruta
        return out
    if CFG.proyectos.is_dir():
        for d in sorted(CFG.proyectos.iterdir()):
            if (d / ".git").exists():
                out[d.name] = str(d)
    return out


def proyecto_de(t: dict, cuerpo: str) -> str | None:
    android = "android" in (t["titulo"] + cuerpo).lower()[:400]
    if t["tipo"] == "ideas" and t.get("app") in APPS_ANDROID and android:
        return APPS_ANDROID[t["app"]]
    if t["tipo"] == "ideas" and t.get("app") in APPS:
        return APPS[t["app"]]
    repos = repos_conocidos()
    lista = "\n".join(f"- {n}: {r}" for n, r in repos.items())
    texto = kumi.consultar(
        f"Eres la recepción de Kumi, la megafactoría de software de {H}. Decide si esta tarea de Notion es un cambio "
        "de código en UNO de sus repos (y en cuál) o si es otra cosa (un post, una investigación, un trámite, "
        "algo de varios repos a la vez o de un repo que no está en la lista).\n\n"
        f"Repos:\n{lista}\n\nTarea ({t['tipo']}, categoría {t.get('categoria') or t.get('app') or '-'}):\n{t['titulo']}\n\n"
        f"Página:\n{cuerpo[:3000] or '(vacía)'}\n\n"
        "Si lo que pide es CREAR un proyecto de software que aún no existe (una app, una web, una herramienta nueva), "
        "pon `nuevo: true` y `repo: null`.\n"
        'Responde SOLO con JSON: {"repo": "<ruta exacta de la lista>" o null, "nuevo": false, "por_que": "una frase"}',
        "recepcion")
    m = re.search(r"\{.*\}", texto, re.S)
    try:
        plan = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        plan = {}
    if plan.get("nuevo") and not plan.get("repo"):
        return NUEVO
    repo = plan.get("repo")
    return repo if repo in repos.values() else None


# --- Oficina: lo que no es de código, exactamente como lo hacía el vigilante de tareas ----------

def oficina(t: dict, cuerpo: str) -> tuple[str | None, str]:
    texto = (
        f"Eres el vigilante de tareas de {H}. Esta tarea está en su base «Tareas» de Notion "
        "marcada como humano = no: quiere que la hagas tú, sin preguntarle (nadie va a "
        "contestar; esto corre solo, sin nadie delante).\n"
        "Hazla de principio a fin. Antes de empezar, mira en tu memoria y en el contenido de "
        "la página si ya hay trabajo hecho o decisiones suyas que la condicionen, y respétalas. "
        "Si depende de algo de fuera que aún no ha pasado (una respuesta, un merge, una fecha) "
        "o de una decisión que solo puede tomar él, no la fuerces: haz lo que se pueda y "
        "explica qué falta.\n\n"
        f"TAREA ({t.get('categoria') or 'sin categoría'}, prioridad {t.get('prioridad') or '-'}, "
        f"ahora «{t['estado']}»):\n{t['titulo']}\n\n"
        f"CONTENIDO DE LA PÁGINA:\n{cuerpo or '(vacía)'}\n\n"
        "Cuando acabes, termina tu respuesta EXACTAMENTE con este formato:\n"
        "ESTADO: Listo        (o «ESTADO: En progreso» si queda algo pendiente)\n"
        "RESUMEN:\n<qué has hecho, dónde está (rutas, enlaces) y, si sigue en progreso, qué "
        "falta y por qué; en español y breve>")
    codigo, salida = motores.ejecutar_texto(CFG.elecciones("oficina"), texto, str(Path.home()),
                                            {**os.environ, "OMOIKANE_AUTHOR": "kumi-oficina"}, timeout=TIEMPO_OFICINA)
    salida = (salida or "").strip()
    m = re.search(r"^\s*ESTADO:\s*(Listo|En progreso)\s*$(.*)", salida, re.M | re.S | re.I)
    if codigo != 0 or not m:
        log(f"oficina ({codigo}) sin ESTADO: {salida[-500:]}")
        return None, salida
    valor = "Listo" if m.group(1).lower() == "listo" else "En progreso"
    return valor, re.sub(r"^\s*RESUMEN:\s*", "", m.group(2).strip(), flags=re.I)


# --- Megafactoría: lo que es de un repo -------------------------------------------------------

def resumen_kumi(t: dict) -> str:
    lineas = [f"Tarea de Kumi `{t['id']}` · estado: {t['estado']}" + (f" · {t.get('usd', 0)} $" if t.get("usd") else "")]
    for p in t.get("pasos", []):
        lineas.append(f"- {p['papel']} {p['nombre']}: {p.get('estado')} · {p.get('resumen', '')[:300]}")
    if t.get("estado") == "pr_humana":
        lineas.append("\nEspera tu revisión: " + "; ".join(t.get("motivos_pr", [])))
        lineas.append(f"Rama `{t.get('rama')}` en {t['raiz']}.")
        lineas.append("Para publicarla escribe al final de la página «Respuesta: sí». Para pedir cambios, «Respuesta: <qué cambiar>».")
    if t.get("version"):
        lineas.append(f"Publicada la versión {t['version']}.")
    if t.get("rollback"):
        lineas.append(f"Rollback: {t['rollback'].get('detalle')}")
    for h in t.get("hijas", []):
        lineas.append(f"Tarea nueva creada: {h}")
    return "\n".join(lineas)


def megafactoria(item: dict, proyecto: str, cuerpo: str, previa: dict | None) -> tuple[str, dict]:
    """Pasa el encargo por Kumi. Si ya hay una tarea suya esperando PR y el humano contestó, la resuelve."""
    respuesta = ultima_respuesta(cuerpo)
    if previa and previa.get("tarea_kumi") and respuesta:
        t = kumi.cargar(previa["tarea_kumi"])
        if t["estado"] == "pr_humana":
            if APRUEBA.match(respuesta):
                log(f"Respuesta de {H}: aprueba la PR de {t['id']}")
                subprocess.run([sys.executable, "-m", "kumi", "aprobar", t["id"]])
            else:
                log(f"Respuesta de {H}: pide cambios en {t['id']}")
                subprocess.run([sys.executable, "-m", "kumi", "rechazar", t["id"], respuesta])
            t = kumi.cargar(t["id"])
            final = kumi.cargar(t["hijas"][-1]) if t.get("hijas") else t
            return final["estado"], final
    texto = f"{item['titulo']}\n\n{cuerpo}".strip()
    origen = "notion-ideas" if item["tipo"] == "ideas" else "notion-tareas"
    if proyecto == NUEVO:
        # Un proyecto que aún no existe: la central lo crea y encarga su primera versión (PR humana siempre).
        t = kumi.cargar(kumi.crear_proyecto(texto, origen=origen)["id"])
        while t.get("hijas"):
            t = kumi.cargar(t["hijas"][-1])
        return t["estado"], t
    t = kumi.nueva_tarea(Path(proyecto), texto, origen)
    if item["tipo"] == "ideas":
        t["siempre_humano"] = f"es una idea de Notion: las ideas nunca se publican sin {H}"
        kumi.guardar(t)
    t = kumi.recorrer(t)
    if t["estado"] not in ("planificando", "esperando_aprobacion_cadena", "planificacion_incompleta"):
        kumi.aprender(t, item["titulo"])
    t = kumi.cargar(t["id"])
    while t.get("hijas"):                    # el supervisor o producción pueden haber seguido en una tarea hija
        t = kumi.cargar(t["hijas"][-1])
    return t["estado"], t


# --- Pasada -----------------------------------------------------------------------------

def atender(item: dict, e: dict, simular: bool) -> None:
    intentos = e.setdefault(item["tipo"], {})
    cuerpo = contenido(item["id"])
    proyecto = proyecto_de(item, cuerpo)
    destino = ("megafactoría (proyecto nuevo)" if proyecto == NUEVO else f"megafactoría ({proyecto})") if proyecto else "oficina"
    log(f"toca [{item['tipo']}]: {item['titulo'][:110]} ({item['estado']}) → {destino}")
    if simular:
        return
    if item["estado"] != "En progreso":
        poner_estado(item["id"], "En progreso")
    registro = {"cuando": ahora().isoformat(), "titulo": item["titulo"][:120]}
    if proyecto:
        est, t = megafactoria(item, proyecto, cuerpo, intentos.get(item["id"]))
        anotar(item["id"], resumen_kumi(t))
        registro.update(resultado=est, tarea_kumi=t["id"])
        # Tareas: «Listo» si se publicó. Ideas: se quedan «En progreso» hasta que el humano las pase a «Listo».
        if item["tipo"] == "tareas" and est == "publicada":
            poner_estado(item["id"], "Listo")
        log(f"→ Kumi {t['id']}: {est}")
    else:
        valor, resumen = oficina(item, cuerpo)
        if valor:
            anotar(item["id"], resumen)
            poner_estado(item["id"], valor)
            log(f"→ {valor}: {resumen[:300]}")
        registro["resultado"] = valor
    registro["editada"] = _notion("GET", f"pages/{item['id']}")["last_edited_time"]
    intentos[item["id"]] = registro
    if registro.get("resultado") == "Listo":
        intentos.pop(item["id"], None)
    guardar(e)


def anotar_saltadas(tareas: list[dict], e: dict, simular: bool) -> None:
    """Deja en el registro las tareas pendientes que no se van a hacer por el campo «humano», para que se vea
    por qué no entran. Cada una se anota una sola vez, y otra vez si el humano la edita y sigue sin entrar."""
    vistas = e.setdefault("saltadas", {})
    pendientes = set()
    for t in tareas:
        if t["estado"] not in PENDIENTES or t["humano"] == "no":
            continue
        pendientes.add(t["id"])
        if vistas.get(t["id"]) == t["editada"]:
            continue
        motivo = "humano = sí, la haces tú" if t["humano"] else "sin «humano»; para que la haga Kumi, pon humano = no"
        log(f"saltada ({motivo}): {t['titulo'][:90]}")
        vistas[t["id"]] = t["editada"]
    for i in set(vistas) - pendientes:
        del vistas[i]                          # ya no está pendiente (hecha, borrada o pasada a «no»)
    if not simular:
        guardar(e)


def pasada(simular: bool) -> None:
    if not BD_TAREAS and not BD_IDEAS:
        raise SystemExit("No hay canal Notion en kumi.toml ([canales.notion] tareas = … / ideas = …)")
    e = estado()
    tareas = leer_bd(BD_TAREAS, "tareas") if BD_TAREAS else []
    limpiar(tareas, simular)
    ideas = leer_bd(BD_IDEAS, "ideas") if BD_IDEAS else []
    anotar_saltadas(tareas, e, simular)
    hubo = False
    for todas, clave in ((tareas, "tareas"), (ideas, "ideas")):
        item = elegir(todas, e.get(clave, {}))
        if item:
            hubo = True
            atender(item, e, simular)
    if not hubo:
        log("nada que hacer")


def main(argv: list[str] | None = None) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="kumi recepcion", description="Recepción de Kumi: bases de tareas e ideas de Notion")
    ap.add_argument("--simular", action="store_true")
    ap.add_argument("--token", action="store_true")
    a = ap.parse_args(argv)
    if a.token:
        import getpass
        DATOS.mkdir(parents=True, exist_ok=True)
        TOKEN.write_text(getpass.getpass("Token de la integración de Notion (no se verá): ").strip(), encoding="utf-8")
        print("Guardado.")
        return
    try:
        pasada(a.simular)
    except Exception as ex:  # noqa: BLE001
        log(f"ERROR: {ex}")
        sys.exit(1)


if __name__ == "__main__":
    main()
