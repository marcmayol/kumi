"""Recorre una web como una persona: móvil, móvil con letra ampliada y escritorio.

Sirve para las dos cosas de la cadena de test web:
  - local: levanta la web (estática o Astro ya construida), la recorre y la para al acabar;
  - prod:  recorre la URL publicada y espera a que aparezca el contenido nuevo.

    uv run --with playwright python <kumi>/tests/web/navegar.py --estatica <repo> --salida <carpeta>
    uv run --with playwright python <kumi>/tests/web/navegar.py --astro <repo> --rutas "" es/ --salida <carpeta>
    uv run --with playwright python <kumi>/tests/web/navegar.py --url https://ejemplo.com/ --espera "Texto nuevo" --reintentar 600

Nunca envía formularios de verdad: los POST a otros dominios se contestan con un 200 falso y la
analítica se bloquea (ni correos al webhook ni visitas falsas en las estadísticas).

Deja en --salida: <config>/<nn>-<página>-pantalla.png (lo que se ve al abrir), -entera.png (la
página completa) e informe.json. Sale con código 1 si hay fallos graves (página que no responde 200,
error de JavaScript, error de consola, enlace o recurso propio roto, texto esperado ausente).
"""
from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

from playwright.sync_api import sync_playwright

# Chrome en Android aplica el tamaño de letra del sistema como zoom de página: a 1,5 la página
# ve un ancho CSS de 390/1,5 = 260 px. Se emula igual: menos ancho CSS y más densidad.
CONFIGS = {
    "movil": dict(viewport={"width": 390, "height": 844}, device_scale_factor=3, is_mobile=True, has_touch=True),
    "movil-130": dict(viewport={"width": 300, "height": 649}, device_scale_factor=3.9, is_mobile=True, has_touch=True),
    "movil-150": dict(viewport={"width": 260, "height": 563}, device_scale_factor=4.5, is_mobile=True, has_touch=True),
    "escritorio": dict(viewport={"width": 1440, "height": 900}, device_scale_factor=1),
}
ANALITICA = re.compile(r"googletagmanager\.com|google-analytics\.com|doubleclick\.net|clarity\.ms|hotjar|"
                       r"connect\.facebook\.net|plausible\.io|umami|cloudflareinsights", re.I)

# Lo que se mide en cada página, dentro del navegador.
MEDIR = """() => {
  const W = innerWidth, sel = e => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') +
    (e.className && typeof e.className === 'string' ? '.' + e.className.trim().split(/\\s+/).slice(0, 2).join('.') : '');
  const visible = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const todos = [...document.querySelectorAll('body *')].filter(visible);
  // Solo cuenta lo que se lee o se toca: un fondo decorativo más ancho que la pantalla no es un fallo.
  const conTexto = e => [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
  const util = e => conTexto(e) || /^(IMG|VIDEO|BUTTON|INPUT|SELECT|TEXTAREA|A)$/.test(e.tagName);
  const desborda = todos.filter(e => util(e) && e.getBoundingClientRect().right > W + 1 &&
    !e.closest('pre, code, table')).slice(0, 8).map(sel);
  // Texto que no cabe en su caja: recortado (overflow hidden, elipsis) o saliéndose por encima del borde.
  const noCabe = e => {
    const s = getComputedStyle(e);
    if (!e.textContent.trim() || e.children.length > 3 || /auto|scroll/.test(s.overflowX)) return false;
    if (e.closest('pre, code, table') || e.clientWidth <= 2 || e.clientHeight <= 2) return false;  // sr-only
    return e.scrollWidth > e.clientWidth + 1 || (/hidden|clip/.test(s.overflowY) && e.scrollHeight > e.clientHeight + 2);
  };
  const aProposito = e => { const s = getComputedStyle(e); return s.textOverflow === 'ellipsis' || s.webkitLineClamp !== 'none'; };
  const desc = e => sel(e) + ': «' + e.textContent.trim().slice(0, 40) + '»';
  const sinCaber = todos.filter(noCabe);
  const cortado = sinCaber.filter(e => !aProposito(e)).slice(0, 8).map(desc);
  const recortado = sinCaber.filter(aProposito).slice(0, 4).map(desc);
  const imgs = [...document.images];
  const rotas = imgs.filter(i => i.complete && i.naturalWidth === 0 && i.getAttribute('src')).map(i => i.currentSrc || i.src);
  const sinAlt = imgs.filter(i => !i.hasAttribute('alt')).map(i => i.currentSrc || i.src).slice(0, 8);
  const pequenos = matchMedia('(pointer: coarse)').matches ? [...document.querySelectorAll('a[href], button, input, select, textarea, [role=button]')]
    .filter(visible).filter(e => { const r = e.getBoundingClientRect(); return r.width < 24 || r.height < 24; })
    .slice(0, 8).map(e => sel(e) + ' ' + Math.round(e.getBoundingClientRect().width) + 'x' + Math.round(e.getBoundingClientRect().height)) : [];
  return {
    titulo: document.title, lang: document.documentElement.lang,
    viewport_meta: !!document.querySelector('meta[name=viewport]'),
    robots: document.querySelector('meta[name=robots]')?.content || '',
    canonical: document.querySelector('link[rel=canonical]')?.href || '',
    h1: document.querySelectorAll('h1').length,
    scroll_horizontal: document.documentElement.scrollWidth > W + 1,
    desborda, cortado, recortado_a_proposito: recortado, imagenes_rotas: rotas, sin_alt: sinAlt, objetivos_pequenos: pequenos,
    enlaces: [...document.querySelectorAll('a[href]')].map(a => a.href),
    texto: document.body.innerText,
  };
}"""


def puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def levantar(a) -> tuple[subprocess.Popen | None, str]:
    if a.url:
        return None, a.url
    p = puerto_libre()
    if a.estatica:
        orden = [sys.executable, "-m", "http.server", str(p), "--bind", "127.0.0.1", "--directory", str(a.estatica)]
        cwd = a.estatica
    else:
        # node directo, sin npm/npx por medio: un solo proceso que parar.
        # El ejecutable cambia de sitio entre versiones (astro.js en la 5, bin/astro.mjs en la 7).
        paquete = Path(a.astro) / "node_modules" / "astro"
        binario = json.loads((paquete / "package.json").read_text(encoding="utf-8"))["bin"]
        binario = binario["astro"] if isinstance(binario, dict) else binario
        orden = ["node", str(paquete / binario), "preview", "--host", "127.0.0.1", "--port", str(p)]
        cwd = a.astro
    proc = subprocess.Popen(orden, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{p}"
    for _ in range(60):
        try:
            urllib.request.urlopen(base + "/", timeout=2)
            break
        except urllib.error.HTTPError:
            break  # responde (aunque sea 404): está arriba
        except Exception:
            time.sleep(0.5)
    else:
        parar(proc)
        sys.exit(f"El servidor no arranca: {' '.join(orden)}")
    return proc, base + "/"


def parar(proc: subprocess.Popen | None) -> None:
    if not proc or proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.terminate()
    proc.wait(timeout=10)


def esperar_contenido(url: str, texto: str, segundos: int) -> bool:
    """GitHub Pages tarda 1-2 min y su CDN cachea hasta 10: se pide con un parámetro que rompe la caché."""
    fin = time.time() + segundos
    while True:
        try:
            sep = "&" if "?" in url else "?"
            html = urllib.request.urlopen(f"{url}{sep}kumi={int(time.time())}", timeout=15).read().decode("utf-8", "replace")
            if texto in html:
                return True
        except Exception:
            pass
        if time.time() > fin:
            return False
        time.sleep(15)


def comprobar_enlace(url: str) -> int | str:
    pet = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Kumi test web)"})
    try:
        return urllib.request.urlopen(pet, timeout=15).status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        return type(e).__name__


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", help="web ya servida (prod o un servidor que ya tengas levantado)")
    g.add_argument("--estatica", type=Path, help="carpeta a servir tal cual")
    g.add_argument("--astro", type=Path, help="repo Astro ya construido (npm run build hecho)")
    ap.add_argument("--rutas", nargs="*", default=[""], help="rutas a recorrer seguro, relativas a la base y sin barra inicial (es/, blog/es/); vacía = portada")
    ap.add_argument("--rastrear", type=int, default=25, help="páginas propias extra a descubrir por enlaces")
    ap.add_argument("--configs", nargs="*", default=list(CONFIGS), choices=list(CONFIGS))
    ap.add_argument("--espera", action="append", default=[], help="texto que debe aparecer en la primera ruta")
    ap.add_argument("--reintentar", type=int, default=0, help="segundos esperando a que aparezca --espera (prod)")
    ap.add_argument("--externos", action="store_true", help="comprobar también los enlaces a otros dominios")
    ap.add_argument("--salida", type=Path, default=Path("evidencias"))
    a = ap.parse_args()
    for flujo in (sys.stdout, sys.stderr):
        flujo.reconfigure(encoding="utf-8")

    if any(re.match(r"^[A-Za-z]:[/\\]", r) for r in a.rutas):
        sys.exit("Git Bash ha convertido una ruta en ruta de Windows: escríbelas sin barra inicial (es/, blog/es/).")
    proc, base = levantar(a)
    graves: list[str] = []
    informe = {"base": base, "paginas": {}, "enlaces_rotos": [], "graves": graves}
    try:
        origen = urlparse(base).netloc
        primera = urljoin(base, a.rutas[0].lstrip("/"))
        for t in a.espera:
            if a.reintentar and not esperar_contenido(primera, t, a.reintentar):
                graves.append(f"tras {a.reintentar} s no aparece «{t}» en {primera}")
        cola = [urljoin(base, r.lstrip("/")) for r in a.rutas]
        vistas, enlaces = [], set()
        with sync_playwright() as pw:
            nav = pw.chromium.launch()
            for nombre in a.configs:
                ctx = nav.new_context(locale="es-ES", **CONFIGS[nombre])

                def interceptar(route, req):
                    if ANALITICA.search(req.url):
                        return route.abort()
                    if req.method == "POST" and urlparse(req.url).netloc != origen:
                        return route.fulfill(status=200, content_type="application/json", body='{"ok":true}',
                                             headers={"Access-Control-Allow-Origin": "*"})
                    return route.continue_()
                ctx.route("**/*", interceptar)
                pendientes = list(cola) if nombre == a.configs[0] else list(vistas)
                hechas = 0
                while pendientes:
                    url = pendientes.pop(0)
                    if nombre == a.configs[0]:
                        if url in vistas:
                            continue
                        vistas.append(url)
                    pg = ctx.new_page()
                    consola, fallos = [], []
                    # «Failed to load resource» ya sale en fallos_red (y ahí se distingue lo propio de lo ajeno y lo bloqueado).
                    pg.on("console", lambda m: m.type == "error" and not m.text.startswith("Failed to load resource")
                          and consola.append(m.text[:300]))
                    pg.on("pageerror", lambda e: consola.append("JS: " + str(e)[:300]))
                    pg.on("requestfailed", lambda r: not ANALITICA.search(r.url) and fallos.append(f"{r.url} ({r.failure})"))
                    pg.on("response", lambda r: r.status >= 400 and fallos.append(f"{r.url} ({r.status})"))
                    try:
                        resp = pg.goto(url, wait_until="load", timeout=45000)
                        estado = resp.status if resp else None
                        # Bajar poco a poco para que carguen las imágenes perezosas.
                        pg.evaluate("""async () => { for (let y = 0; y < document.body.scrollHeight; y += innerHeight / 2) {
                            scrollTo(0, y); await new Promise(r => setTimeout(r, 120)); } scrollTo(0, 0); }""")
                        pg.wait_for_load_state("networkidle", timeout=15000)
                    except Exception as e:
                        estado = f"error: {e}"[:200]
                    m = pg.evaluate(MEDIR) if isinstance(estado, int) else {}
                    hechas += 1
                    slug = re.sub(r"[^a-z0-9]+", "-", urlparse(url).path.lower()).strip("-") or "inicio"
                    carpeta = a.salida / nombre
                    carpeta.mkdir(parents=True, exist_ok=True)
                    pref = carpeta / f"{hechas:02d}-{slug}"
                    if m:
                        pg.screenshot(path=f"{pref}-pantalla.png")
                        pg.screenshot(path=f"{pref}-entera.png", full_page=True)
                    texto = m.pop("texto", "")
                    nuevos = m.pop("enlaces", [])
                    if nombre == a.configs[0]:
                        for h in nuevos:
                            h = urldefrag(h)[0]
                            if h.startswith(("http://", "https://")):
                                enlaces.add(h)
                            if urlparse(h).netloc == origen and h not in vistas and h not in pendientes \
                                    and len(vistas) + len(pendientes) < len(cola) + a.rastrear \
                                    and not re.search(r"\.(xml|json|md|txt|pdf|zip|png|jpe?g|webp|svg)$", urlparse(h).path):
                                pendientes.append(h)
                        if url == primera:
                            for t in a.espera:
                                # innerText aplica text-transform: vale también si está en el HTML.
                                if t not in texto and t not in pg.content() and not any(t in g for g in graves):
                                    graves.append(f"no aparece «{t}» en {url}")
                    ficha = {"estado": estado, "consola": consola, "fallos_red": sorted(set(fallos)), **m}
                    informe["paginas"].setdefault(url, {})[nombre] = ficha
                    if estado != 200:
                        graves.append(f"[{nombre}] {url} responde {estado}")
                    for c in consola:
                        graves.append(f"[{nombre}] {url} consola: {c}")
                    for f in sorted(set(fallos)):
                        if urlparse(f.split(" ")[0]).netloc == origen:
                            graves.append(f"[{nombre}] {url} recurso propio roto: {f}")
                    pg.close()
                ctx.close()
            nav.close()

        # Enlaces: los propios siempre; los de fuera solo si se pide (muchos sitios bloquean robots).
        for h in sorted(enlaces):
            propio = urlparse(h).netloc == origen
            if propio and h in vistas:
                continue
            if not propio and not a.externos:
                continue
            r = comprobar_enlace(h)
            if r != 200 and not (isinstance(r, int) and 300 <= r < 400):
                informe["enlaces_rotos"].append({"url": h, "estado": r, "propio": propio})
                if propio:
                    graves.append(f"enlace propio roto: {h} ({r})")
    finally:
        parar(proc)

    a.salida.mkdir(parents=True, exist_ok=True)
    (a.salida / "informe.json").write_text(json.dumps(informe, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(informe['paginas'])} páginas x {len(a.configs)} configs · informe en {a.salida / 'informe.json'}")
    for url, por_config in informe["paginas"].items():
        for cfg, f in por_config.items():
            avisos = [k for k in ("scroll_horizontal",) if f.get(k)] + \
                     [f"{k}={len(f[k])}" for k in ("desborda", "cortado", "imagenes_rotas", "sin_alt", "objetivos_pequenos") if f.get(k)]
            if f.get("viewport_meta") is False:
                avisos.append("sin meta viewport")
            if avisos:
                print(f"  [{cfg}] {url}: {', '.join(avisos)}")
    for g_ in graves:
        print("GRAVE:", g_)
    return 1 if graves else 0


if __name__ == "__main__":
    sys.exit(main())
