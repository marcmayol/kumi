"""Kumi (組): la central que recibe tareas y las pasa por cadenas de montaje.

    tarea → CENTRAL (encargado + planificador + registro)
          → CADENA DE MONTAJE (una factoría, o varias encadenadas si se pide)
          → CADENAS DE TEST (una o varias)
          → SUPERVISOR ──fallo──→ tarea nueva (origen: supervisor)
          → commit → PUERTA DE RIESGO ──nivel 2──→ PR humana (aprobar / rechazar → tarea nueva)
          → DESPLIEGUE → PROD TESTS ──fallo──→ tarea nueva (origen: produccion) + aviso
          → aviso al humano siempre; al cliente si la receta lo pide

Órdenes:
    kumi tarea --proyecto ~/proyectos/mi-app "texto" [--modo asdd] [--ruta android,seo-geo]
    kumi estado
    kumi ver <id>
    kumi aprobar <id>                 # PR humana: publica
    kumi rechazar <id> "qué cambiar"  # PR humana: tarea nueva con el comentario
    kumi aprobar-cadena <nombre>      # quita «provisional» a una cadena diseñada
    kumi registro
    kumi limpiar <id>                 # quita el worktree de una tarea ya cerrada
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import tomllib
from pathlib import Path

import psutil

from . import canales  # noqa: E402
from . import motores  # noqa: E402
from . import registro  # noqa: E402
from . import riesgo  # noqa: E402
from . import traza  # noqa: E402
from .config import CFG, catalogo, catalogo_glob, humanizar_texto, nota_local, recetas_dir  # noqa: E402

RAIZ = Path(__file__).resolve().parent          # catálogo de serie (viaja con el paquete)
DATOS = CFG.datos                               # lo de esta instalación: tareas, obras, cerrojos, cadenas propias
TAREAS = DATOS / "tareas"
OBRAS = DATOS / "obras"
CERROJOS = DATOS / "cerrojos"
RECETAS = recetas_dir()
GASTO = DATOS / "gasto.json"

MAX_VUELTAS_SUPERVISOR = CFG.max_vueltas_supervisor   # tareas encadenadas por el supervisor antes de pedir humano
ANDROID_SDK = CFG.android_sdk
AVD_TEST = os.environ.get("KUMI_AVD", CFG.avd_test)
EXE = ".exe" if sys.platform == "win32" else ""


# --- Utilidades ----------------------------------------------------------------------

def ahora() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def leer(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def escribir(p: Path, datos: dict) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def git(*args: str, cwd: Path, ok: bool = False) -> str | None:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        if ok:
            return None
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


def log(t: dict, texto: str) -> None:
    linea = f"{ahora()} {texto}"
    with CANDADO:
        t.setdefault("historia", []).append(linea)
        guardar(t)
    print(f"[kumi {t['id']}] {texto}", file=sys.stderr, flush=True)


# Las cadenas de test de una tarea corren en hilos a la vez y comparten el dict de la tarea.
CANDADO = threading.RLock()


def guardar(t: dict) -> None:
    with CANDADO:
        escribir(TAREAS / t["id"] / "tarea.json", t)


def sitio_libre() -> str | None:
    """None si al equipo le queda sitio para un agente más (disco y RAM de [recursos]); si no, qué falta.
    Cada agente es una sesión de 1-2 GB de RAM que además puede compilar."""
    falta = []
    sistema = Path(os.environ.get("SystemDrive", "C:") + "\\") if sys.platform == "win32" else Path("/")
    for ruta, minimo, nombre in ((DATOS if DATOS.exists() else Path.home(), CFG.min_disco_gb, "disco de las obras"),
                                 (sistema, CFG.min_sistema_gb, "disco del sistema")):
        libre = shutil.disk_usage(ruta).free / 2**30
        if libre < minimo:
            falta.append(f"{nombre}: {libre:.0f} GB libres (mínimo {minimo:g})")
    ram = psutil.virtual_memory().available / 2**30
    if ram < CFG.min_ram_gb:
        falta.append(f"RAM: {ram:.1f} GB libres (mínimo {CFG.min_ram_gb:g})")
    return "; ".join(falta) or None


def esperar_sitio(t: dict, que: str) -> None:
    """Espera a que haya sitio en el equipo; lo apunta una vez en la tarea."""
    avisado = False
    while motivo := sitio_libre():
        if not avisado:
            log(t, f"{que}: espera, no hay sitio en el equipo ({motivo})")
            avisado = True
        time.sleep(30)
    if avisado:
        log(t, f"{que}: ya hay sitio, arranca")


def cargar(tid: str) -> dict:
    return leer(TAREAS / tid / "tarea.json")


# --- Omoikane: el brain de Kumi ---------------------------------------------------------
# La central lee y escribe el brain ella misma; no depende de que los agentes se acuerden.
#   · antes de trabajar: lo que el proyecto y la factoría ya saben entra en las instrucciones;
#   · la cola de decisiones son decisiones `open` en Omoikane; las [blocking] paran la publicación;
#   · lo que sale (publicado, parado, PR humana) queda registrado en el proyecto.

OMOI_ERROR = ""


def omoi(*args: str, entrada: str | None = None) -> str:
    """La memoria. Con `[memoria] motor = "ninguna"` Kumi trabaja sin ella: perfiles y cola de decisiones vacíos."""
    global OMOI_ERROR
    if CFG.memoria == "ninguna" or not shutil.which("omoi"):
        OMOI_ERROR = "sin memoria configurada"
        return ""
    r = subprocess.run(["omoi", *args], input=entrada, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "OMOIKANE_AUTHOR": "kumi"})
    OMOI_ERROR = "" if r.returncode == 0 else (r.stderr or r.stdout).strip()
    return r.stdout if r.returncode == 0 else ""


def brain_buscar(consulta: str, *, dir: str | None = None, tags: list[str] = (), n: int = 8) -> list[dict]:
    args = ["search", "--json", "-n", str(n)]
    if dir:
        args += ["--dir", dir]
    for e in tags:
        args += ["--tag", e]
    try:
        return json.loads(omoi(*args, consulta) or "[]")
    except json.JSONDecodeError:
        return []


def brain_recordar(t: dict, tipo: str, titulo: str, texto: str, etiquetas: list[str], data: str | None = None) -> str | None:
    args = ["remember", "--json", "--kind", tipo, "--title", titulo, "--dir", t["raiz"], "--source", "kumi", "--force"]
    if data:
        args += ["--data", data]
    for e in ["kumi", f"tarea:{t['id']}", *etiquetas]:
        args += ["--tag", e]
    try:
        return json.loads(omoi(*args, "-", entrada=texto) or "{}").get("id")
    except json.JSONDecodeError:
        return None


def verificar(mem_id: str | None, raiz: str, que: str) -> bool:
    """Sello «verificado por una persona» de Omoikane 1.8.5 sobre lo que el humano acaba de aprobar.

    Solo vale si de verdad lo aprueba él: desde su terminal o desde su «Respuesta:» en Notion (la recepción
    corre en el Programador de tareas, sin agente por medio). Si quien ejecuta Kumi es un agente, Omoikane
    se niega, y está bien: NO se limpian sus variables de entorno para forzarlo, porque eso sería saltarse
    la protección que impide que un agente se verifique a sí mismo.
    """
    if not mem_id:
        return False
    ok = bool(omoi("verify", mem_id, "--by", CFG.humano, "--yes", "--dir", raiz))
    print(f"[kumi] {que}: " + (f"verificado por {CFG.humano} en Omoikane" if ok
                              else "sin sello de verificación (aprobado a través de un agente)"), file=sys.stderr)
    return ok


def brain_cerrar(t: dict, mem_id: str, motivo: str) -> None:
    """Cierra una decisión de Kumi (Omoikane 1.8: quita open y blocking y añade resolved)."""
    omoi("decisions", "--resolve", mem_id, "--answer", motivo, "--dir", t["raiz"], "-r", motivo)


def cadena_de_tareas(t: dict) -> list[str]:
    """La tarea y sus antepasadas: comparten rama, así que comparten cola de decisiones."""
    ids, actual = [t["id"]], t
    while actual.get("padre") and (TAREAS / actual["padre"] / "tarea.json").exists():
        actual = cargar(actual["padre"])
        ids.append(actual["id"])
    return ids


def bloqueantes_abiertas(t: dict) -> list[dict]:
    """Decisiones bloqueantes aún abiertas de esta tarea (o de su cadena), leídas del brain."""
    out, vistos = [], set()
    for tid in cadena_de_tareas(t):
        try:
            ms = json.loads(omoi("decisions", "--blocking", "--tag", f"tarea:{tid}", "--json", "--dir", t["raiz"]) or "[]")
        except json.JSONDecodeError:
            ms = []
        for m in ms:
            if m["id"] not in vistos:
                vistos.add(m["id"])
                out.append(m)
    return out


def brain_para(t: dict, papel: str, nombre: str) -> str:
    """Lo que el brain ya sabe y le sirve a esta factoría o cadena de test, para sus instrucciones."""
    # Las globales (preferencias del humano) ya las inyecta Omoikane al arrancar la sesión: aquí, solo lo propio.
    propio = lambda ms: [m for m in ms if not m.get("global") and m.get("project")]  # noqa: E731
    proyecto = propio(brain_buscar(t["texto"][:300], dir=t["raiz"], n=20))[:8]
    oficio = []
    abiertas = propio(brain_buscar("decision", dir=t["raiz"], tags=["open"], n=20))[:10]
    # Las preferencias y reglas del oficio (Scaffold en Android…) las inyecta Omoikane 1.8 al arrancar la sesión
    # en cualquier proyecto de ese stack; aquí solo va lo del proyecto.
    tipo, reglas = None, []
    if not (proyecto or oficio or abiertas):
        return ""
    linea = lambda m: f"- `{m['id'][:12]}` [{m.get('kind', '')}] {m.get('title', '')}"  # noqa: E731
    partes = ["\n## Lo que ya sabe el brain (Omoikane)\n",
              "Léelo antes de empezar; usa `get` con el id para verlo entero. Si algo de aquí te sirve, ponlo en `memoria_usada`."]
    if reglas:
        partes += [f"\n**Reglas de {CFG.humano} para todo proyecto {nombre}{' de tipo ' + tipo if tipo else ''} (aplícalas sin que lo pida):**",
                   *(f"- {m.get('content', '').splitlines()[0]}" for m in reglas)]
    if proyecto:
        partes += ["\n**Del proyecto:**", *map(linea, proyecto)]
    if oficio:
        partes += [f"\n**De la {papel} {nombre} (vale para cualquier proyecto de este tipo):**", *map(linea, oficio)]
    if abiertas:
        partes += ["\n**Decisiones abiertas del proyecto (no las contradigas; si te afectan, dilo):**", *map(linea, abiertas)]
    return "\n".join(partes) + "\n"


# --- Perfil del proyecto (en Omoikane) ------------------------------------------------
# Las decisiones del humano sobre cada proyecto viven en su brain, no en Kumi: qué factoría lo trabaja,
# qué tests extra no le hacen falta, si lleva prod tests, en qué fase está y si es de un cliente.
#   página personal con noindex: sin seo-geo ni cyber, solo que se despliegue bien.
#   web de cliente: todo, con prod tests y aviso al cliente.
#   app Android en alpha: sin prod tests.
# Va con la clave git del proyecto: lo ven todos sus clones y worktrees, y viaja con el sync de Omoikane.

# Sin «kumi» en la clave: es la misma en las capas de oficio, y Omoikane rechaza ahí nombres de proyecto.
PERFIL_TAG = "megafactoria-ruta"
PERFIL_CLAVE = "megafactoria-ruta"
PERFIL_TITULO = "Kumi: ruta del proyecto por la megafactoría"
PERFIL_DEFECTO = {"ruta": [], "tambien_casan": [], "tests": [], "manual": False, "tests_omitir": [], "prod_tests": True,
                  "fase": None, "tipo": None, "aviso_cliente": False, "papeles": {}}


def ruta_capas(raiz: Path) -> list[dict]:
    """Las capas de `kumi-ruta` que ve el proyecto, de la más concreta a la más general (Omoikane 1.8)."""
    try:
        return json.loads(omoi("get", "--key", PERFIL_CLAVE, "--resolve", "--json", "--dir", str(raiz)) or "[]")
    except json.JSONDecodeError:
        return []


def perfil_leer(raiz: Path) -> dict | None:
    """La ruta del proyecto combinando sus capas: proyecto > cliente (espacio) > oficio > global.

    Escalares y `ruta`: gana la capa más concreta que los tenga. `tests`: unión de todas.
    `encadena_con` de una capa (p. ej. un cliente que siempre quiere seo-geo) se añade a la ruta.
    `tests_omitir`: el de la capa más concreta que lo tenga. Nunca se omite el test propio (lo comprueba
    quien lo use: tests_que_no_tocan y la orden `perfil`).
    """
    capas = ruta_capas(raiz)
    if not capas:
        return None
    p = {**PERFIL_DEFECTO, "ruta": [], "tests": [], "capas": [c.get("scope") for c in capas]}
    hay_omitir = False
    for c in capas:                                   # de la más concreta a la más general
        d = c.get("data") or {}
        for k in ("tambien_casan", "manual", "prod_tests", "fase", "tipo", "aviso_cliente", "papeles"):
            if k in d and p.get(f"_{k}") is None:
                p[k], p[f"_{k}"] = d[k], True
        if d.get("ruta") and not p["ruta"]:
            p["ruta"] = list(d["ruta"])
        if "tests_omitir" in d and not hay_omitir:
            p["tests_omitir"], hay_omitir = list(d["tests_omitir"]), True
        if c.get("scope") == "project":
            p["id"] = c.get("id")
    for c in reversed(capas):                         # unión en orden: lo general primero, lo propio después
        d = c.get("data") or {}
        for x in d.get("tests", []):
            if x not in p["tests"]:
                p["tests"].append(x)
        for f in d.get("encadena_con", []) if not str(c.get("scope", "")).startswith("trade") else []:
            if p["ruta"] and f not in p["ruta"]:
                p["ruta"].append(f)
    return {k: v for k, v in p.items() if not k.startswith("_")}


def perfil_texto(p: dict) -> str:
    partes = []
    if p["ruta"]:
        partes.append("Factorías del proyecto: " + ", ".join(p["ruta"])
                      + (" (cada tarea levanta las que necesita y en el orden que necesita; sin tarea concreta, la primera)"
                         if len(p["ruta"]) > 1 else "")
                      + (" · fijadas a mano" if p["manual"] else ""))
    if p["tambien_casan"]:
        partes.append("También casa con: " + ", ".join(p["tambien_casan"]) + " (pasa sus tests)")
    if p.get("tests"):
        activos = [x for x in p["tests"] if x not in p["tests_omitir"]]
        partes.append("Factorías de test que usa: " + ", ".join(activos))
    if p["tipo"]:
        partes.append(f"Tipo: {p['tipo']}")
    if p["fase"]:
        partes.append(f"Fase: {p['fase']}")
    partes.append("Tests que no le hacen falta: " + (", ".join(p["tests_omitir"]) or "ninguno (pasa todos)"))
    partes.append("Prod tests: " + ("sí" if p["prod_tests"] else "no"))
    partes.append("Aviso al cliente: " + ("sí" if p["aviso_cliente"] else "no"))
    if p.get("papeles"):
        partes.append("Motores propios del proyecto: " + "; ".join(
            f"{k} = {', '.join(v if isinstance(v, str) else motores.describir(v) for v in vs)}"
            for k, vs in p["papeles"].items()))
    return "\n".join(f"- {x}" for x in partes)


def perfil_guardar(raiz: Path, cambios: dict, motivo: str) -> dict:
    """Guarda la capa de PROYECTO de `kumi-ruta` (las de cliente y oficio no se tocan desde aquí)."""
    propia = next((c.get("data") or {} for c in ruta_capas(raiz) if c.get("scope") == "project"), None)
    nuevo = {**PERFIL_DEFECTO, **(propia or {}), **cambios}
    nuevo = {k: nuevo[k] for k in PERFIL_DEFECTO}
    if propia is not None and all(propia.get(k) == nuevo[k] for k in PERFIL_DEFECTO):
        return perfil_leer(raiz) or nuevo
    texto = (f"Decisiones de {CFG.humano} para que Kumi trabaje este proyecto. {motivo} ({ahora()[:10]}).\n\n{perfil_texto(nuevo)}\n\n"
             "La factoría de test propia del stack corre siempre: aquí solo se quitan tests extra. "
             "Cambiar: `kumi perfil --proyecto <repo> …`.")
    omoi("remember", "--kind", "decision", "--title", PERFIL_TITULO, "--key", PERFIL_CLAVE,
         "--data", json.dumps(nuevo, ensure_ascii=False), "--dir", str(raiz), "--source", "kumi",
         "--tag", "kumi", "--tag", PERFIL_TAG, texto)
    # Un proyecto que pasa SEO/GEO tiene ese oficio (Omoikane no lo detecta por ficheros).
    if "seo-geo" in nuevo["tests"] + nuevo["ruta"] and "seo-geo" not in nuevo["tests_omitir"]:
        omoi("trades", "add", "seo-geo", "--dir", str(raiz))
    return perfil_leer(raiz) or nuevo


def asignacion_leer(raiz: Path) -> dict | None:
    p = perfil_leer(raiz)
    return p if p and p.get("ruta") else None


def asignacion_guardar(raiz: Path, ruta: list[str], tambien: list[str], motivo: str, manual: bool = False) -> None:
    perfil_guardar(raiz, {"ruta": ruta, "tambien_casan": tambien, "manual": manual}, motivo)


def omoi_decision(t: dict, titulo: str, texto: str, etiquetas: list[str], data: str | None = None) -> str | None:
    """La cola de decisiones de Kumi vive en Omoikane. Las [blocking] llevan la etiqueta nativa `blocking`."""
    prioridad = ["blocking"] if texto.lstrip().startswith("[blocking]") else []
    return brain_recordar(t, "decision", titulo, texto, ["open", *prioridad, *etiquetas], data=data)


def ver_memoria(mid: str | None) -> str:
    """Cómo abrir una memoria en la vista web de Omoikane (1.8.6+): `omoi serve --open --memory` arranca la vista o
    reutiliza la abierta, con su token. Un enlace suelto `#/memoria/<id>` no carga si la vista lleva token."""
    if not mid or CFG.memoria == "ninguna":
        return ""
    return f"\n\nAbrirla en la memoria: `omoi serve --open --memory {mid[:12]}`"


def avisar(t: dict, asunto: str, cuerpo: str, cliente: bool = False, tipo: str = "aviso") -> None:
    """Aviso: fichero en la tarea + los canales que `[enrutado]` asigna a su tipo (aviso, pr_humana, pregunta,
    cliente). Al cliente, solo si el perfil o la receta lo piden."""
    tipo = "cliente" if cliente else tipo
    carpeta = TAREAS / t["id"]
    nombre = "aviso_cliente.md" if cliente else "aviso.md"
    (carpeta / nombre).write_text(f"# {asunto}\n\n{cuerpo}\n", encoding="utf-8")
    fallos = canales.avisar(tipo, asunto, f"{cuerpo}\n\nTarea {t['id']} · {Path(t['raiz']).name}", tarea=t["id"])
    t.setdefault("avisos", []).append({"cuando": ahora(), "tipo": tipo, "asunto": asunto,
                                       "canales": CFG.enrutado.get(tipo, []), **({"fallos": fallos} if fallos else {})})
    guardar(t)


# --- Plazas: una tarea por proyecto a la vez; cupo por factoría y por cadena de test ----

def coger_plaza(clave: str, cupo: int, dueño: str) -> Path:
    CERROJOS.mkdir(parents=True, exist_ok=True)
    avisado = False
    while True:
        for n in range(cupo):
            p = CERROJOS / f"{clave}-{n}.pid"
            if p.exists():
                try:
                    pid = int(p.read_text().split()[0])
                except (ValueError, IndexError):
                    pid = -1
                if psutil.pid_exists(pid):
                    continue
                p.unlink(missing_ok=True)
            try:
                fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                continue
            os.write(fd, f"{os.getpid()} {dueño}".encode())
            os.close(fd)
            return p
        if not avisado:
            print(f"[kumi] {clave}: ocupado ({cupo}), en cola…", file=sys.stderr, flush=True)
            avisado = True
        time.sleep(5)


def clave_proyecto(raiz: Path) -> str:
    comun = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=raiz))
    return "proyecto-" + re.sub(r"[^A-Za-z0-9]+", "_", str(comun.parent)).strip("_")[-60:]


# --- Encargado: qué cadena de montaje -------------------------------------------------

def ficheros_repo(raiz: Path) -> list[str]:
    """Lo que lleva git: rápido y sin node_modules, target ni build."""
    salida = git("ls-files", "--cached", "--others", "--exclude-standard", cwd=raiz, ok=True) or ""
    return salida.splitlines()


def casa_huella(raiz: Path, huella: dict, ficheros: list[str] | None = None) -> int:
    """0 si no casa; si casa, cuántas reglas cumplió (más = más específica).

    Globs con fnmatch sobre las rutas de git (`*` cruza carpetas): `todos` deben casar todos,
    `alguno` al menos uno, `nunca` ninguno.
    """
    import fnmatch
    fs = ficheros if ficheros is not None else ficheros_repo(raiz)
    hay = lambda g: any(fnmatch.fnmatch(f, g) for f in fs)  # noqa: E731
    todos, alguno, nunca = huella.get("todos", []), huella.get("alguno", []), huella.get("nunca", [])
    if not all(hay(g) for g in todos) or any(hay(g) for g in nunca):
        return 0
    n_alguno = sum(1 for g in alguno if hay(g))
    if alguno and not n_alguno:
        return 0
    return len(todos) + n_alguno


def tests_que_no_tocan(t: dict, d: dict, factorias: dict) -> dict[str, str]:
    """Tests EXTRA que solo tienen sentido si el cambio toca lo suyo (`tests_si_cambio` de la factoría).

    Ej.: en una web, seo-geo solo corre si el diff toca contenido, metadatos, rutas, robots o sitemap; un
    cambio solo de CSS no lo necesita. Nunca se salta la factoría de test propia del stack (regla de Kumi)
    ni una que no declare condición.
    """
    import fnmatch
    obra = Path(t["obra"])
    rutas = (git("diff", "--name-only", "--no-renames", f"{t['base']}...HEAD", cwd=obra, ok=True) or "").split()
    diff = git("diff", f"{t['base']}...HEAD", cwd=obra, ok=True) or ""
    cambiadas = "\n".join(l[1:] for l in diff.splitlines() if l[:1] in "+-" and l[:3] not in ("+++", "---"))
    propias = set(d["ruta"]) | set(d.get("tambien_casan", []))
    saltar = {}
    # El proyecto manda: su receta puede quitar tests extra que no le aplican (una página personal
    # con noindex no necesita seo-geo). Las webs de empresa lo siguen pasando siempre.
    perfil = perfil_leer(Path(t["raiz"])) if t.get("raiz") else None
    for test in (perfil or {}).get("tests_omitir", []):
        if test in d["tests"] and test not in propias:
            saltar[test] = "el perfil del proyecto en Omoikane no lo necesita"
    for f in d["ruta"] + d.get("tambien_casan", []):
        for test, cond in factorias[f].get("tests_si_cambio", {}).items():
            if test in propias or test in saltar or test not in d["tests"]:
                continue
            por_ruta = any(fnmatch.fnmatch(r, g) for r in rutas for g in cond.get("rutas", []))
            por_texto = any(re.search(p, cambiadas, re.M) for p in cond.get("contenido", []))
            if not (por_ruta or por_texto):
                saltar[test] = "el cambio no toca nada suyo"
    return saltar


def aprender(t: dict, peticion: str) -> list[dict]:
    """Paso «aprender»: ¿lo que el humano acaba de pedir es de este proyecto o una costumbre suya para todo el stack?

    Lo que sea del stack se PROPONE como regla del oficio en una decisión abierta; el humano la confirma con
    `kumi aprobar-regla <id>`. Nunca se guarda una regla sin su sí.
    """
    d_f = TAREAS / t["id"] / "decision.json"
    ruta = json.loads(d_f.read_text(encoding="utf-8")).get("ruta", []) if d_f.exists() else []
    if not ruta:
        return []
    perfil = perfil_leer(Path(t["raiz"])) or {}
    extra = (f"- Lo que pidió {CFG.humano}: «{peticion[:1500]}»\n- Factoría del proyecto: {', '.join(ruta)} · tipo de proyecto: "
             f"{perfil.get('tipo') or 'sin indicar'}\n- Tareas anteriores de Kumi: `{TAREAS}`\n")
    res = lanzar_agente(t, "aprendiz", "oficio", receta_de(catalogo("aprendiz.md")), extra=extra)
    propuestas = []
    for pr in res.get("propuestas", []) or []:
        oficio, regla = pr.get("oficio") or ruta[0], (pr.get("regla") or "").strip()
        if not regla:
            continue
        solo = pr.get("solo_tipo")
        datos = json.dumps({"oficio": oficio, "solo_tipo": solo, "regla": regla}, ensure_ascii=False)
        mid = omoi_decision(
            t, f"Kumi: ¿regla para todo proyecto {oficio}{' de ' + solo if solo else ''}? «{regla[:70]}»",
            f"[review-after] Propuesta de regla de oficio. {regla}\nPor qué: {pr.get('por_que', '')}\n"
            f"Evidencia: {', '.join(pr.get('evidencia', []))}\n"
            f"Aprobar: kumi aprobar-regla <id> · Descartar: kumi descartar-regla <id>",
            ["kumi-propuesta", f"oficio:{oficio}"], data=datos)
        propuestas.append({"id": mid, "oficio": oficio, "regla": regla})
    if propuestas:
        t["propuestas_regla"] = propuestas
        guardar(t)
        avisar(t, f"Kumi propone {len(propuestas)} regla(s) de oficio",
               "\n".join(f"- [{x['oficio']}] {x['regla']} → `kumi aprobar-regla {(x['id'] or '?')[:12]}`" for x in propuestas), tipo="pregunta")
    return propuestas


def propuestas_regla(abiertas: bool = True) -> list[dict]:
    try:
        return json.loads(omoi("list", "--tag", "kumi-propuesta", *(["--tag", "open"] if abiertas else []),
                               "--all", "--json") or "[]")
    except json.JSONDecodeError:
        return []


def regla_resolver(mem_id: str, aprobar: bool) -> str:
    """Aprueba (la guarda como memoria del OFICIO en Omoikane) o descarta una propuesta del aprendiz."""
    m = next((x for x in propuestas_regla() if x["id"].startswith(mem_id)), None)
    if not m:
        raise SystemExit(f"No encuentro ninguna propuesta abierta que empiece por {mem_id}")
    datos = m.get("data") or json.loads(re.search(r"```json\s*(\{.*?\})\s*```", m["content"], re.S).group(1))
    raiz = m.get("project") or str(DATOS)
    if aprobar:
        para = {"cliente": "client", "personal": "personal"}.get(datos.get("solo_tipo") or "", None)
        args = ["remember", "--kind", "preference", "--trade", datos["oficio"], *(["--for", para] if para else []),
                "--title", f"Regla del oficio {datos['oficio']}: {datos['regla'][:80]}", "--dir", str(DATOS),
                "--source", "kumi", "--tag", "megafactoria", "--tag", "regla-oficio"]
        salida = omoi(*args, "--json", datos["regla"])
        if not salida:
            return f"Omoikane no la ha guardado: {OMOI_ERROR}"
        try:
            verificar(json.loads(salida).get("id"), str(DATOS), "regla del oficio")
        except json.JSONDecodeError:
            pass
    omoi("decisions", "--resolve", m["id"], "--answer", "aprobada" if aprobar else "descartada", "--dir", raiz)
    if not aprobar:
        return f"Descartada: {datos['regla']}"
    quien = datos["oficio"] + (f" de tipo {datos['solo_tipo']}" if datos.get("solo_tipo") else "")
    return f"Regla guardada como memoria del oficio {quien} (Omoikane la inyecta en cada proyecto de ese stack): {datos['regla']}"


def consultar(prompt: str, papel: str = "encargado", cwd: str | None = None, perfil: dict | None = None) -> str:
    """Una pregunta corta sin herramientas, con el motor y modelo que la configuración da a ese papel."""
    return motores.consultar(CFG.elecciones(papel, perfil=perfil), prompt, cwd)


def choque_con_decisiones(t: dict) -> dict | None:
    """¿La tarea va contra una decisión ya registrada del proyecto? Si es así, pasa a modo con spec.

    Hace falta un modelo (código solo no sabe si «quita el modo oscuro» contradice «la app tiene modo oscuro»).
    Si se equivoca, lo peor que pasa es que una tarea va por el camino con spec sin necesitarlo.
    """
    decs = [m for m in brain_buscar(t["texto"][:300], dir=t["raiz"], n=25)
            if m.get("kind") == "decision" and m.get("project") and "open" not in (m.get("tags") or [])
            and "kumi" not in (m.get("tags") or [])][:12]
    if not decs:
        return None
    lista = "\n".join(f"- [{m['id'][:12]}] {m.get('title', '')}: {(m.get('snippet') or m.get('content') or '')[:300]}"
                      for m in decs)
    texto = consultar(
        "Estas son decisiones ya tomadas en un proyecto de software, y una tarea nueva. ¿La tarea CONTRADICE alguna "
        "(hacer lo contrario de lo decidido, quitar algo que se decidió tener, cambiar una regla fijada)? Que la tarea "
        "toque el mismo tema NO es contradecir.\n\n"
        f"Decisiones:\n{lista}\n\nTarea:\n{t['texto'][:2500]}\n\n"
        'Responde SOLO con JSON: {"choca": true|false, "decision": "<id de 12 caracteres o null>", "por_que": "una frase"}',
        "encargado", cwd=t["raiz"])
    m = re.search(r"\{.*\}", texto, re.S)
    try:
        r = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return None
    if not r.get("choca"):
        return None
    dec = next((d for d in decs if d["id"].startswith(str(r.get("decision") or "")[:12]) and r.get("decision")), None)
    return {"decision": dec["id"] if dec else None, "titulo": dec.get("title") if dec else None,
            "por_que": str(r.get("por_que", ""))[:300]}


def encargado_extras(t: dict, d: dict, reg: dict) -> list[dict]:
    """Plan de la tarea: qué factorías DEL PROYECTO levanta (cualquier subconjunto, en cualquier orden) y si
    necesita añadir alguna más (extras). Deja el subconjunto en t["activar"]; devuelve los extras."""
    lista_cadenas = "\n".join(f"- factoría `{f['nombre']}`: {f.get('descripcion', '')}" for f in reg["factorias"])
    lista_cadenas += "\n" + "\n".join(f"- test `{x['nombre']}`: {x.get('descripcion', '')}" for x in reg["tests"])
    prompt = (receta_de(catalogo("encargado.md"))
              + f"\n\n## Proyecto\n`{Path(t['raiz']).name}` · factorías del proyecto: {', '.join(d['ruta'])}"
              + f" · factorías de test: {', '.join(d['tests'])}\n\n## Catálogo\n{lista_cadenas}\n\n## Tarea\n{t['texto'][:3000]}\n")
    texto = consultar(prompt, "encargado", cwd=t["raiz"])
    m = re.search(r"\{.*\}", texto, re.S)
    try:
        plan = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        plan = {}
    extras = plan.get("extras", []) or []
    t["activar"] = [f for f in (plan.get("activar") or []) if f in d["ruta"]]
    nombres_f = {f["nombre"] for f in reg["factorias"]}
    nombres_t = {x["nombre"] for x in reg["tests"]}
    limpios = []
    for e in extras:
        nombre = re.sub(r"[^a-z0-9-]+", "-", str(e.get("nombre", "")).lower()).strip("-")
        if not nombre or e.get("tipo") not in ("factoria", "test"):
            continue
        ya = nombre in (d["ruta"] if e["tipo"] == "factoria" else d["tests"])
        if ya:
            continue
        existe = nombre in (nombres_f if e["tipo"] == "factoria" else nombres_t)
        limpios.append({"tipo": e["tipo"], "nombre": nombre, "existe": existe,
                        "alcance": "permanente" if e.get("alcance") == "permanente" else "puntual",
                        "por_que": str(e.get("por_que", ""))[:300]})
    return limpios


def factorias_por_nombre(reg: dict) -> dict:
    return {f["nombre"]: f for f in reg["factorias"]}


def factorias_sin_test() -> list[str]:
    """Factorías de producción a las que les falta su factoría de test (tests/<mismo nombre>/test.toml)."""
    return sorted(p.parent.name for p in catalogo_glob("factorias/*/factoria.toml")
                  if not catalogo("tests", p.parent.name, "test.toml").exists())


def tests_condicionales(raiz: Path, fs: list[str], factoria: dict) -> list[str]:
    """Tests que una factoría añade según el repo: `tests_si` (globs de ruta) y `tests_si_contenido`
    (regex sobre el código). Ej.: una app Python que importa tkinter pasa también por `escritorio`."""
    import fnmatch
    out = []
    for test, globs in factoria.get("tests_si", {}).items():
        if any(fnmatch.fnmatch(f, g) for f in fs for g in globs):
            out.append(test)
    codigo = [f for f in fs if f.endswith((".py", ".pyw", ".js", ".ts", ".rs", ".kt"))][:400]
    for test, patrones in factoria.get("tests_si_contenido", {}).items():
        if test in out:
            continue
        rx = [re.compile(p, re.M) for p in patrones]
        for f in codigo:
            try:
                texto = (raiz / f).read_text(encoding="utf-8", errors="replace")[:200_000]
            except OSError:
                continue
            if any(r.search(texto) for r in rx):
                out.append(test)
                break
    return out


def encargado(t: dict, reg: dict, ruta_pedida: list[str] | None) -> dict:
    raiz = Path(t["raiz"])
    factorias = {f["nombre"]: f for f in reg["factorias"]}
    fs = ficheros_repo(raiz)
    # Orden: prioridad declarada, luego lo específica que es la huella.
    candidatas = sorted(((f.get("prioridad", 0), casa_huella(raiz, f.get("huella", {}), fs), f["nombre"])
                         for f in reg["factorias"]), reverse=True)
    candidatas = [(p, n, nombre) for p, n, nombre in candidatas if n]
    d = {"cuando": ahora(), "candidatas": [{"factoria": c, "prioridad": p, "puntos": n} for p, n, c in candidatas]}

    asignada = None if ruta_pedida else asignacion_leer(raiz)
    if asignada and not all(f in factorias for f in asignada["ruta"]):
        asignada = None                              # la factoría vinculada ya no existe
    if asignada and not any(c == asignada["ruta"][0] for _, _, c in candidatas) and not asignada.get("manual"):
        log(t, f"el proyecto ya no casa con «{asignada['ruta'][0]}»: se reasigna")
        asignada = None                              # el repo cambió de stack

    if ruta_pedida:
        faltan = [f for f in ruta_pedida if f not in factorias]
        d.update(accion="construir" if faltan else "usar", ruta=ruta_pedida, faltan=faltan,
                 motivo="ruta pedida en la tarea")
    elif asignada:
        d.update(accion="usar", ruta=asignada["ruta"], tambien_casan=asignada.get("tambien_casan", []),
                 motivo=f"ruta del proyecto cargada de Omoikane ({asignada['id'][:12]}): "
                        + " → ".join(asignada["ruta"]) + (f" · tests {', '.join(asignada['tests'])}" if asignada.get("tests") else ""))
    elif candidatas:
        principal = candidatas[0][2]
        d.update(accion="usar", ruta=[principal], motivo=f"la huella del proyecto casa con «{principal}»")
        if len(candidatas) > 1:
            d["tambien_casan"] = [c for _, _, c in candidatas[1:]]
    else:
        d.update(accion="construir", ruta=[], motivo="ninguna cadena de montaje reconoce este proyecto")

    if d["accion"] == "usar":
        provisionales = [f for f in d["ruta"] if factorias[f].get("provisional")]
        # Regla de Kumi: cada stack tiene su factoría y su factoría de test, con el mismo nombre.
        # La suya va siempre la primera, aunque su factoria.toml no la nombre.
        tests = []
        if asignada and asignada.get("tests"):
            tests = list(asignada["tests"])          # la ruta guardada manda: no se recalcula
        # Las factorías de test propias, siempre y al principio, en el orden de la ruta.
        for f in reversed(d["ruta"] + d.get("tambien_casan", [])):
            if f in tests:
                tests.remove(f)
            tests.insert(0, f)
        # Repos mixtos (Rust + plugin, app + web): produce la principal, prueban todas las que casan.
        for f in ([] if (asignada and asignada.get("tests")) else d["ruta"] + d.get("tambien_casan", [])):
            tests += [x for x in factorias[f].get("tests", []) if x not in tests]
            for x in tests_condicionales(raiz, fs, factorias[f]):
                if x not in tests:
                    tests.append(x)
                    d.setdefault("tests_por_contenido", []).append(f"{x} (por {f})")
        d["tests"] = tests
        tests_reg = {x["nombre"]: x for x in reg["tests"]}
        provisionales += [f"test:{x}" for x in tests if tests_reg.get(x, {}).get("provisional")]
        faltan_tests = [x for x in tests if x not in tests_reg]
        if faltan_tests:
            d.update(accion="construir", faltan=[f"test:{x}" for x in faltan_tests],
                     motivo=f"faltan cadenas de test: {', '.join(faltan_tests)}")
        elif provisionales:
            d.update(accion="esperar_aprobacion", provisionales=provisionales,
                     motivo=f"la cadena está diseñada pero {CFG.humano} aún no la ha aprobado")
        else:
            # El proyecto queda vinculado a sus factorías y a sus factorías de test, en Omoikane.
            # La próxima tarea las carga tal cual. Si la ruta se pidió con --ruta (web → seo-geo), también.
            previas = (perfil_leer(raiz) or {}).get("ruta", []) if ruta_pedida else []
            perfil_guardar(raiz, {"ruta": list(dict.fromkeys(previas + d["ruta"])), "tambien_casan": d.get("tambien_casan", []),
                                  "tests": d["tests"], **({"manual": True} if ruta_pedida else {})},
                           "Ruta pedida en la tarea" if ruta_pedida else d["motivo"])
    escribir(TAREAS / t["id"] / "decision.json", d)
    return d


# --- Lanzar un agente (factoría, cadena de test, planificador) -------------------------

def contexto(t: dict, papel: str, nombre: str, carpeta: Path, extra: str = "") -> str:
    return f"""
## Este trabajo

- Tarea `{t['id']}` (origen: {t['origen']}{f", padre `{t['padre']}`" if t.get('padre') else ''}) · papel: {papel} `{nombre}`
- Proyecto: `{t['raiz']}` · trabajas en: `{t.get('obra') or t['raiz']}`{f" · rama `{t['rama']}`" if t.get('rama') else ''} · rama base (contra la que se compara y se fusiona): `{t['base']}`
- Tu carpeta (documentos, evidencias, informe): `{carpeta}`
- Memoria del proyecto: Omoikane en este repo (decisiones, gotchas). Lo que valga para CUALQUIER proyecto de este stack va como memoria de oficio: `remember` con `--trade <oficio>` (CLI) o `trade` (MCP). Sin nombres de proyecto, clientes, rutas ni URLs: Omoikane lo rechaza.
- Firmas en Omoikane como `kumi-{papel}-{nombre}` (ya está en OMOIKANE_AUTHOR). Etiqueta lo que guardes con `tarea:{t['id']}`.
- Registro de piezas y cadenas de Kumi: `{DATOS / 'registro.json'}` · catálogo de serie: `{RAIZ}` · cadenas propias de esta instalación: `{DATOS}` (factorias/, tests/)
- Android SDK: `{ANDROID_SDK}` · adb: `{ANDROID_SDK / 'platform-tools' / ('adb' + EXE)}` · emulator: `{ANDROID_SDK / 'emulator' / ('emulator' + EXE)}` · AVD de test: `{AVD_TEST}` (si no existe, créalo con el system image que haya instalado)
- Quien aprueba: {CFG.humano}. Modo de la tarea: {t['modo']}.{(' Factorías de test de la tarea: ' + ', '.join(t['tests_tarea']) + '.') if t.get('tests_tarea') else ''}
{choque_texto(t)}{extra}
## Tarea

{t['texto']}

## Al terminar (obligatorio)

`{carpeta / 'informe.md'}` (para {CFG.humano}, corto, en español) y `{carpeta / 'resultado.json'}`:

```json
{{"estado": "ok|fallo|bloqueado|ascender", "resumen": "una frase",
  "hallazgos": [{{"gravedad": "alta|media|baja", "descripcion": "...", "evidencia": "ruta o salida", "config": "opcional"}}],
  "tipo_cambio": "arreglo|funcionalidad",
  "bloqueantes": 0, "decisiones_abiertas": ["ids de Omoikane"],
  "memoria_usada": ["ids"], "memoria_escrita": ["ids"]}}
```

Sin `resultado.json` Kumi da tu trabajo por fallido.
"""


def choque_texto(t: dict) -> str:
    c = t.get("choque")
    if not c:
        return ""
    return (f"- **Esta tarea choca con una decisión ya tomada** ({c.get('titulo') or 'ver Omoikane'}"
            f"{', `' + c['decision'][:12] + '`' if c.get('decision') else ''}): {c.get('por_que', '')}. Por eso va con spec: "
            "la spec dice qué cambia de esa decisión y por qué, y si no está claro que se quiera cambiar, va a la cola de "
            "decisiones como [blocking].\n")


def gasto_hoy() -> float:
    try:
        return float(json.loads(GASTO.read_text(encoding="utf-8")).get(dt.date.today().isoformat(), 0))
    except (OSError, ValueError, json.JSONDecodeError):
        return 0.0


def sumar_gasto(usd: float | None) -> None:
    """Gasto del día de todos los agentes (solo lo que el motor declara: Claude Code lo da; otros, no siempre)."""
    if not usd:
        return
    try:
        d = json.loads(GASTO.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        d = {}
    hoy = dt.date.today().isoformat()
    d = {k: v for k, v in d.items() if k >= (dt.date.today() - dt.timedelta(days=60)).isoformat()}
    d[hoy] = round(d.get(hoy, 0) + usd, 4)
    GASTO.parent.mkdir(parents=True, exist_ok=True)
    GASTO.write_text(json.dumps(d, indent=1), encoding="utf-8")


def gasto_superado(t: dict) -> str | None:
    """Motivo si esta tarea (con las de su cadena: vueltas del supervisor, cambios pedidos) o el día ya pasaron
    el tope de [gasto]; None si se puede seguir."""
    if CFG.gasto_tarea:
        cadena = sum((cargar(i).get("usd") or 0) if i != t["id"] else (t.get("usd") or 0) for i in cadena_de_tareas(t))
        if cadena >= CFG.gasto_tarea:
            return f"la tarea lleva {cadena:.2f} $ y el tope por tarea es {CFG.gasto_tarea:.2f} $"
    if CFG.gasto_dia:
        hoy = gasto_hoy()
        if hoy >= CFG.gasto_dia:
            return f"hoy se llevan {hoy:.2f} $ y el tope diario es {CFG.gasto_dia:.2f} $"
    return None


def lanzar_agente(t: dict, papel: str, nombre: str, receta: str, extra: str = "", necesita: list[str] = (),
                  desde: int = 0) -> dict:
    """Una sesión de agente con un oficio, con el motor y modelo que la configuración da a ese papel (y los de
    respaldo si el primero no puede). Devuelve su resultado.json ya validado."""
    tope = gasto_superado(t)
    if tope:
        if not t.get("tope_gasto"):
            t["tope_gasto"] = tope
            log(t, f"tope de gasto: {tope}")
            avisar(t, "Tarea parada por el tope de gasto", tope + ". Súbelo en [gasto] de kumi.toml y reanuda con "
                   f"`kumi reanudar {t['id']}`.")
        return {"estado": "bloqueado", "resumen": tope, "hallazgos": [], "bloqueantes": 1}
    esperar_sitio(t, f"{papel} {nombre}")
    with CANDADO:
        (TAREAS / t["id"]).mkdir(parents=True, exist_ok=True)
        n = max(len(t.get("pasos", [])), len(list((TAREAS / t["id"]).glob("[0-9][0-9]-*")))) + 1
        paso = f"{n:02d}-{papel}-{nombre}"
        carpeta = TAREAS / t["id"] / paso
        carpeta.mkdir(parents=True)
    prompt = humanizar_texto(receta) + "\n" + contexto(t, papel, nombre, carpeta, extra + brain_para(t, papel, nombre))
    span = traza.nuevo_span()
    env = {**os.environ, "OMOIKANE_AUTHOR": f"kumi-{papel}-{nombre}", "KUMI_TAREA": t["id"], "PYTHONIOENCODING": "utf-8",
           **traza.entorno(t, span, papel, nombre, paso)}
    perfil = perfil_leer(Path(t["raiz"])) if t.get("raiz") else None
    elecciones = CFG.elecciones(papel, nombre, perfil)
    # Si en esta cadena (vueltas del supervisor, cambios pedidos) ya acertó un motor, se empieza por él y no por
    # el primero de la lista, que ya falló una vez.
    bueno = t.get("motores_buenos", {}).get(f"{papel}:{nombre}")
    if bueno:
        igual = lambda e: (e.get("motor"), e.get("modelo")) == (bueno.get("motor"), bueno.get("modelo"))  # noqa: E731
        elecciones = [e for e in elecciones if igual(e)] + [e for e in elecciones if not igual(e)]
    elecciones = elecciones[desde:]
    inicio = ahora()
    inicio_ns = time.time_ns()
    ej = motores.ejecutar(elecciones, prompt, t.get("obra") or t["raiz"], env, carpeta, list(necesita))
    if ej.error:
        log(t, f"{papel} {nombre}: {ej.error}")
    res, problemas = motores.leer_resultado(carpeta / "resultado.json")
    if res is not None and problemas:
        # El contrato no se cumplió del todo: una vuelta corta para que lo arregle el mismo motor.
        log(t, f"{papel} {nombre}: resultado.json no cumple el contrato ({'; '.join(problemas)}), se le pide que lo corrija")
        arreglo = (f"Tu trabajo está hecho, pero `{carpeta / 'resultado.json'}` no cumple el contrato de Kumi:\n"
                   + "\n".join(f"- {p}" for p in problemas)
                   + "\n\nReescribe SOLO ese fichero con el formato correcto, sin cambiar nada más. El formato está al "
                     f"final de `{carpeta / 'instrucciones.md'}`.")
        sub = carpeta / "correccion"
        sub.mkdir(exist_ok=True)
        motores.ejecutar([ej.eleccion], arreglo, t.get("obra") or t["raiz"], env, sub, [])
        res2, problemas2 = motores.leer_resultado(carpeta / "resultado.json")
        res = res2 or res
        if problemas2:
            log(t, f"{papel} {nombre}: sigue sin cumplir el contrato; Kumi usa lo que se pudo leer")
    if res is None:
        res = {"estado": "fallo", "resumen": f"no dejó un resultado.json válido ({'; '.join(problemas)})",
               "hallazgos": [], "bloqueantes": 0}
    if papel == "factoria" and res.get("tipo_cambio") == "funcionalidad":
        t["funcionalidad_nueva"] = True      # sube el minor al publicar; los arreglos, el parche
    CANDADO.acquire()
    t.setdefault("pasos", []).append({"paso": paso, "papel": papel, "nombre": nombre, "inicio": inicio,
                                      "fin": ahora(), "codigo": ej.codigo, "usd": ej.usd, "turnos": ej.turnos,
                                      "motor": ej.eleccion.get("motor"), "modelo": ej.eleccion.get("modelo"),
                                      "familia": motores.familia(ej.eleccion),
                                      "estado": res.get("estado"), "resumen": res.get("resumen", ""),
                                      "span": span})
    t["usd"] = round(sum(p.get("usd") or 0 for p in t["pasos"]), 2)
    sumar_gasto(ej.usd)
    CANDADO.release()
    traza.paso(t, span, papel, nombre, paso, inicio_ns, ej.eleccion, ej.codigo, ej.usd, ej.turnos, res, ej.error)
    log(t, f"{papel} {nombre} [{motores.describir(ej.eleccion)}]: {res.get('estado')} · {res.get('resumen', '')}")
    return res


def avisar_mismo_modelo(t: dict, d: dict, reg: dict) -> None:
    """Si quien prueba es de la misma familia de modelos que quien construye, se revisa con sus mismos puntos
    ciegos. Es decisión de cada persona: Kumi solo avisa, una vez por tarea."""
    if t.get("aviso_mismo_modelo") is not None:
        return
    perfil = perfil_leer(Path(t["raiz"]))
    fam = lambda papel, n: motores.familia(motores.efectiva(CFG.elecciones(papel, n, perfil)))  # noqa: E731
    constructores = {fam("factoria", f) for f in d["ruta"]}
    iguales = [x for x in d["tests"] if fam("test", x) in constructores]
    t["aviso_mismo_modelo"] = iguales
    if iguales:
        log(t, f"aviso: las factorías de test {', '.join(iguales)} usan la misma familia de modelos que la factoría "
               f"({', '.join(sorted(constructores))}); conviene otra en `[papeles] test` de kumi.toml")
    guardar(t)


def receta_de(*ficheros: Path) -> str:
    """Junta los ficheros de una receta. A cada oficio del catálogo se le suman las notas locales de esa factoría
    (lo que solo vale en esta máquina o para estos proyectos), si las hay."""
    partes = []
    for f in ficheros:
        if not f.exists():
            continue
        partes.append(f.read_text(encoding="utf-8"))
        if f.name == "oficio.md" and f.parent.parent.name in ("factorias", "tests"):
            nota = nota_local(f.parent.parent.name, f"{f.parent.name}.md")
            if nota:
                partes.append("## Notas de esta instalación\n\n" + nota.read_text(encoding="utf-8"))
    return humanizar_texto("\n\n".join(partes))


# --- Obra (worktree) -----------------------------------------------------------------

def cambio_en_obra(t: dict) -> bool:
    """¿Hay algo distinto de la base en la obra (commits o ficheros sin commitear)?"""
    obra = Path(t["obra"])
    return bool(git("status", "--porcelain", cwd=obra, ok=True)
                or git("diff", "--name-only", f"{t['base']}...HEAD", cwd=obra, ok=True))


def preparar_obra(t: dict) -> None:
    if t.get("obra") and Path(t["obra"]).exists():
        return                                   # tarea hija sobre la misma rama
    OBRAS.mkdir(parents=True, exist_ok=True)
    raiz = Path(t["raiz"])
    t["rama"] = f"kumi/{t['id']}"
    t["obra"] = str(OBRAS / t["id"])
    git("worktree", "add", "-b", t["rama"], t["obra"], t["base"], cwd=raiz)
    log(t, f"obra en {t['obra']} (rama {t['rama']} desde {t['base']})")


# --- El recorrido de una tarea --------------------------------------------------------

def crear_proyecto(descripcion: str, nombre: str | None = None, stack: str | None = None,
                   tipo: str | None = None, carpeta: Path | None = None, origen: str = "directa") -> dict:
    """Un proyecto que aún no existe: decide el stack por lo que se pide, crea el repo en D: y le encarga
    la primera versión a la factoría de ese stack (modo ASDD, PR humana siempre).

    El oficio se fija en Omoikane desde el principio (`omoi trades set`): así las reglas aprobadas para ese
    stack (el Scaffold en Android…) llegan ya a la primera versión, aunque la carpeta esté vacía.
    """
    reg = registro.construir()
    factorias = factorias_por_nombre(reg)
    if not stack or not nombre:
        lista_cadenas = "\n".join(f"- `{f['nombre']}`: {f.get('descripcion', '')}" for f in reg["factorias"])
        texto = consultar(
            f"Eres la central de Kumi, la megafactoría de software de {CFG.humano}. Le piden crear un proyecto que aún no existe. "
            "Decide con qué factoría (stack) se construye y un nombre corto para la carpeta.\n\n"
            f"Factorías:\n{lista_cadenas}\n\nLo que pide {CFG.humano}:\n{descripcion[:3000]}\n\n"
            "Si ninguna factoría sirve, pon en `stack` un nombre nuevo en minúsculas con guiones (ios, wordpress, dotnet…): "
            "la diseñará el planificador. El nombre de carpeta, en PascalCase sin espacios ni tildes (MiApp).\n"
            'Responde SOLO con JSON: {"stack": "...", "nombre": "...", "tipo": "personal|cliente|null", "por_que": "una frase"}',
            "encargado")
        import re as _re
        m = _re.search(r"\{.*\}", texto, _re.S)
        plan = json.loads(m.group(0)) if m else {}
        stack = stack or plan.get("stack")
        nombre = nombre or plan.get("nombre")
        tipo = tipo or (plan.get("tipo") if plan.get("tipo") in ("personal", "cliente") else None)
    if not stack or not nombre:
        raise SystemExit("No he podido decidir el stack o el nombre: pásalos con --stack y --nombre.")
    nombre = re.sub(r"[^A-Za-z0-9_-]+", "", nombre)
    raiz = (carpeta or CFG.proyectos / nombre).resolve()
    if raiz.exists() and any(raiz.iterdir()):
        raise SystemExit(f"{raiz} ya existe y no está vacía: usa `kumi tarea --proyecto {raiz}`.")
    raiz.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "-b", "main", cwd=raiz)
    git("-c", "user.name=Kumi", "-c", "user.email=kumi@localhost", "commit", "-q", "--allow-empty",
        "-m", "Inicio del proyecto", cwd=raiz)
    print(f"[kumi] proyecto nuevo en {raiz} · stack {stack}" + (f" · {tipo}" if tipo else ""), file=sys.stderr)
    # El oficio, ya: Omoikane inyectará sus reglas en la primera sesión aunque aún no haya ficheros.
    omoi("trades", "set", stack, "--dir", str(raiz))
    if stack in factorias:
        tests = list(dict.fromkeys([stack] + factorias[stack].get("tests", [])))
        perfil_guardar(raiz, {"ruta": [stack], "tests": tests, "manual": True, "tipo": tipo},
                       "Proyecto creado por Kumi")
    texto_tarea = (f"Crea la primera versión de este proyecto NUEVO (la carpeta está vacía: monta la estructura del stack "
                   f"«{stack}» desde cero, con su sistema de construcción, sus tests y un README corto). Lo que pide {CFG.humano}:\n\n"
                   f"{descripcion}")
    tarea = nueva_tarea(raiz, texto_tarea, origen, modo="asdd", ruta=[stack])
    tarea["siempre_humano"] = "es la primera versión de un proyecto nuevo"
    tarea["proyecto_nuevo"] = True
    guardar(tarea)
    brain_recordar(tarea, "fact", f"Proyecto creado por Kumi: {nombre}",
                   f"{ahora()[:10]}. Stack {stack}" + (f", {tipo}" if tipo else "") + f". Lo que pidió {CFG.humano}: {descripcion[:500]}",
                   ["proyecto-nuevo"])
    return recorrer(tarea)


def nueva_tarea(proyecto: Path, texto: str, origen: str = "directa", padre: dict | None = None,
                modo: str = "ajuste", ruta: list[str] | None = None, misma_obra: bool = False) -> dict:
    raiz = Path(git("rev-parse", "--show-toplevel", cwd=proyecto))
    modo = "spec" if modo in ("asdd", "spec") else "ajuste"      # «asdd» es el nombre antiguo del modo con spec
    tid =f"{dt.datetime.now():%Y%m%d-%H%M%S}-{re.sub(r'[^a-z0-9]+', '-', raiz.name.lower())[:20]}"
    (TAREAS / tid).mkdir(parents=True)
    t = {"id": tid, "origen": origen, "padre": padre["id"] if padre else None, "texto": texto,
         "raiz": str(raiz), "modo": modo, "ruta_pedida": ruta, "estado": "recibida", "creada": ahora(),
         "base": git("rev-parse", "--abbrev-ref", "HEAD", cwd=raiz),
         "vuelta": (padre.get("vuelta", 0) + 1) if (padre and origen == "supervisor") else 0,
         # Por qué esta tarea pasa siempre por el humano (se hereda en su cadena: supervisor, rechazos…).
         "siempre_humano": ("nace de un fallo en producción" if origen == "produccion"
                            else (padre.get("siempre_humano") if padre else False))}
    t["traza"] = traza.nueva(traza.de_tarea(padre) if padre else None)
    if misma_obra and padre:
        t.update(obra=padre["obra"], rama=padre["rama"], base=padre["base"])
    if padre and padre.get("motores_buenos"):
        t["motores_buenos"] = dict(padre["motores_buenos"])
    guardar(t)
    if padre:
        padre.setdefault("hijas", []).append(tid)
        guardar(padre)
    return t


def recorrer(t: dict) -> dict:
    """Lleva la tarea tan lejos como pueda; al soltarla (acabe, espere a una persona o se pare) manda su span."""
    try:
        return _recorrer(t)
    finally:
        traza.cerrar_tarea(t)


def _recorrer(t: dict) -> dict:
    reg = registro.construir()
    if t.get("modo") == "asdd":
        t["modo"] = "spec"
    # Una tarea que va contra una decisión ya registrada no es un ajuste: pasa a modo con spec.
    if (CFG.contradiccion_a_spec and t.get("modo") == "ajuste" and "choque_revisado" not in t
            and t.get("origen") not in ("supervisor", "produccion")):
        t["choque"] = choque_con_decisiones(t)
        t["choque_revisado"] = True
        if t["choque"]:
            t["modo"] = "spec"
            log(t, f"choca con una decisión ya tomada ({t['choque'].get('titulo') or '?'}): pasa a modo con spec")
        guardar(t)
    d = encargado(t, reg, t.get("ruta_pedida"))
    log(t, f"encargado: {d['accion']} · {d['motivo']}")

    if d["accion"] == "usar":
        # Se decide una vez por tarea; al reanudar (p. ej. tras aprobar una factoría nueva) se reaplica.
        if not t.get("extras_revisados"):
            t["extras"] = encargado_extras(t, d, reg)
            t["extras_revisados"] = True
            if t.get("ruta_pedida"):
                t["activar"] = [f for f in t["ruta_pedida"] if f in d["ruta"]]     # --ruta manda
        # Las factorías del proyecto no son una cadena fija: cada tarea levanta las que necesita, en su orden.
        d["factorias_proyecto"] = list(d["ruta"])
        activas = t.get("activar") or d["ruta"][:1]
        if activas != d["ruta"]:
            no_activas = [f for f in d["ruta"] if f not in activas]
            d["ruta"] = list(activas)
            # Factorías de test: la propia de cada factoría activa, las que declara su factoria.toml y las del
            # proyecto que no sean la propia de una factoría que esta tarea no levanta.
            tests = list(dict.fromkeys(d["ruta"] + d.get("tambien_casan", [])))
            for f in d["ruta"]:
                tests += [x for x in factorias_por_nombre(reg).get(f, {}).get("tests", []) if x not in tests]
            tests += [x for x in d["tests"] if x not in tests and x not in no_activas]
            d["tests"] = tests
            log(t, f"encargado: esta tarea levanta {' → '.join(d['ruta'])} (no {', '.join(no_activas)})"
                   f" · factorías de test {', '.join(d['tests'])}")
            escribir(TAREAS / t["id"] / "decision.json", d)
            guardar(t)
        nombres_f = {f["nombre"] for f in reg["factorias"]}
        nombres_t = {x["nombre"] for x in reg["tests"]}
        extras = [{**e, "existe": e["nombre"] in (nombres_f if e["tipo"] == "factoria" else nombres_t)}
                  for e in t.get("extras", [])]
        if extras:
            d["extras"] = extras
            log(t, "encargado: esta tarea necesita además " + "; ".join(
                f"{e['tipo']} {e['nombre']} ({e['alcance']}{'' if e['existe'] else ', nueva'})" for e in extras))
            nuevas = [e for e in extras if not e["existe"]]
            if nuevas:
                # Lo que no existe lo diseña el planificador (con su factoría de test) y el humano lo aprueba.
                d.update(accion="construir", faltan=[f"{e['tipo']}:{e['nombre']} — {e['por_que']}" for e in nuevas],
                         motivo="la tarea necesita factorías que aún no existen")
            for e in [e for e in extras if e["existe"]]:
                if e["tipo"] == "factoria":
                    d["ruta"].append(e["nombre"])
                    for x in [e["nombre"]] + next((f.get("tests", []) for f in reg["factorias"] if f["nombre"] == e["nombre"]), []):
                        if x not in d["tests"]:
                            d["tests"].append(x)
                elif e["nombre"] not in d["tests"]:
                    d["tests"].append(e["nombre"])
            permanentes = [e for e in extras if e["existe"] and e["alcance"] == "permanente"]
            if permanentes:
                perfil = perfil_leer(Path(t["raiz"])) or PERFIL_DEFECTO
                conjunto = list(dict.fromkeys(d["factorias_proyecto"] + [e["nombre"] for e in permanentes if e["tipo"] == "factoria"]))
                perfil_guardar(Path(t["raiz"]), {"ruta": conjunto, "tests": list(dict.fromkeys(perfil["tests"] + d["tests"]))},
                               "Añadidas de forma permanente: " + ", ".join(e["nombre"] for e in permanentes))
            escribir(TAREAS / t["id"] / "decision.json", d)
            guardar(t)

    if d["accion"] == "construir":
        t["estado"] = "planificando"
        guardar(t)
        res = lanzar_agente(t, "planificador", "cadenas", receta_de(catalogo("planificador.md")),
                            extra=f"- Lo que falta según el encargado: {d.get('faltan') or 'una cadena para este proyecto'}\n")
        # Una factoría sin su factoría de test no se da por creada: se crean juntas, siempre.
        cojas = factorias_sin_test()
        if cojas:
            t["estado"] = "planificacion_incompleta"
            guardar(t)
            avisar(t, "El planificador dejó factorías sin su factoría de test",
                   f"Sin test propio: {', '.join(cojas)}. Cada stack tiene su factoría y su factoría de test; "
                   f"relanza con `kumi reanudar {t['id']}` para que las complete.")
            return t
        t["estado"] = "esperando_aprobacion_cadena"
        guardar(t)
        avisar(t, "Cadena nueva diseñada: revísala",
               f"{res.get('resumen', '')}\n\nInforme: {TAREAS / t['id']}\n\nAprueba con `kumi aprobar-cadena <nombre>` "
               f"y relanza con `kumi reanudar {t['id']}`.")
        return t
    if d["accion"] == "esperar_aprobacion":
        t["estado"] = "esperando_aprobacion_cadena"
        guardar(t)
        avisar(t, "Tarea parada: cadena sin aprobar", f"{d['provisionales']}: {d['motivo']}")
        return t

    factorias = {f["nombre"]: f for f in reg["factorias"]}
    plaza_p = coger_plaza(clave_proyecto(Path(t["raiz"])), 1, t["id"])
    try:
        preparar_obra(t)
        # 1. Cadena de montaje: cada factoría de la ruta, en orden.
        t["estado"] = "produciendo"
        guardar(t)
        for nombre in d["ruta"]:
            f = factorias[nombre]
            plaza_f = coger_plaza(f"factoria-{nombre}", f.get("cupo", 2), t["id"])
            try:
                oficio = Path(f["carpeta"]) / "oficio.md"
                res = lanzar_agente(t, "factoria", nombre, receta_de(catalogo("modos", f"{t['modo']}.md"), oficio),
                                    necesita=f.get("necesita", []))
                if res.get("estado") == "ascender":
                    log(t, f"{nombre}: pasa a modo con spec ({res.get('resumen', '')})")
                    t["modo"] = "spec"
                    previo = TAREAS / t["id"] / t["pasos"][-1]["paso"] / "ajuste.md"
                    res = lanzar_agente(t, "factoria", nombre, receta_de(catalogo("modos", "spec.md"), oficio),
                                        extra=f"- Viene de modo ajuste: parte de `{previo}`\n",
                                        necesita=f.get("necesita", []))
                # Un «ok» sin ningún cambio en el repo no es de fiar (un modelo pequeño puede escribir en otra
                # carpeta): se pasa el trabajo al siguiente motor de la lista, si lo hay.
                desde = 1
                while (res.get("estado") == "ok" and not cambio_en_obra(t)
                       and len(CFG.elecciones("factoria", nombre, perfil_leer(Path(t["raiz"])))) > desde):
                    log(t, f"{nombre}: dice ok pero el repo no ha cambiado; lo repite el siguiente motor de la lista")
                    res = lanzar_agente(t, "factoria", nombre, receta_de(catalogo("modos", f"{t['modo']}.md"), oficio),
                                        necesita=f.get("necesita", []), desde=desde)
                    desde += 1
                if res.get("estado") == "ok" and cambio_en_obra(t):
                    ult = t["pasos"][-1]
                    t.setdefault("motores_buenos", {})[f"factoria:{nombre}"] = {"motor": ult["motor"], "modelo": ult["modelo"]}
            finally:
                plaza_f.unlink(missing_ok=True)
            if res.get("estado") != "ok":
                t["estado"] = "parada"
                guardar(t)
                mid = omoi_decision(t, f"Kumi: tarea parada en la factoría {nombre}",
                              f"[blocking] {res.get('resumen', '')}\nTarea: «{t['texto'][:300]}». Informe en {TAREAS / t['id']}.",
                              [f"factoria:{nombre}"])
                avisar(t, f"Tarea parada en la factoría {nombre}", res.get("resumen", "") + ver_memoria(mid))
                return t
            t["bloqueantes"] = t.get("bloqueantes", 0) + int(res.get("bloqueantes") or 0)

        # 1b. Riesgo temprano (código, coste cero): no adelanta la aprobación humana, que sigue al final con todo
        #     en verde; solo decide qué pruebas extra pasa el cambio. Nivel 2 → el refuerzo de kumi.toml.
        tests_reg = {x["nombre"]: x for x in reg["tests"]}
        obra = Path(t["obra"])
        if git("status", "--porcelain", cwd=obra):
            git("add", "-A", cwd=obra)
            git("commit", "-m", f"Kumi {t['id']}: cambios de la factoría", cwd=obra)
        temprano = riesgo.evaluar(obra, t["base"], d["ruta"] + d.get("tambien_casan", []))
        t["riesgo_temprano"] = temprano
        if temprano["nivel"] >= 2:
            refuerzo = [x for x in CFG.nivel2_tests if x in tests_reg and x not in d["tests"]]
            sin_cadena = [x for x in CFG.nivel2_tests if x not in tests_reg]
            d["tests"] += refuerzo
            log(t, f"riesgo nivel 2 tras la factoría ({'; '.join(temprano['motivos'][:3])})"
                   + (f": se refuerza con {', '.join(refuerzo)}" if refuerzo else ": sin refuerzo configurado")
                   + (f" (no existen: {', '.join(sin_cadena)})" if sin_cadena else ""))
            escribir(TAREAS / t["id"] / "decision.json", d)

        # 2. Cadenas de test.
        t["estado"] = "probando"
        t["tests_tarea"] = list(d["tests"])
        guardar(t)
        avisar_mismo_modelo(t, d, reg)
        resultados = []
        saltados = tests_que_no_tocan(t, d, factorias)
        if saltados:
            log(t, "tests que no tocan para este cambio: " + "; ".join(f"{n} ({m})" for n, m in saltados.items()))
            t["tests_saltados"] = saltados
        def una_cadena(nombre: str) -> tuple[str, dict]:
            c = tests_reg[nombre]
            plaza_t = coger_plaza(f"test-{nombre}", c.get("cupo", 1), t["id"])
            try:
                return nombre, lanzar_agente(
                    t, c.get("papel", "test"), nombre, receta_de(catalogo("tests", "comun.md"), Path(c["carpeta"]) / "oficio.md"),
                    extra=f"- Lo que dejó producción para probar: carpetas de los pasos anteriores en `{TAREAS / t['id']}`\n"
                          "- Las demás cadenas de test de esta tarea corren a la vez que tú sobre la misma obra: no toques el "
                          "código ni el árbol de git, y lo que crees (servidores, puertos, carpetas) que sea tuyo.\n",
                    necesita=c.get("necesita", []))
            except Exception as ex:  # noqa: BLE001
                return nombre, {"estado": "fallo", "resumen": f"la cadena se cayó: {ex}", "hallazgos": [], "bloqueantes": 0}
            finally:
                plaza_t.unlink(missing_ok=True)

        # Las cadenas de test no dependen unas de otras: van a la vez (cada una con su plaza y su cupo).
        lista = [n for n in d["tests"] if n not in saltados]
        if lista:
            log(t, f"cadenas de test a la vez: {', '.join(lista)}")
            with ThreadPoolExecutor(max_workers=len(lista)) as hilos:
                resultados = list(hilos.map(una_cadena, lista))
    finally:
        plaza_p.unlink(missing_ok=True)

    # 3. Supervisor (código): ¿pasan los tests?
    graves = [(n, h) for n, r in resultados for h in r.get("hallazgos", []) if h.get("gravedad") in ("alta", "media")]
    caidos = [n for n, r in resultados if r.get("estado") != "ok"]
    if graves or caidos:
        t["estado"] = "rechazada_supervisor"
        guardar(t)
        if t.get("vuelta", 0) >= MAX_VUELTAS_SUPERVISOR:
            mid = omoi_decision(t, f"Kumi: {t['id']} no pasa los tests tras {t['vuelta'] + 1} vueltas",
                                "[blocking] " + "; ".join(f"{n}: {h.get('descripcion')}" for n, h in graves[:8]), ["kumi"])
            avisar(t, "El supervisor no consigue que pase los tests",
                   f"{len(graves)} hallazgos tras {t['vuelta'] + 1} vueltas. Revisa {TAREAS / t['id']}." + ver_memoria(mid))
            return t
        texto = (f"Corrige lo que encontraron las cadenas de test en la tarea {t['id']} (texto original: «{t['texto']}»).\n\n"
                 + "\n".join(f"- [{h.get('gravedad')}] ({n}{', ' + h['config'] if h.get('config') else ''}) "
                             f"{h.get('descripcion')} — evidencia: {h.get('evidencia')}" for n, h in graves)
                 + (f"\n- Cadenas de test caídas: {', '.join(caidos)}" if caidos else ""))
        hija = nueva_tarea(Path(t["raiz"]), texto, "supervisor", t, t["modo"], d["ruta"], misma_obra=True)
        log(t, f"supervisor: no pasa → tarea nueva {hija['id']}")
        return recorrer(hija)

    # 4. Commit y puerta de riesgo.
    obra = Path(t["obra"])
    if git("status", "--porcelain", cwd=obra):
        git("add", "-A", cwd=obra)
        git("commit", "-m", f"Kumi {t['id']}: cambios pendientes de la factoría", cwd=obra)
    # En un repo mixto cuentan las reglas de todas las factorías que casan, no solo la que produce.
    r = riesgo.evaluar(obra, t["base"], d["ruta"] + d.get("tambien_casan", []))
    t["riesgo"] = r
    motivos_pr = []
    if r["nivel"] >= 2:
        motivos_pr.append("riesgo nivel 2: " + "; ".join(r["motivos"][:5]))
    abiertas = bloqueantes_abiertas(t)
    if abiertas or t.get("bloqueantes"):
        n = len(abiertas) or t["bloqueantes"]
        motivos_pr.append(f"{n} decisiones [blocking] abiertas en Omoikane: "
                          + "; ".join(m.get("title", "") for m in abiertas[:4]))
    if t.get("siempre_humano"):
        motivos_pr.append(t["siempre_humano"] if isinstance(t["siempre_humano"], str) else f"esta tarea siempre pasa por {CFG.humano}")
    receta, origen_receta = receta_publicar(t, factorias[d["ruta"][0]])
    if receta.get("provisional"):
        motivos_pr.append(f"la receta de publicación ({origen_receta}) es provisional")
    if motivos_pr:
        t.update(estado="pr_humana", motivos_pr=motivos_pr)
        t["decision_pr"] = omoi_decision(
            t, f"Kumi: aprobar la publicación de «{t['texto'][:70]}»",
            "[blocking] PR humana pendiente.\n" + "\n".join(f"- {m}" for m in motivos_pr)
            + f"\nRama {t['rama']} en {t['raiz']}. Aprobar: kumi aprobar {t['id']} · Cambios: kumi rechazar {t['id']} \"…\"",
            ["pr-humana"])
        guardar(t)
        avisar(t, "PR humana: revisa y aprueba",
               "\n".join(f"- {m}" for m in motivos_pr)
               + f"\n\nRama `{t['rama']}`. Diff: `git -C \"{t['raiz']}\" diff {t['base']}...{t['rama']}`\n\n"
               f"Aprobar: `kumi aprobar {t['id']}` · Pedir cambios: `kumi rechazar {t['id']} \"…\"`"
               + ver_memoria(t["decision_pr"]), tipo="pr_humana")
        return t
    return desplegar(t, factorias[d["ruta"][0]])


# --- Despliegue -----------------------------------------------------------------------

class CIFallida(Exception):
    pass


CI_ESPERA_MAX = 45 * 60        # una build de CI larga (Android firmado, Rust multiplataforma)


def escuchar_ci(t: dict, raiz: Path, sha: str) -> dict:
    """Espera a las acciones automáticas lanzadas por este commit y devuelve cómo acabaron.

    GitHub Actions con `gh`, GitLab CI con `glab`. Sin CI en el repo, no hay nada que escuchar.
    """
    remoto = git("remote", "get-url", "origin", cwd=raiz, ok=True) or ""
    workflows = any(raiz.glob(".github/workflows/*.y*ml"))
    # GitHub lanza acciones propias aunque el repo no tenga workflows (GitHub Pages: «pages build and
    # deployment»). Lo vimos en la primera tarea real de Kumi (una web en GitHub Pages): sin esperarlas,
    # Kumi daba por publicado algo que GitHub aún estaba desplegando.
    if "github.com" in remoto or workflows:
        sistema = "GitHub Actions"
    elif (raiz / ".gitlab-ci.yml").exists():
        sistema = "GitLab CI"
    else:
        return {"estado": "sin_ci", "sistema": None, "ejecuciones": []}
    log(t, f"escuchando {sistema} del commit {sha[:8]}…")
    limite = time.time() + CI_ESPERA_MAX
    # Sin workflows propios, si en 2 minutos no aparece ninguna acción es que no hay nada que esperar.
    sin_acciones_hasta = time.time() + (120 if not workflows and sistema == "GitHub Actions" else CI_ESPERA_MAX)
    vistas: list[dict] = []
    while time.time() < limite:
        if sistema == "GitHub Actions":
            r = subprocess.run(["gh", "run", "list", "--commit", sha, "--json",
                                "databaseId,name,status,conclusion,url"],
                               cwd=raiz, capture_output=True, text=True, encoding="utf-8")
            vistas = [{"id": x["databaseId"], "nombre": x["name"], "estado": x["status"],
                       "conclusion": x.get("conclusion") or "", "url": x.get("url")}
                      for x in (json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else [])]
        else:
            r = subprocess.run(["glab", "ci", "list", "--sha", sha, "-F", "json"],
                               cwd=raiz, capture_output=True, text=True, encoding="utf-8")
            vistas = [{"id": x["id"], "nombre": f"pipeline {x['id']}", "estado": x["status"],
                       "conclusion": x["status"], "url": x.get("web_url")}
                      for x in (json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else [])]
        # Las acciones tardan unos segundos en aparecer tras el push.
        en_marcha = [v for v in vistas if v["estado"] not in ("completed", "success", "failed", "canceled", "skipped")]
        if vistas and not en_marcha:
            break
        if not vistas and time.time() > sin_acciones_hasta:
            log(t, f"{sistema}: este commit no ha lanzado ninguna acción")
            return {"estado": "sin_ci", "sistema": sistema, "ejecuciones": []}
        time.sleep(30)
    else:
        return {"estado": "fallo", "sistema": sistema, "ejecuciones": vistas,
                "registro": f"sin terminar tras {CI_ESPERA_MAX // 60} min"}
    malas = [v for v in vistas if v["conclusion"] not in ("success", "skipped", "neutral")]
    res = {"estado": "fallo" if malas else "ok", "sistema": sistema, "ejecuciones": vistas}
    if malas and sistema == "GitHub Actions":
        r = subprocess.run(["gh", "run", "view", str(malas[0]["id"]), "--log-failed"],
                           cwd=raiz, capture_output=True, text=True, encoding="utf-8", errors="replace")
        res["registro"] = r.stdout[-6000:]
    log(t, f"{sistema}: {res['estado']} ({len(vistas)} ejecuciones)")
    t["ci"] = res
    guardar(t)
    return res

def slug_proyecto(raiz: Path) -> str:
    url = git("remote", "get-url", "origin", cwd=raiz, ok=True) or ""
    nombre = url.rstrip("/").removesuffix(".git").split("/")[-1] if url else raiz.name
    return re.sub(r"[^A-Za-z0-9_-]+", "-", nombre).lower()


def detectar_receta(raiz: Path) -> dict | None:
    """Primera vez en un proyecto: busca su propio ritual de publicación. Nace provisional."""
    r: dict = {"provisional": True, "merge": True, "push": True, "pasos": [], "prod_tests": [], "aviso_cliente": False}
    encontrado = False
    for fichero, code, name in [("gradle.properties", r"appVersionCode=(\d+)", r"appVersionName=([\w.\-]+)"),
                                ("app/build.gradle.kts", r"versionCode\s*=\s*(\d+)", r'versionName\s*=\s*"([^"]+)"'),
                                ("app/build.gradle", r"versionCode\s+(\d+)", r"versionName\s+['\"]([^'\"]+)['\"]"),
                                # Rust: solo nombre, sin código. Publicar en crates.io/Releases lo decide su receta.
                                ("Cargo.toml", None, r'(?m)^version\s*=\s*"([^"]+)"')]:
        f = raiz / fichero
        if f.exists() and re.search(code or name, f.read_text(encoding="utf-8", errors="replace")):
            r["version"] = {"fichero": fichero, "code": code, "name": name}
            encontrado = True
            break
    if (raiz / "scripts" / "publicar_release.py").exists():
        r["pasos"] = ['python scripts/publicar_release.py --notas "{notas}"']
        encontrado = True
    return r if encontrado else None


def receta_publicar(t: dict, factoria: dict) -> tuple[dict, str]:
    RECETAS.mkdir(parents=True, exist_ok=True)
    f = RECETAS / f"{slug_proyecto(Path(t['raiz']))}.toml"
    if f.exists():
        return tomllib.loads(f.read_text(encoding="utf-8")), str(f)
    detectada = detectar_receta(Path(t["raiz"]))
    if detectada:
        f.write_text(toml_simple(detectada), encoding="utf-8")
        log(t, f"receta de publicación detectada y guardada (provisional): {f}")
        return detectada, str(f)
    return factoria.get("publicar", {"merge": True, "push": False, "pasos": []}), f"factoría {factoria['nombre']}"


def toml_simple(d: dict, prefijo: str = "") -> str:
    lineas, tablas = [], []
    for k, v in d.items():
        if isinstance(v, dict):
            tablas.append((k, v))
        else:
            lineas.append(f"{k} = {json.dumps(v, ensure_ascii=False)}")
    texto = "\n".join(lineas) + "\n"
    for k, v in tablas:
        texto += f"\n[{k}]\n" + "\n".join(f"{kk} = {json.dumps(vv, ensure_ascii=False)}" for kk, vv in v.items()) + "\n"
    return texto


def subir_version(obra: Path, v: dict, menor: bool) -> str:
    f = obra / v["fichero"]
    texto = f.read_text(encoding="utf-8")
    mc = re.search(v["code"], texto) if v.get("code") else None
    mn = re.search(v["name"], texto)
    partes = mn.group(1).split(".")
    if menor:
        # Minor: 2.21.3 → 2.22.0, o 2.22 si `[versiones] minor_sin_cero` (hay quien escribe los minor sin «.0»).
        partes = [partes[0], str(int(partes[1]) + 1 if len(partes) > 1 else 1)]
        if not CFG.minor_sin_cero:
            partes.append("0")
    elif len(partes) < 3:
        partes = partes + ["1"]
    else:
        partes[-1] = str(int(partes[-1]) + 1)
    nombre = ".".join(partes)
    # De atrás adelante para no mover los índices.
    cambios = [(mn, nombre)] + ([(mc, str(int(mc.group(1)) + 1))] if mc else [])
    for m, nuevo in sorted(cambios, key=lambda x: -x[0].start(1)):
        texto = texto[:m.start(1)] + nuevo + texto[m.end(1):]
    f.write_text(texto, encoding="utf-8", newline="")
    return nombre


def correr_prod_tests(raiz: Path, receta: dict, perfil: dict) -> list[str]:
    """Las comprobaciones de la receta contra lo publicado. Devuelve los fallos (vacío = todo bien)."""
    fallos = []
    for prueba in (receta.get("prod_tests", []) if perfil.get("prod_tests", True) else []):
        prueba = humanizar_texto(prueba)
        r = subprocess.run(prueba, shell=True, cwd=raiz, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode:
            fallos.append(f"{prueba}: {(r.stderr or r.stdout).strip()[-400:]}")
    return fallos


def rollback(t: dict, receta: dict, motivo: str) -> dict:
    """Vuelve producción a como estaba antes de esta tarea.

    Con `git revert` de lo fusionado (nunca reescribe la historia ni hace push forzado), push, escucha
    de la CI y prod tests otra vez para confirmar que lo anterior vuelve a funcionar. En una app que
    se publica con pasos (una tienda propia, Play), el móvil solo acepta versiones más altas: se publica el código
    anterior con un número de versión nuevo. La receta puede desactivarlo con `rollback = "no"`.
    """
    raiz = Path(t["raiz"])
    res = {"cuando": ahora(), "motivo": motivo, "hecho": False}
    if receta.get("rollback", "revert") == "no":
        res["detalle"] = "la receta del proyecto no hace rollback"
        return res
    antes = t.get("base_antes")
    try:
        if not antes or git("rev-parse", "HEAD", cwd=raiz) == antes:
            raise RuntimeError("no hay nada fusionado que deshacer")
        if git("status", "--porcelain", "--untracked-files=no", cwd=raiz):
            raise RuntimeError("el repo principal tiene cambios sin commitear: no lo toco")
        if git("rev-parse", "--abbrev-ref", "HEAD", cwd=raiz) != t["base"]:
            raise RuntimeError(f"el repo principal ya no está en {t['base']}: no lo toco")
        f_version = raiz / receta["version"]["fichero"] if receta.get("version") else None
        publicada = f_version.read_bytes() if f_version and f_version.exists() else None
        if git("revert", "--no-edit", f"{antes}..HEAD", cwd=raiz, ok=True) is None:
            git("revert", "--abort", cwd=raiz, ok=True)
            raise RuntimeError("el revert choca con cambios posteriores")
        log(t, f"rollback: revertido {antes[:8]}..{t.get('version') or 'HEAD'}")
        if publicada is not None and receta.get("pasos"):
            # El revert también deshizo la subida de versión, y el móvil solo acepta versiones más altas
            # que la publicada (la rota): se parte de esa y se sube una más.
            f_version.write_bytes(publicada)
            nombre = subir_version(raiz, receta["version"], menor=False)
            git("commit", "-am", f"Versión {nombre} (vuelta a la versión anterior)", cwd=raiz)
            res["version"] = nombre
        if receta.get("push"):
            git("push", *(["origin", f"HEAD:{receta['push_a']}"] if receta.get("push_a") else []), cwd=raiz)
            ci = escuchar_ci(t, raiz, git("rev-parse", "HEAD", cwd=raiz))
            res["ci"] = ci["estado"]
            if ci["estado"] == "fallo":
                raise RuntimeError(f"la CI también falla con la versión anterior ({ci['sistema']})")
        for paso in (receta.get("pasos", []) if receta.get("rollback_pasos", True) else []):
            orden = paso.replace("{notas}", f"Vuelta a la versión anterior: {motivo[:150]}".replace('"', "'"))
            r = subprocess.run(orden, shell=True, cwd=raiz, capture_output=True, text=True, encoding="utf-8", errors="replace")
            (TAREAS / t["id"] / "despliegue.log").open("a", encoding="utf-8").write(f"$ [rollback] {orden}\n{r.stdout}\n{r.stderr}\n")
            if r.returncode:
                raise RuntimeError(f"falló «{orden}» al publicar la versión anterior")
        fallos = correr_prod_tests(raiz, receta, perfil_leer(raiz) or PERFIL_DEFECTO)
        res.update(hecho=True, prod_tests="fallan también" if fallos else "en verde",
                   detalle="producción ha vuelto a la versión anterior" + (" (pero sus prod tests también fallan)" if fallos else ""))
    except RuntimeError as e:
        res["detalle"] = f"rollback NO hecho: {e}"
    log(t, f"rollback: {res['detalle']}")
    t["rollback"] = res
    guardar(t)
    return res


def desplegar(t: dict, factoria: dict) -> dict:
    raiz, obra = Path(t["raiz"]), Path(t["obra"])
    receta, origen = receta_publicar(t, factoria)
    t["estado"] = "desplegando"
    guardar(t)
    try:
        if git("status", "--porcelain", "--untracked-files=no", cwd=raiz):
            raise RuntimeError("el repo principal tiene cambios sin commitear: no lo toco")
        actual = git("rev-parse", "--abbrev-ref", "HEAD", cwd=raiz)
        if actual != t["base"]:
            raise RuntimeError(f"el repo principal está en «{actual}», no en «{t['base']}»: no lo toco")
        if git("rebase", t["base"], cwd=obra, ok=True) is None:
            git("rebase", "--abort", cwd=obra, ok=True)
            raise RuntimeError(f"la rama no se puede poner encima de {t['base']} sin conflictos")
        if receta.get("version"):
            cadena = [cargar(i) if i != t["id"] else t for i in cadena_de_tareas(t)]
            menor = any(x.get("modo") in ("asdd", "spec") or x.get("funcionalidad_nueva") for x in cadena)
            nombre = subir_version(obra, receta["version"], menor=menor)
            git("commit", "-am", f"Versión {nombre}", cwd=obra)
            t["version"] = nombre
        if receta.get("merge", True):
            t["base_antes"] = git("rev-parse", "HEAD", cwd=raiz)
            guardar(t)
            git("merge", "--ff-only", t["rama"], cwd=raiz)
            log(t, f"merge en {t['base']}")
        if receta.get("push") and not git("remote", cwd=raiz, ok=True):
            log(t, "el repo aún no tiene remoto: no hay push (cuando lo tenga, la receta lo hará)")
        elif receta.get("push"):
            git("push", *(["origin", f"HEAD:{receta['push_a']}"] if receta.get("push_a") else []), cwd=raiz)
            log(t, "push hecho" + (f" (a origin/{receta['push_a']})" if receta.get("push_a") else ""))
            # Si el repo tiene CI, el despliegue de verdad lo hacen sus acciones: hay que escucharlas.
            ci = escuchar_ci(t, raiz, git("rev-parse", "HEAD", cwd=raiz))
            if ci["estado"] == "fallo":
                raise CIFallida(ci)
        # Notas: las de la tarea o de su cadena si alguien las fijó; si no, lo que hizo la primera factoría
        # de la cadena (la que hizo el cambio de verdad; las siguientes suelen ser arreglos de los tests).
        cadena = [cargar(i) if i != t["id"] else t for i in cadena_de_tareas(t)]
        notas = next((x["notas_publicacion"] for x in cadena if x.get("notas_publicacion")), None) or next(
            (p["resumen"] for x in reversed(cadena) for p in x.get("pasos", []) if p["papel"] == "factoria"), t["texto"][:200])
        for paso in receta.get("pasos", []):
            orden = humanizar_texto(paso.replace("{notas}", notas.replace('"', "'")))
            r = subprocess.run(orden, shell=True, cwd=raiz, capture_output=True, text=True, encoding="utf-8", errors="replace")
            (TAREAS / t["id"] / "despliegue.log").open("a", encoding="utf-8").write(f"$ {orden}\n{r.stdout}\n{r.stderr}\n")
            if r.returncode:
                raise RuntimeError(f"falló «{orden}» ({r.returncode})")
            log(t, f"despliegue: {orden}")
    except CIFallida as e:
        # Lo publicado ya está en el remoto y su CI ha fallado: error de producción → tarea nueva.
        ci = e.args[0]
        t.update(estado="ci_fallida", ci=ci)
        guardar(t)
        texto = (f"Las acciones automáticas ({ci['sistema']}) han fallado tras publicar la tarea {t['id']}:\n"
                 + "\n".join(f"- {x['nombre']}: {x['conclusion']} — {x.get('url', '')}" for x in ci["ejecuciones"])
                 + (f"\n\nRegistro del fallo:\n{ci.get('registro', '')[-3000:]}" if ci.get("registro") else ""))
        rb = rollback(t, receta, f"{ci['sistema']} falló tras publicar")
        if rb["hecho"]:
            texto += (f"\n\nProducción se ha devuelto a la versión anterior (rollback). Vuelve a aplicar el cambio de la tarea "
                      f"{t['id']} (rama {t['rama']}: «{t['texto'][:300]}») y corrige el fallo.")
        hija = nueva_tarea(raiz, texto, "produccion", t, "ajuste", t.get("ruta_pedida"))
        avisar(t, f"Ha fallado {ci['sistema']} tras publicar",
               f"{texto[:1500]}\n\nRollback: {rb['detalle']}\nTarea de arreglo: {hija['id']} (pasará por tu PR)")
        return recorrer(hija)
    except RuntimeError as e:
        t.update(estado="despliegue_fallido", error=str(e))
        guardar(t)
        avisar(t, "El despliegue no se ha hecho", f"{e}\n\nReceta: {origen}")
        return t

    # Prod tests: órdenes de la receta contra lo ya desplegado.
    fallos = []
    perfil = perfil_leer(raiz) or PERFIL_DEFECTO
    if not perfil.get("prod_tests", True) and receta.get("prod_tests"):
        log(t, f"prod tests: no, según el perfil del proyecto (fase {perfil.get('fase') or '-'})")
    fallos = correr_prod_tests(raiz, receta, perfil)
    resumen = f"Versión {t.get('version', '-')} · riesgo nivel {t['riesgo']['nivel']} · {t.get('usd', 0)} $"
    if fallos:
        t["estado"] = "prod_tests_fallidos"
        guardar(t)
        rb = rollback(t, receta, "fallaron los prod tests")
        texto = f"Arregla el fallo en producción tras la tarea {t['id']}:\n" + "\n".join(f"- {f}" for f in fallos)
        if rb["hecho"]:
            texto += (f"\n\nProducción se ha devuelto a la versión anterior (rollback). Vuelve a aplicar el cambio de la tarea "
                      f"{t['id']} (rama {t['rama']}: «{t['texto'][:300]}») y corrige el fallo.")
        hija = nueva_tarea(raiz, texto, "produccion", t, "ajuste", t.get("ruta_pedida"))
        avisar(t, "Han fallado los prod tests", f"{resumen}\n\n" + "\n".join(fallos)
               + f"\n\nRollback: {rb['detalle']}\nTarea de arreglo: {hija['id']} (pasará por tu PR)")
        return recorrer(hija)
    t["estado"] = "publicada"
    guardar(t)
    brain_recordar(t, "fact", f"Kumi publicó {t.get('version') or 'un cambio'}: {t['texto'][:80]}",
                   f"{ahora()[:10]}. {notas}\n{resumen}. Rama {t['rama']} fusionada en {t['base']}"
                   + (f"; {t['ci']['sistema']} en verde" if t.get("ci", {}).get("estado") == "ok" else "")
                   + f". Tarea {t['id']} (origen {t['origen']}).", ["publicado"])
    avisar(t, "Publicado y prod tests en verde" if receta.get("prod_tests") else "Publicado (sin prod tests en la receta)", resumen)
    if perfil.get("aviso_cliente") or receta.get("aviso_cliente"):
        avisar(t, "Cambio publicado", f"{t['texto'][:300]}\n\n{resumen}", cliente=True)
    return t


# --- Consultas y órdenes humanas ------------------------------------------------------

def estado() -> None:
    if not TAREAS.exists():
        print("Sin tareas.")
        return
    for c in sorted(TAREAS.iterdir()):
        f = c / "tarea.json"
        if f.exists():
            t = leer(f)
            padre = f" ← {t['padre']}" if t.get("padre") else ""
            print(f"{t['id']:<36} {t['estado']:<26} {t.get('usd', 0):>6} $  [{t['origen']}]{padre}  {t['texto'][:60]!r}")


def diagnostico() -> None:
    """`kumi config`: qué motor y modelo hace cada papel, qué no está instalado, qué factoría no podría
    trabajar con su motor y dónde construye y prueba la misma familia de modelos."""
    from .config import PAPELES_AGENTE, PAPELES_CONSULTA
    print(f"Configuración: {CFG.fichero or '(por defecto: no hay kumi.toml)'}")
    print(f"Quien aprueba: {CFG.humano} · proyectos nuevos en {CFG.proyectos} · memoria: {CFG.memoria}"
          f"{'' if CFG.memoria == 'ninguna' or shutil.which('omoi') else ' (¡omoi no está instalado!)'}")
    print(f"Android SDK: {CFG.android_sdk}{'' if CFG.android_sdk.exists() else ' (no existe)'}")
    print(f"Datos (tareas, obras, cadenas propias): {CFG.datos} · cachés de compilación: {CFG.compilacion}")
    print(f"Gasto: hoy {gasto_hoy():.2f} $ · tope diario {CFG.gasto_dia or 'sin tope'} · por tarea {CFG.gasto_tarea or 'sin tope'}")
    if CFG.notas:
        n = CFG.notas
        cuantas = len(list(n.glob("factorias/*.md")) + list(n.glob("tests/*.md"))) if n.exists() else 0
        print(f"Notas locales: {n}" + (f" ({cuantas} oficios con notas, {len(list(n.glob('recetas/*.toml')))} recetas)"
                                       if n.exists() else " (no existe)"))
    print()
    print("Papeles:")
    for papel in PAPELES_CONSULTA + PAPELES_AGENTE:
        els = CFG.elecciones(papel)
        estado_ = [f"{motores.describir(e)}{'' if motores.instalado(motores.motor(e['motor'])) else ' ✗ no instalado'}"
                   for e in els]
        print(f"  {papel:<13} {' → '.join(estado_)}")
    for k, els in CFG.por_nombre.items():
        print(f"  {k:<13} {' → '.join(motores.describir(e) for e in els)}")
    reg = registro.cadenas()
    print("\nFactorías y tests que no tendrían motor (les falta una capacidad o no hay nada instalado):")
    problemas = 0
    for papel, lista in (("factoria", reg["factorias"]), ("test", reg["tests"])):
        for c in lista:
            p_ = c.get("papel", papel)
            necesita = c.get("necesita", [])
            puede = [e for e in CFG.elecciones(p_, c["nombre"]) if motores.instalado(motores.motor(e["motor"]))
                     and motores.motor(e["motor"])["tipo"] not in ("api", "openai")
                     and all(n in motores.motor(e["motor"]).get("capacidades", []) for n in necesita)]
            if not puede:
                problemas += 1
                print(f"  {p_}:{c['nombre']} necesita {', '.join(necesita) or 'un agente'}; ninguna elección lo cumple")
    if not problemas:
        print("  ninguna")
    fam_f = {motores.familia(motores.efectiva(CFG.elecciones("factoria", f["nombre"]))) for f in reg["factorias"]}
    iguales = [x["nombre"] for x in reg["tests"] if motores.familia(motores.efectiva(CFG.elecciones(x.get("papel", "test"), x["nombre"]))) in fam_f]
    if iguales:
        print(f"\nAviso: {len(iguales)} factorías de test usan la misma familia de modelos que las factorías "
              f"({', '.join(sorted(fam_f))}). Funciona, pero se revisan con los mismos puntos ciegos.")
    print("\nCanales:")
    for tipo, lista in CFG.enrutado.items():
        print(f"  {tipo:<10} → {', '.join(lista) or '(ninguno: solo el fichero de la tarea)'}")


def orden_remota(orden: str, args: list[str], responder) -> None:
    """Lo que llega por un canal de ida y vuelta (ya comprobado que viene de un aprobador)."""
    yo = [sys.executable, "-m", "kumi"]
    try:
        if orden == "estado":
            filas = []
            for c in sorted(TAREAS.iterdir())[-10:] if TAREAS.exists() else []:
                if (c / "tarea.json").exists():
                    t = leer(c / "tarea.json")
                    filas.append(f"{t['id']} · {t['estado']} · {t['texto'][:50]}")
            responder("\n".join(filas) or "Sin tareas.")
        elif orden == "aprobar" and args:
            responder(f"Aprobada {args[0]}: publicando…")
            r = subprocess.run(yo + ["aprobar", args[0]], capture_output=True, text=True, encoding="utf-8", errors="replace")
            responder(f"{args[0]}: {(r.stdout or r.stderr).strip()[-500:]}")
        elif orden == "rechazar" and len(args) == 2:
            responder(f"Cambios pedidos en {args[0]}: Kumi se pone con ellos.")
            subprocess.Popen(yo + ["rechazar", args[0], args[1]])
        elif orden == "tarea" and len(args) == 2:
            proyecto = Path(args[0])
            if not proyecto.exists():
                candidatos = [p for p in CFG.proyectos.glob("*") if p.name.lower() == args[0].lower()]
                proyecto = candidatos[0] if candidatos else proyecto
            if not proyecto.exists():
                responder(f"No encuentro el proyecto «{args[0]}» (ni en {CFG.proyectos}).")
                return
            responder(f"Tarea recibida para {proyecto.name}. Te aviso por aquí cuando acabe o necesite algo.")
            subprocess.Popen(yo + ["tarea", "--proyecto", str(proyecto), "--origen", "canal", args[1]])
        else:
            responder("No entiendo la orden.")
    except Exception as e:  # noqa: BLE001
        responder(f"Error: {e}")


def main() -> None:
    for flujo in (sys.stdout, sys.stderr):
        flujo.reconfigure(encoding="utf-8")
    if sys.argv[1:2] == ["recepcion"]:          # sus propias opciones (--simular, --token)
        from . import recepcion
        recepcion.main(sys.argv[2:])
        return
    from . import __version__
    ap = argparse.ArgumentParser(prog="kumi", description="Kumi: la central y sus cadenas de montaje")
    ap.add_argument("--version", action="version", version=f"kumi {__version__}")
    sub = ap.add_subparsers(dest="orden", required=True)
    ini = sub.add_parser("init", help="crea ~/.kumi con un kumi.toml mínimo y dice qué falta instalar")
    ini.add_argument("--nombre", help="quien aprueba")
    ini.add_argument("--proyectos", help="carpeta de los proyectos nuevos")
    ini.add_argument("--si", action="store_true", help="sin preguntas, con los valores por defecto")
    sub.add_parser("recepcion", help="una pasada por las bases de Notion (--simular, --token)")
    sub.add_parser("gasto", help="lo gastado hoy y los topes")
    a = sub.add_parser("tarea")
    a.add_argument("texto")
    a.add_argument("--proyecto", required=True, type=Path)
    a.add_argument("--modo", choices=["ajuste", "spec", "asdd"], default="ajuste",
                   help="ajuste (por defecto) o spec (primero se acuerda el criterio); asdd = spec, nombre antiguo")
    a.add_argument("--ruta", help="factorías en orden, separadas por comas (opcional)")
    a.add_argument("--origen", default="directa")
    for n in ("ver", "aprobar", "reanudar", "limpiar"):
        sub.add_parser(n).add_argument("id")
    r = sub.add_parser("rechazar")
    r.add_argument("id")
    r.add_argument("comentario")
    sub.add_parser("aprobar-cadena").add_argument("nombre")
    nv = sub.add_parser("nuevo", help="crea un proyecto que aún no existe y encarga su primera versión")
    nv.add_argument("texto", help="qué quieres que sea")
    nv.add_argument("--nombre", help="nombre de la carpeta (si no, lo decide Kumi)")
    nv.add_argument("--stack", help="factoría (android, web, rust…); si no, la decide Kumi")
    nv.add_argument("--tipo", choices=["personal", "cliente"])
    nv.add_argument("--carpeta", type=Path, help="dónde crearlo (por defecto [rutas] proyectos / <nombre>)")
    pf = sub.add_parser("perfil", help="ver o cambiar las decisiones del proyecto (se guardan en su Omoikane)")
    pf.add_argument("--proyecto", required=True, type=Path)
    pf.add_argument("--omitir", help="tests extra que no le hacen falta, separados por comas ('' = ninguno)")
    pf.add_argument("--prod-tests", choices=["si", "no"])
    pf.add_argument("--aviso-cliente", choices=["si", "no"])
    pf.add_argument("--fase", help="alpha, beta, produccion… ('' = sin fase)")
    pf.add_argument("--tipo", help="personal, cliente… ('' = sin tipo)")
    pf.add_argument("--motor", action="append", metavar="PAPEL=MOTOR[:MODELO][,MOTOR…]",
                    help="motor y modelo de un papel solo en este proyecto, p. ej. factoria=codex:gpt-5 "
                         "o test:cyber=gemini-cli; 'PAPEL=' lo quita")
    sub.add_parser("config", help="qué motor hace cada papel, qué falta instalar y qué avisos hay")
    es = sub.add_parser("escuchar", help="recibe órdenes y aprobaciones por los canales de ida y vuelta (Telegram)")
    es.add_argument("--una-vez", action="store_true")
    s = sub.add_parser("asignar", help="vincula a mano un proyecto con su factoría (o factorías encadenadas)")
    s.add_argument("--proyecto", required=True, type=Path)
    s.add_argument("--ruta", required=True, help="factorías separadas por comas; la primera produce")
    s.add_argument("--tambien", default="", help="factorías cuyos tests también se pasan")
    sub.add_parser("aprobar-regla").add_argument("id")
    sub.add_parser("descartar-regla").add_argument("id")
    sub.add_parser("reglas", help="propuestas de regla pendientes y reglas de oficio aprobadas")
    sub.add_parser("usos", help="qué factorías usa cada proyecto y en qué proyectos se usa cada factoría")
    sub.add_parser("estado")
    sub.add_parser("registro")
    x = ap.parse_args()

    if x.orden == "tarea":
        t = nueva_tarea(x.proyecto.resolve(), x.texto, x.origen, modo=x.modo,
                        ruta=x.ruta.split(",") if x.ruta else None)
        t = recorrer(t)
        if t["estado"] not in ("planificando", "esperando_aprobacion_cadena", "planificacion_incompleta"):
            aprender(t, x.texto)
        print(json.dumps({"tarea": t["id"], "estado": t["estado"]}, ensure_ascii=False))
    elif x.orden == "estado":
        estado()
    elif x.orden == "ver":
        print(json.dumps(cargar(x.id), ensure_ascii=False, indent=2))
    elif x.orden == "aprobar":
        t = cargar(x.id)
        if t["estado"] != "pr_humana":
            raise SystemExit(f"La tarea está en «{t['estado']}», no esperando PR humana.")
        reg = registro.construir()
        f = next(f for f in reg["factorias"] if f["nombre"] == json.loads((TAREAS / t["id"] / "decision.json").read_text(encoding="utf-8"))["ruta"][0])
        log(t, f"PR humana: aprobada por {CFG.humano}")
        if t.get("decision_pr"):
            brain_cerrar(t, t["decision_pr"], f"{CFG.humano} aprobó la publicación")
            t["verificada"] = verificar(t["decision_pr"], t["raiz"], "aprobación de la PR")
        for m in bloqueantes_abiertas(t):        # aprobar la PR resuelve también lo que la paraba
            brain_cerrar(t, m["id"], f"resuelta al aprobar {CFG.humano} la PR humana")
        t = desplegar(t, f)
        print(t["estado"])
    elif x.orden == "rechazar":
        t = cargar(x.id)
        t["estado"] = "rechazada_humano"
        log(t, f"PR humana: rechazada ({x.comentario})")
        if t.get("decision_pr"):
            brain_cerrar(t, t["decision_pr"], f"{CFG.humano} pidió cambios: {x.comentario[:200]}")
        brain_recordar(t, "decision", f"{CFG.humano} pidió cambios en «{t['texto'][:60]}»", x.comentario, ["pr-humana"])
        hija = nueva_tarea(Path(t["raiz"]), f"Cambios pedidos por {CFG.humano} en la tarea {t['id']} («{t['texto'][:200]}»):\n{x.comentario}",
                           "humano", t, t["modo"], t.get("ruta_pedida"), misma_obra=True)
        aprender(hija, x.comentario)
        print(recorrer(hija)["estado"])
    elif x.orden == "reanudar":
        t = cargar(x.id)
        t.pop("tope_gasto", None)            # si se paró por gasto, se vuelve a mirar con los topes de ahora
        print(recorrer(t)["estado"])
    elif x.orden == "aprobar-cadena":
        hechos = []
        nombres = [x.nombre] if x.nombre != "todas" else sorted({p.parent.name for p in catalogo_glob("factorias/*/factoria.toml")}
                                                                  | {p.parent.name for p in catalogo_glob("tests/*/test.toml")})
        # Solo se aprueban las cadenas propias (las de serie ya vienen aprobadas).
        for f in [g for n in nombres for g in (DATOS / "factorias" / n / "factoria.toml", DATOS / "tests" / n / "test.toml")]:
            if f.exists():
                f.write_text(re.sub(r"(?m)^provisional\s*=\s*true", "provisional = false", f.read_text(encoding="utf-8")), encoding="utf-8")
                hechos.append(str(f))
        print("Aprobada: " + ", ".join(hechos) if hechos else f"No existe ninguna cadena «{x.nombre}».")
    elif x.orden == "asignar":
        raiz = Path(git("rev-parse", "--show-toplevel", cwd=x.proyecto.resolve()))
        reg = registro.construir()
        nombres = {f["nombre"] for f in reg["factorias"]}
        ruta, tambien = x.ruta.split(","), [f for f in x.tambien.split(",") if f]
        if faltan := [f for f in ruta + tambien if f not in nombres]:
            raise SystemExit(f"No existen: {', '.join(faltan)}")
        asignacion_guardar(raiz, ruta, tambien, f"Asignada a mano por {CFG.humano}", manual=True)
        print(f"{raiz.name} → {', '.join(ruta)}" + (f" (+ tests de {', '.join(tambien)})" if tambien else ""))
    elif x.orden == "nuevo":
        t = crear_proyecto(x.texto, x.nombre, x.stack, x.tipo, x.carpeta)
        if t["estado"] not in ("planificando", "esperando_aprobacion_cadena", "planificacion_incompleta"):
            aprender(t, x.texto)
        print(json.dumps({"tarea": t["id"], "proyecto": t["raiz"], "estado": t["estado"]}, ensure_ascii=False))
    elif x.orden == "perfil":
        raiz = Path(git("rev-parse", "--show-toplevel", cwd=x.proyecto.resolve()))
        cambios = {}
        lista = lambda s: [v for v in s.split(",") if v]  # noqa: E731
        if x.omitir is not None:
            # Las propias: las del perfil y cualquier factoría cuya huella case con el repo.
            fs = ficheros_repo(raiz)
            propias = set((perfil_leer(raiz) or PERFIL_DEFECTO)["ruta"]) | {
                f["nombre"] for f in registro.construir()["factorias"] if casa_huella(raiz, f.get("huella", {}), fs)}
            if choque := [v for v in lista(x.omitir) if v in propias]:
                raise SystemExit(f"No se puede omitir la factoría de test propia del stack: {', '.join(choque)}")
            cambios["tests_omitir"] = lista(x.omitir)
        if x.prod_tests:
            cambios["prod_tests"] = x.prod_tests == "si"
        if x.aviso_cliente:
            cambios["aviso_cliente"] = x.aviso_cliente == "si"
        for k in ("fase", "tipo"):
            if getattr(x, k) is not None:
                cambios[k] = getattr(x, k) or None
        if x.motor:
            papeles = dict((perfil_leer(raiz) or {}).get("papeles") or {})
            for asig in x.motor:
                papel, _, valor = asig.partition("=")
                if valor:
                    papeles[papel.strip()] = [v.strip() for v in valor.split(",") if v.strip()]
                else:
                    papeles.pop(papel.strip(), None)
            cambios["papeles"] = papeles
        p_ = perfil_guardar(raiz, cambios, f"Cambiado a mano por {CFG.humano}") if cambios else (perfil_leer(raiz) or PERFIL_DEFECTO)
        print(raiz.name)
        print(perfil_texto({**PERFIL_DEFECTO, **p_}))
    elif x.orden in ("aprobar-regla", "descartar-regla"):
        print(regla_resolver(x.id, x.orden == "aprobar-regla"))
    elif x.orden == "reglas":
        pendientes = propuestas_regla()
        print(f"Propuestas pendientes ({len(pendientes)}):")
        for m in pendientes:
            print(f"  {m['id'][:12]}  {m['title']}")
        try:
            aprobadas = json.loads(omoi("list", "--tag", "regla-oficio", "--all", "--json") or "[]")
        except json.JSONDecodeError:
            aprobadas = []
        print(f"Reglas de oficio aprobadas ({len(aprobadas)}):")
        for m in aprobadas:
            print(f"  {m['title']}")
    elif x.orden == "usos":
        perfiles = json.loads(omoi("list", "--tag", PERFIL_TAG, "--all", "--json") or "[]")
        por_factoria: dict[str, list[str]] = {}
        for m in perfiles:
            if not m.get("data"):
                continue
            pf_ = {**PERFIL_DEFECTO, **m["data"]}
            nombre = Path(m.get("project") or "?").name
            print(f"{nombre:<28} producción: {' → '.join(pf_['ruta']) or '-':<22} "
                  f"test: {', '.join(x for x in pf_['tests'] if x not in pf_['tests_omitir']) or '-'}")
            for f in pf_["ruta"] + [x for x in pf_["tests"] if x not in pf_["tests_omitir"]]:
                por_factoria.setdefault(f, []).append(nombre)
        print("\nQué proyectos usan cada factoría:")
        for f, ps in sorted(por_factoria.items()):
            print(f"  {f:<16} {', '.join(sorted(set(ps)))}")
    elif x.orden == "config":
        diagnostico()
    elif x.orden == "init":
        from .inicio import init
        init(x.nombre, x.proyectos, x.si)
    elif x.orden == "gasto":
        print(f"Hoy: {gasto_hoy():.2f} $ · tope diario: {CFG.gasto_dia or 'sin tope'} · tope por tarea: {CFG.gasto_tarea or 'sin tope'}")
    elif x.orden == "escuchar":
        canales.escuchar_telegram(orden_remota, una_vez=x.una_vez)
    elif x.orden == "registro":
        r = registro.construir()
        print(f"{len(r['piezas']['skills'])} skills · {len(r['factorias'])} factorías · {len(r['tests'])} cadenas de test")
    elif x.orden == "limpiar":
        t = cargar(x.id)
        if t.get("obra") and str(t["obra"]).startswith(str(OBRAS)) and Path(t["obra"]).exists():
            git("worktree", "remove", "--force", t["obra"], cwd=Path(t["raiz"]))
            print(f"Quitado {t['obra']}")


if __name__ == "__main__":
    main()
