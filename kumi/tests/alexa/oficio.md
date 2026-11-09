# Oficio: test Alexa

Cómo se prueba una skill de Alexa sin humano delante y **sin desplegar**: la lambda en local con las peticiones que mandaría Alexa, en varias fechas; el modelo de interacción y el manifiesto validados; y cada intent del modelo cruzado con su handler. Aquí «usarla como una persona» es hacerle las preguntas que le haría la familia a su Echo y leer lo que contestaría.

## Reglas de la casa

- **No despliegues ni cambies nada en Amazon.** Nada de `git push` al remoto de la skill (en Alexa-hosted, un push a CodeCommit ya despliega), `ask deploy`, Build Model ni Deploy en la consola.
- **No ensucies el worktree**: copia `lambda/` a `scratch/lambda` dentro de tu carpeta de trabajo e instala ahí: `npm ci --ignore-scripts` si hay `package-lock.json`, si no `npm install --no-package-lock --ignore-scripts --no-audit --no-fund`. Al final, `git status --porcelain` de la obra igual que al empezar.
- `ask` global está roto en esta máquina: si hace falta, `npx -y ask-cli@2.30.7`.

## Configuraciones

Cada configuración es una fecha y hora fija (en `Europe/Madrid`) con la que se congela el reloj de la lambda. Evidencia en `evidencias/<config>/`.

| Config | Fecha fijada | Para qué |
|---|---|---|
| semana | los 7 días de una semana normal a las 10:00 (p. ej. 2026-11-09 a 2026-11-15) | cada día de la semana |
| medianoche | 2026-11-11T23:30+01:00 y 2026-11-12T00:30+01:00 (en UTC, 22:30 y 23:30 del 11) | la Lambda corre en UTC: «hoy» y «mañana» tienen que salir en hora de Mallorca |
| cambio-hora | 2027-03-28T01:30+01:00, 2027-03-28T03:30+02:00 y 2026-10-25T02:30+01:00 | horario de verano |

Si el código no depende de la fecha, basta con `semana` y lo dices.

## 1. Construye

- `scratch/lambda` con las dependencias instaladas y `node -e "require('./scratch/lambda/index.js').handler"` sin error. Si no carga: `alta` y terminas.
- Si el proyecto tiene tests de verdad (`npm test` que no sea el `echo "No tests"` de plantilla, o `node --test test/`), ejecútalos y guarda la salida.

## 2. Modelo y manifiesto

Guarda este validador como `scratch/validar_modelo.js` y pásale cada modelo (`skill-package/interactionModels/custom/*.json` o `models/*.json`):

```js
// uso: node validar_modelo.js <modelo.json>...
const fs = require('fs');
let errores = 0;
const mal = (f, m) => { errores++; console.log(`ERROR ${f}: ${m}`); };
const OBLIG = ['AMAZON.CancelIntent', 'AMAZON.HelpIntent', 'AMAZON.StopIntent', 'AMAZON.NavigateHomeIntent'];
for (const f of process.argv.slice(2)) {
  let m; try { m = JSON.parse(fs.readFileSync(f, 'utf8')).interactionModel.languageModel; } catch (e) { mal(f, 'JSON roto o sin interactionModel.languageModel: ' + e.message); continue; }
  const inv = m.invocationName || '';
  if (!/^[a-záéíóúüñàèòç' .]+$/.test(inv) || inv.trim().split(/\s+/).length < 2) mal(f, `invocationName «${inv}»: minúsculas y al menos dos palabras`);
  if (/\b(alexa|amazon|echo|skill|app)\b/.test(inv)) mal(f, `invocationName «${inv}» lleva una palabra reservada`);
  const nombres = new Set((m.intents || []).map(i => i.name));
  for (const o of OBLIG) if (!nombres.has(o)) mal(f, `falta ${o}`);
  if (!nombres.has('AMAZON.FallbackIntent')) console.log(`AVISO ${f}: sin AMAZON.FallbackIntent`);
  const tipos = new Set((m.types || []).map(t => t.name));
  const vistas = new Map();
  for (const it of m.intents || []) {
    const slots = new Map((it.slots || []).map(s => [s.name, s.type]));
    for (const [s, t] of slots) if (!t.startsWith('AMAZON.') && !tipos.has(t)) mal(f, `${it.name}: slot {${s}} de tipo ${t} sin definir en types`);
    if (!it.name.startsWith('AMAZON.') && !(it.samples || []).length) mal(f, `${it.name}: sin samples`);
    for (const s of it.samples || []) {
      const norm = s.trim().toLowerCase();
      for (const r of s.matchAll(/\{(\w+)\}/g)) if (!slots.has(r[1])) mal(f, `${it.name}: «${s}» usa {${r[1]}} que no está en slots`);
      if (/[.,!?¿¡]/.test(s)) mal(f, `${it.name}: «${s}» lleva puntuación`);
      if (vistas.has(norm) && vistas.get(norm) !== it.name) mal(f, `«${s}» está en ${it.name} y en ${vistas.get(norm)}`);
      vistas.set(norm, it.name);
    }
  }
  console.log(`${f}: ${nombres.size} intents, ${vistas.size} samples`);
}
process.exit(errores ? 1 : 0);
```

Además, a mano:

- **Intents ↔ handlers**: cada intent del modelo (salvo los `AMAZON.*` que el código no necesite) aparece en algún `getIntentName(...) === '<nombre>'` del código, y cada nombre del código existe en todos los modelos. Uno sin pareja es `alta`: Alexa respondería con el error genérico.
- **Manifiesto** (`skill.json`): JSON válido; un locale en `publishingInformation.locales` por cada modelo; `apis.custom.endpoint.sourceDir` apunta a la carpeta de la lambda (o `uri` a una Lambda propia). Si el diff cambia `permissions`, `privacyAndCompliance`, países o distribución, dilo en el informe aunque esté bien: {humano} tiene que saberlo.
- Si cambió el modelo, apunta en el informe que **hay que reconstruirlo en Amazon** al desplegar.

## 3. Recorrido con eventos

Guarda este arnés como `scratch/probar.js`. Congela el reloj, monta peticiones como las de Alexa y las pasa por el `handler`:

```js
// uso: node probar.js <carpeta lambda> <fecha ISO con zona> <casos.json>
// casos.json: [["nombre", {"type": "LaunchRequest"}], ["viernes", {"type": "IntentRequest", "intent": {"name": "X", "slots": {"dia": {"name": "dia", "value": "viernes"}}}}]]
const path = require('path'), fs = require('fs');
const [lambda, fija, casosF] = process.argv.slice(2);
const Real = Date, t0 = new Real(fija).getTime();
global.Date = class extends Real { constructor(...a) { super(...(a.length ? a : [t0])); } static now() { return t0; } };
const { handler } = require(path.join(path.resolve(lambda), 'index.js'));
const app = { applicationId: 'amzn1.ask.skill.prueba' }, user = { userId: 'amzn1.ask.account.prueba' };
const evento = (request, locale) => ({ version: '1.0',
  session: { new: true, sessionId: 'SessionId.prueba', application: app, user, attributes: {} },
  context: { System: { application: app, user, device: { deviceId: 'prueba', supportedInterfaces: {} }, apiEndpoint: 'https://api.eu.amazonalexa.com' } },
  request: { requestId: 'prueba', timestamp: new Date().toISOString(), locale, ...request } });
const errores = []; const log = console.log; console.log = (...a) => { if (String(a[0]).includes('rror')) errores.push(a.join(' ').split('\n')[0]); };
(async () => {
  let mal = 0;
  for (const [nombre, req, locale = 'es-ES'] of JSON.parse(fs.readFileSync(casosF, 'utf8'))) {
    if (req.intent) { req.intent.confirmationStatus ??= 'NONE'; for (const s of Object.values(req.intent.slots || {})) s.confirmationStatus ??= 'NONE'; }
    errores.length = 0;
    const r = await new Promise((ok, ko) => handler(evento(req, locale), {}, (e, res) => e ? ko(e) : ok(res)));
    const ssml = r.response.outputSpeech?.ssml ?? '';
    const interior = ssml.replace(/^<speak>|<\/speak>$/g, '').replace(/<\/?[a-z\-:]+[^<>]*>/gi, '');
    const fallos = [...errores, ...(/[<>]|&(?!amp;|lt;|gt;|quot;|apos;)/.test(interior) ? ['SSML con <, > o & sin escapar'] : [])];
    if (fallos.length) mal++;
    log(JSON.stringify({ caso: nombre, fecha: fija, dice: ssml, reprompt: r.response.reprompt?.outputSpeech?.ssml ?? null,
                         fin: r.response.shouldEndSession ?? null, fallos }));
  }
  process.exit(mal ? 1 : 0);
})().catch(e => { log('EXCEPCIÓN ' + e.stack); process.exit(2); });
```

Escribe `scratch/casos.json` con, como mínimo:

- `LaunchRequest`;
- **cada intent propio del modelo**, con sus slots rellenos con valores reales (los que diría Alexa: para `AMAZON.DayOfWeek` en es-ES llega el día en español, «viernes») y además con el slot vacío y con un valor raro;
- `AMAZON.HelpIntent`, `AMAZON.StopIntent`, `AMAZON.CancelIntent`, `AMAZON.FallbackIntent` y un intent que no exista (debe contestar algo útil, no reventar);
- `SessionEndedRequest` (sin respuesta hablada, sin error);
- un caso por cada locale que tenga modelo.

Ejecuta `node scratch/probar.js scratch/lambda <fecha> scratch/casos.json > evidencias/<config>/<fecha>.jsonl` por cada fecha de la matriz y **lee lo que dice** cada caso como si lo oyeras en el Echo:

- ¿Responde a lo que se pregunta y con el dato correcto para esa fecha? Compáralo con la referencia del proyecto (calendario, tabla del README, lo que diga el encargo), no con lo que el propio código cree.
- ¿Se entiende en voz alta? Números, horas, abreviaturas, símbolos, frases que no terminan.
- `fallos` vacío; si `ErrorHandler` contestó (sale en `fallos` como «Error handled»), es `alta`, salvo en el intent inexistente y en `AMAZON.FallbackIntent` cuando el modelo no lo declara (Alexa nunca lo mandaría: `baja`, recomendación de añadirlo).
- Sesión: si hay `reprompt`, la frase acaba en pregunta; si no lo hay, `fin: true`.

Cada criterio del encargo: cumple, no cumple o no comprobable (y por qué).

## 4. Con Amazon (solo lectura, opcional)

Solo si el encargo lo pide o si `ask` puede ayudar sin desplegar: `npx -y ask-cli@2.30.7 smapi list-skills-for-vendor` para encontrar el id y `smapi get-skill-manifest -s <id> -g development` para comparar el manifiesto publicado con el de la rama. **`ask dialog` y `smapi simulate-skill` hablan con lo que está desplegado**, no con la rama: antes de publicar solo sirven para tener la conversación de referencia del código viejo. Nunca copies tokens de `~/.ask/cli_config` a evidencias.

## Prod tests (tras publicar)

Los ejecuta la central desde la receta del proyecto (`prod_tests`). Propón en el informe los que encajen, por ejemplo una conversación con `ask dialog` contra la fase *development* ya desplegada:

```bash
# scratch/replay.json: {"skillId": "<id>", "locale": "es-ES", "type": "text", "userInput": ["abre basura artá", "qué toca el viernes"]}
npx -y ask-cli@2.30.7 dialog --replay replay.json --save-skill-io dialogo.json && grep -q "materia orgánica" dialogo.json
```

Solo tiene sentido si el despliegue es automático (CodeCommit o ASK CLI). Si el proyecto se despliega a mano en la consola, dilo: el prod test tiene que esperar a que {humano} lo haya pegado.

## Deja el entorno como estaba

Borra `scratch/lambda/node_modules`, comprueba `git status --porcelain` de la obra y que no queda ningún proceso de Node tuyo vivo.
