"""Trazas de cada tarea en el estándar W3C Trace Context y OTLP, para ver qué pasa dentro de la central.

Cada tarea es una traza: un span «kumi.tarea» y, colgando de él, un span por paso (factoría, test,
revisor…). Al agente de cada paso se le pasa `TRACEPARENT` con el span de su paso, así que lo que
haga dentro (llamadas al modelo, herramientas, subagentes) cae debajo de ese paso. Claude Code lo
lee en `-p`; los demás motores lo heredan igual y lo usan si saben.

El `TRACEPARENT` va siempre, y la traza queda guardada en la tarea (`t["traza"]`) y en cada paso:
sirve para cruzar datos aunque no haya dónde mandarla (por ejemplo, Omoikane puede apuntarla).

Para mandar las trazas a un sitio, `[trazas] endpoint` en kumi.toml (o `OTEL_EXPORTER_OTLP_ENDPOINT`):
la URL base de un receptor OTLP/HTTP (un OpenTelemetry Collector, Jaeger, Phoenix, Tempo…). Con eso,
Kumi manda sus spans y activa la telemetría de Claude Code hacia el mismo sitio. Sin dependencias:
OTLP/HTTP en JSON con la biblioteca estándar. Si el receptor no contesta, se avisa una vez y se sigue:
una traza nunca para una tarea.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import secrets
import socket
import sys
import time
import urllib.parse
import urllib.request

from .config import CFG

# Estados de tarea que cuentan como error en su span.
ESTADOS_ERROR = {"parada", "rechazada_supervisor", "rechazada_humano", "prod_tests_fallidos", "fallo",
                 "planificacion_incompleta"}

_avisado = False


def _id(n_bytes: int) -> str:
    while True:
        h = secrets.token_hex(n_bytes)
        if h.strip("0"):          # el estándar no admite ids de solo ceros
            return h


def nuevo_span() -> str:
    return _id(8)


def traceparent(trace_id: str, span_id: str) -> str:
    return f"00-{trace_id}-{span_id}-01"


def nueva(padre: dict | None = None) -> dict:
    """La traza de una tarea nueva. Una tarea hija (vuelta del supervisor, rechazo, fallo en producción)
    sigue en la traza de su madre, colgando de su span de tarea."""
    if padre and padre.get("trace_id"):
        return {"trace_id": padre["trace_id"], "span_id": nuevo_span(), "padre_span": padre.get("span_id")}
    return {"trace_id": _id(16), "span_id": nuevo_span(), "padre_span": None}


def de_tarea(t: dict) -> dict:
    """La traza de la tarea; las tareas de antes de las trazas reciben una al reanudarse."""
    if not t.get("traza"):
        t["traza"] = nueva()
    return t["traza"]


def endpoint() -> str:
    return (CFG.trazas_endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip()


def _cabeceras() -> dict[str, str]:
    return {k: os.path.expandvars(str(v)) for k, v in (CFG.trazas_cabeceras or {}).items()}


def _atributos_recurso(d: dict) -> str:
    """Para OTEL_RESOURCE_ATTRIBUTES: clave=valor separados por comas, con los valores codificados."""
    return ",".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in d.items() if v not in (None, ""))


def entorno(t: dict, span_id: str, papel: str, nombre: str, paso: str) -> dict[str, str]:
    """Variables para el agente de un paso: siempre TRACEPARENT; con receptor, también la telemetría de
    Claude Code hacia él. No pisa lo que ya venga puesto en el entorno (salvo TRACEPARENT, que es del paso)."""
    tr = de_tarea(t)
    env = {"TRACEPARENT": traceparent(tr["trace_id"], span_id)}
    url = endpoint()
    if not url:
        return env
    propio = {"kumi.tarea": t["id"], "kumi.paso": paso, "kumi.papel": papel, "kumi.nombre": nombre,
              "kumi.proyecto": os.path.basename(t.get("raiz") or "")}
    previos = os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "")
    env["OTEL_RESOURCE_ATTRIBUTES"] = ",".join(x for x in (previos, _atributos_recurso(propio)) if x)
    defecto = {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_ENDPOINT": url,
    }
    cab = _cabeceras()
    if cab:
        defecto["OTEL_EXPORTER_OTLP_HEADERS"] = ",".join(f"{k}={urllib.parse.quote(v, safe='')}" for k, v in cab.items())
    if CFG.trazas_contenido:
        defecto.update(OTEL_LOG_USER_PROMPTS="1", OTEL_LOG_TOOL_DETAILS="1")
    for k, v in defecto.items():
        if not os.environ.get(k):
            env[k] = v
    return env


# --- Envío OTLP/HTTP JSON ----------------------------------------------------------------------

def _valor(v) -> dict:
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    return {"stringValue": str(v)}


def _atributos(d: dict) -> list[dict]:
    return [{"key": k, "value": _valor(v)} for k, v in d.items() if v is not None and v != ""]


def span(trace_id: str, span_id: str, padre: str | None, nombre: str, inicio_ns: int, fin_ns: int,
         atributos: dict, error: str | None = None) -> dict:
    s = {"traceId": trace_id, "spanId": span_id, "name": nombre, "kind": 1,
         "startTimeUnixNano": str(inicio_ns), "endTimeUnixNano": str(max(fin_ns, inicio_ns)),
         "attributes": _atributos(atributos),
         "status": {"code": 2, "message": error[:500]} if error is not None else {"code": 1}}
    if padre:
        s["parentSpanId"] = padre
    return s


def cuerpo(spans: list[dict]) -> dict:
    from . import __version__
    recurso = {"service.name": CFG.trazas_servicio or "kumi", "service.version": __version__,
               "host.name": socket.gethostname()}
    return {"resourceSpans": [{"resource": {"attributes": _atributos(recurso)},
                               "scopeSpans": [{"scope": {"name": "kumi", "version": __version__},
                                               "spans": spans}]}]}


def enviar(spans: list[dict]) -> bool:
    """Manda los spans al receptor, si hay. Nunca lanza: una traza no para una tarea."""
    global _avisado
    url = endpoint()
    if not url or not spans:
        return False
    url = url.rstrip("/")
    if not url.endswith("/v1/traces"):
        url += "/v1/traces"
    req = urllib.request.Request(url, data=json.dumps(cuerpo(spans)).encode(), method="POST",
                                 headers={"content-type": "application/json", **_cabeceras()})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return 200 <= r.status < 300
    except Exception as e:  # noqa: BLE001 — receptor caído, sin red, URL mal puesta: se avisa una vez
        if not _avisado:
            _avisado = True
            print(f"[kumi] no se pudieron mandar las trazas a {url}: {e}", file=sys.stderr)
        return False


def _ns(iso: str | None) -> int:
    try:
        return int(dt.datetime.fromisoformat(iso).timestamp() * 1e9) if iso else time.time_ns()
    except ValueError:
        return time.time_ns()


def paso(t: dict, span_id: str, papel: str, nombre: str, paso_id: str, inicio_ns: int, eleccion: dict,
         codigo: int, usd: float | None, turnos: int | None, res: dict, error_motor: str = "") -> None:
    tr = de_tarea(t)
    estado = res.get("estado")
    modelo = eleccion.get("modelo")
    atr = {"kumi.tarea": t["id"], "kumi.paso": paso_id, "kumi.papel": papel, "kumi.nombre": nombre,
           "kumi.motor": eleccion.get("motor"), "kumi.estado": estado, "kumi.codigo": codigo,
           "kumi.usd": usd, "kumi.turnos": turnos, "kumi.hallazgos": len(res.get("hallazgos") or []),
           "kumi.resumen": (res.get("resumen") or "")[:500], "kumi.error_motor": error_motor[:500],
           # Convenciones GenAI de OpenTelemetry, para los visores que las entienden.
           "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": f"{papel}:{nombre}",
           "gen_ai.request.model": modelo}
    error = None
    if estado == "fallo" or codigo != 0:
        error = res.get("resumen") or f"código {codigo}"
    enviar([span(tr["trace_id"], span_id, tr["span_id"], f"kumi.{papel} {nombre}", inicio_ns, time.time_ns(),
                 atr, error)])


def cerrar_tarea(t: dict) -> None:
    """El span de la tarea, del momento en que se creó a ahora. Se manda cada vez que la tarea suelta el
    control (acaba, espera a una persona, se para); al reanudarla se vuelve a mandar con el mismo id y el
    final nuevo, y los receptores que deduplican por id se quedan con el último."""
    tr = de_tarea(t)
    estado = t.get("estado")
    atr = {"kumi.tarea": t["id"], "kumi.proyecto": os.path.basename(t.get("raiz") or ""),
           "kumi.modo": t.get("modo"), "kumi.origen": t.get("origen"), "kumi.estado": estado,
           "kumi.usd": t.get("usd"), "kumi.pasos": len(t.get("pasos") or []), "kumi.padre": t.get("padre"),
           "kumi.texto": (t.get("texto") or "")[:500] if CFG.trazas_contenido else None}
    error = estado if estado in ESTADOS_ERROR else None
    enviar([span(tr["trace_id"], tr["span_id"], tr.get("padre_span"), "kumi.tarea", _ns(t.get("creada")),
                 time.time_ns(), atr, error)])
