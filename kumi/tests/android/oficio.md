# Oficio: test Android

Cómo se prueba una app Android como la usaría una persona: emulador, varios tamaños de móvil y de letra.

- SDK, adb, emulador y AVD de test: en «Este trabajo».
- Si no hay dispositivo en `adb devices`, arranca el AVD sin ventana: `emulator -avd <AVD> -no-window -no-audio -no-snapshot-save -no-boot-anim` en segundo plano y espera a `sys.boot_completed = 1`. Recuerda si lo arrancaste tú.
- **Nunca mates el emulador con taskkill**: el siguiente arranque se queda parado en un diálogo de fallo. Para pararlo: `adb emu kill`.
- Matriz de pantallas (`adb shell wm size`, `wm density`, `settings put system font_scale`):

| Config | Tamaño | Densidad | Letra |
|---|---|---|---|
| pequeña | 720x1280 | 320 | 1.0 |
| pequeña-grande | 720x1280 | 320 | 1.5 |
| grande | 1440x3120 | 560 | 1.0 |
| grande-media | 1440x3120 | 560 | 1.3 |

- En cada config: `am force-stop` y abrir la app; recorrer con `uiautomator dump` + `input tap/text/swipe`; `screencap` tras cada paso a `evidencias/<config>/<nn>-<paso>.png`; **mirar las capturas** (texto cortado o encima de otro, botones tapados o fuera de pantalla, contenido que no se puede desplazar); `adb logcat -d -b crash` buscando `FATAL EXCEPTION` y ANR.
- Al acabar: `wm size reset`, `wm density reset`, `font_scale 1.0` y, si lo arrancaste tú, `adb emu kill`.
