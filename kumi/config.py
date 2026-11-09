"""Configuración de Kumi: quién aprueba, dónde viven las cosas, qué motor y modelo hace cada papel y por
qué canales habla. Todo lo que antes estaba escrito para una sola persona sale de aquí.

Se lee `~/.kumi/kumi.toml` (otra carpeta con la variable KUMI_HOME, u otro fichero con KUMI_CONFIG). Lo que no
esté toma el valor por defecto: todo con Claude Code. `kumi init` crea el fichero con los valores comentados.

Papeles:
    encargado, recepcion  → decisiones cortas sin herramientas (cualquier motor o una API directa)
    planificador, factoria, test, revisor, aprendiz, oficina → agentes que trabajan en una carpeta

Para un papel se da una LISTA de elecciones: la primera que funcione. Si un motor no está instalado, no
tiene lo que la factoría necesita o se cae sin dejar resultado, se pasa a la siguiente (respaldo).

Se puede afinar por factoría o test concreto (`[papeles_por_nombre]`, clave "factoria:android" o
"test:cyber") y por proyecto (perfil del proyecto en la memoria, campo `papeles`, mismo formato).
Prioridad: proyecto > nombre concreto > papel > defecto.
"""
from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parent          # el catálogo (factorías, tests, modos) viaja con el paquete
CASA = Path(os.path.expanduser(os.environ.get("KUMI_HOME") or "~/.kumi"))

PAPELES_CONSULTA = ("encargado", "recepcion")
PAPELES_AGENTE = ("planificador", "factoria", "test", "revisor", "aprendiz", "oficina")

DEFECTO_PAPELES = {
    "encargado": [{"motor": "claude-code", "modelo": "sonnet"}],
    "recepcion": [{"motor": "claude-code", "modelo": "sonnet"}],
    "aprendiz": [{"motor": "claude-code", "modelo": "sonnet"}],
    "planificador": [{"motor": "claude-code"}],
    "factoria": [{"motor": "claude-code"}],
    "test": [{"motor": "claude-code"}],
    "revisor": [{"motor": "claude-code", "modelo": "opus"}],
    "oficina": [{"motor": "claude-code"}],
}


def _sdk_android() -> str:
    """El SDK de Android: variable de entorno o la carpeta por defecto de Android Studio en cada sistema."""
    for v in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(v):
            return os.environ[v]
    casa = Path.home()
    for c in (casa / "AppData" / "Local" / "Android" / "Sdk", casa / "Android" / "Sdk", casa / "Library" / "Android" / "sdk"):
        if c.exists():
            return str(c)
    return str(casa / "Android" / "Sdk")


@dataclass
class Config:
    humano: str = "la persona responsable"
    idioma: str = "es"
    proyectos: Path = field(default_factory=lambda: Path.home() / "proyectos")
    android_sdk: Path = field(default_factory=lambda: Path(_sdk_android()))
    avd_test: str = "kumi_test"
    memoria: str = "omoikane"                  # omoikane | ninguna
    papeles: dict = field(default_factory=lambda: {k: list(v) for k, v in DEFECTO_PAPELES.items()})
    por_nombre: dict = field(default_factory=dict)
    motores: dict = field(default_factory=dict)     # motores propios o ajustes de los de serie
    nivel2_tests: list = field(default_factory=lambda: ["revision"])
    max_vueltas_supervisor: int = 2
    contradiccion_a_spec: bool = True
    minor_sin_cero: bool = False
    canales: dict = field(default_factory=lambda: {"escritorio": {}})
    enrutado: dict = field(default_factory=lambda: {"aviso": ["escritorio"], "pr_humana": ["escritorio"],
                                                    "pregunta": ["escritorio"], "cliente": []})
    # Notas locales: lo que solo vale en esta máquina o para estos proyectos (sus webs, sus repos, sus rutas).
    # <notas>/factorias/<nombre>.md y <notas>/tests/<nombre>.md se añaden al oficio; <notas>/factorias/<nombre>.riesgo.toml
    # suma reglas de riesgo; <notas>/recetas guarda las recetas de publicación y <notas>/inventario.md los repos.
    notas: Path | None = None
    # Dónde van los cachés de compilación (target de cargo, etc.), fuera de los worktrees.
    compilacion: Path = field(default_factory=lambda: Path(os.environ.get("TEMP") or "/tmp") / "kumi-compilacion")
    # Dónde viven las tareas, las obras (worktrees), los cerrojos y el registro. Por defecto, ~/.kumi.
    datos: Path = field(default_factory=lambda: CASA)
    # Topes de gasto en dólares (0 = sin tope). Al pasarlos, la tarea se para y se avisa al humano.
    gasto_tarea: float = 0.0
    gasto_dia: float = 0.0
    fichero: Path | None = None

    def elecciones(self, papel: str, nombre: str | None = None, perfil: dict | None = None) -> list[dict]:
        """Lista de {motor, modelo} para un papel, de la preferida a la de respaldo."""
        claves = ([f"{papel}:{nombre}"] if nombre else []) + [papel]
        for fuente in ((perfil or {}).get("papeles") or {}, self.por_nombre, self.papeles):
            for k in claves:
                if fuente.get(k):
                    return _lista(fuente[k])
        return _lista(DEFECTO_PAPELES.get(papel, [{"motor": "claude-code"}]))


def _lista(v) -> list[dict]:
    """Acepta "codex", "codex:gpt-5", {motor, modelo} o una lista de cualquiera de ellos."""
    if isinstance(v, (str, dict)):
        v = [v]
    out = []
    for e in v:
        if isinstance(e, str):
            motor, _, modelo = e.partition(":")
            e = {"motor": motor, **({"modelo": modelo} if modelo else {})}
        out.append(dict(e))
    return out


def cargar(fichero: Path | None = None) -> Config:
    f = fichero or Path(os.environ.get("KUMI_CONFIG") or CASA / "kumi.toml")
    c = Config()
    if not f.exists():
        return c
    try:
        d = tomllib.loads(f.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        print(f"[kumi] {f} no se puede leer ({e}); uso la configuración por defecto", file=sys.stderr)
        return c
    c.fichero = f
    p = d.get("persona", {})
    c.humano = p.get("nombre", c.humano)
    c.idioma = p.get("idioma", c.idioma)
    r = d.get("rutas", {})
    if r.get("proyectos"):
        c.proyectos = Path(os.path.expandvars(os.path.expanduser(r["proyectos"])))
    if r.get("android_sdk"):
        c.android_sdk = Path(os.path.expandvars(os.path.expanduser(r["android_sdk"])))
    c.avd_test = r.get("avd_test", c.avd_test)
    if r.get("notas"):
        c.notas = Path(os.path.expandvars(os.path.expanduser(r["notas"])))
    if r.get("compilacion"):
        c.compilacion = Path(os.path.expandvars(os.path.expanduser(r["compilacion"])))
    if r.get("datos"):
        c.datos = Path(os.path.expandvars(os.path.expanduser(r["datos"])))
    g = d.get("gasto", {})
    c.gasto_tarea = float(g.get("por_tarea_usd", c.gasto_tarea))
    c.gasto_dia = float(g.get("por_dia_usd", c.gasto_dia))
    c.memoria = d.get("memoria", {}).get("motor", c.memoria)
    for k, v in d.get("papeles", {}).items():
        c.papeles[k] = _lista(v)
    c.por_nombre = {k: _lista(v) for k, v in d.get("papeles_por_nombre", {}).items()}
    c.motores = d.get("motores", {})
    rg = d.get("riesgo", {})
    c.nivel2_tests = rg.get("nivel2_tests", c.nivel2_tests)
    c.max_vueltas_supervisor = rg.get("max_vueltas_supervisor", c.max_vueltas_supervisor)
    c.contradiccion_a_spec = d.get("decisiones", {}).get("contradiccion_a_spec", c.contradiccion_a_spec)
    c.minor_sin_cero = d.get("versiones", {}).get("minor_sin_cero", c.minor_sin_cero)
    if "canales" in d:
        c.canales = d["canales"]
    if "enrutado" in d:
        c.enrutado = {**c.enrutado, **d["enrutado"]}
    return c


CFG = cargar()


def recetas_dir() -> Path:
    """Recetas de publicación por proyecto: en las notas locales si las hay; si no, en la carpeta de datos."""
    return (CFG.notas / "recetas") if CFG.notas else (CFG.datos / "recetas")


def humanizar_texto(texto: str) -> str:
    """Marcadores de los textos del catálogo: «{humano}» quien aprueba, «{kumi}» la carpeta del catálogo de
    serie, «{datos}» la carpeta de datos (tareas, cadenas propias), «{recetas}» las recetas de publicación y
    «{compilacion}» los cachés de compilación."""
    return (texto.replace("{humano}", CFG.humano).replace("{kumi}", RAIZ.as_posix())
            .replace("{datos}", CFG.datos.as_posix()).replace("{recetas}", recetas_dir().as_posix())
            .replace("{compilacion}", CFG.compilacion.as_posix()))


def catalogo(*partes: str) -> Path:
    """Un fichero del catálogo: el propio de esta instalación (en la carpeta de datos, donde el planificador
    crea las cadenas nuevas) manda sobre el de serie que viene con el paquete."""
    propio = CFG.datos.joinpath(*partes)
    return propio if propio.exists() else RAIZ.joinpath(*partes)


def catalogo_glob(patron: str) -> list[Path]:
    """Ficheros del catálogo que casan con el patrón (p. ej. «factorias/*/factoria.toml»), los propios primero;
    si una cadena está en los dos sitios, solo cuenta la propia."""
    vistos, out = set(), []
    for base in (CFG.datos, RAIZ):
        for f in sorted(base.glob(patron)):
            clave = f.relative_to(base).as_posix()
            if clave not in vistos:
                vistos.add(clave)
                out.append(f)
    return out


def nota_local(*partes: str) -> Path | None:
    """Fichero de las notas locales, si están configuradas y existe."""
    if CFG.notas is None:
        return None
    f = CFG.notas.joinpath(*partes)
    return f if f.exists() else None
