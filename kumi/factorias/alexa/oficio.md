# Oficio: fábrica Alexa

Lo que sabe esta fábrica de las skills custom de Alexa en Node.js, valga para el proyecto que valga. Lo propio de cada skill (su id, su remoto, si se despliega sola o a mano) va en el brain del proyecto y en su receta.

## Cómo está montada una skill

```
skill-package/
  skill.json                                  manifiesto: nombre, frases de ejemplo, países, permisos, endpoint
  interactionModels/custom/<locale>.json      modelo de interacción por idioma (invocationName, intents, slots, types)
  assets/                                     iconos de la ficha (108 y 512 px)
lambda/                                       código (sourceDir del manifiesto): index.js + package.json
ask-resources.json, .ask/                     solo si el proyecto está ligado a ASK CLI v2
```

En el formato viejo de ASK CLI v1 el manifiesto está en la raíz (`skill.json`) y los modelos en `models/<locale>.json`: mismo contenido.

- `lambda/index.js` exporta `exports.handler = Alexa.SkillBuilders.custom().addRequestHandlers(...).addErrorHandlers(...).lambda()`. Los handlers se prueban **en orden**: el primero cuyo `canHandle` dé `true` se queda la petición. El `ErrorHandler` con `canHandle() { return true }` va en `addErrorHandlers`, nunca entre los de petición.
- Cada intent del modelo tiene que tener handler, y cada handler de intent tiene que corresponder a un intent del modelo (en todos los locales). Si no, Alexa contesta con el `ErrorHandler` («ha habido un problema») y no hay error en ningún sitio que lo delate.
- Intents obligatorios en el modelo: `AMAZON.CancelIntent`, `AMAZON.HelpIntent`, `AMAZON.StopIntent`, `AMAZON.NavigateHomeIntent`. Muy recomendable `AMAZON.FallbackIntent` con su handler, para no caer en el de errores cuando no se entiende la frase.

## Compilar y probar

- No hay compilación. Dependencias: `npm ci` en `lambda/` si hay `package-lock.json`; si no, `npm install --no-package-lock --ignore-scripts` (para no dejar un lockfile sin querer en el commit). `node_modules/` tiene que estar en `.gitignore`.
- Prueba la lambda en local con eventos de ejemplo: `require('./lambda/index.js').handler(evento, {}, callback)`. La cadena de test `alexa` lleva un arnés de eventos y un validador del modelo en su oficio (`{kumi}/tests/alexa/oficio.md`); úsalos para autocomprobar.
- Si añades tests al proyecto, ponlos **fuera de `lambda/`** (por ejemplo `test/` en la raíz) y con `node:test` (`node --test test/`), sin dependencias nuevas: todo lo que hay en `lambda/` se sube a Amazon. El `require` de `../lambda/index.js` resuelve `ask-sdk-core` desde `lambda/node_modules`.
- La hora: la Lambda corre en UTC. Todo lo que dependa del día se calcula en `Europe/Madrid` (`toLocaleString('en-US', { timeZone: 'Europe/Madrid' })` o `Intl.DateTimeFormat`), y se prueba cerca de medianoche y en los cambios de hora.
- No toques la versión de `lambda/package.json` ni del manifiesto: la central se ocupa al publicar si la receta lo pide.

## Lo que dice Alexa

- Textos en español de España, como se dirían en voz alta: números y horas que suenen bien («a las 21 horas», no «21:00 h»), sin abreviaturas ni símbolos.
- `speak()` mete el texto en SSML **sin escapar**: un `&`, un `<` o un `>` en el texto rompe la respuesta entera (Alexa no dice nada). Escápalos o evítalos.
- Si la respuesta deja la sesión abierta (`reprompt`), la pregunta tiene que ser explícita. Si no se espera respuesta, `withShouldEndSession(true)`.
- Cambiar `invocationName` cambia cómo la abre la familia en sus Echo: es una decisión de producto, no la tomes tú (`decision` abierta).

## Modelo de interacción

- `invocationName`: minúsculas, al menos dos palabras, sin «alexa», «amazon», «echo», «skill» ni «app».
- `samples`: sin puntuación, sin repetir la misma frase en dos intents, y cada `{slot}` declarado en el intent. Tipos propios en `types`; los `AMAZON.*` no.
- Un cambio en el modelo **no vale sin reconstruirlo en Amazon** (Build Model): el código nuevo con el modelo viejo falla en silencio. Si cambias modelo y código a la vez, déjalo dicho en lo que hay que recorrer.

## Despliegue

- **Alexa-hosted con ASK CLI** (hay `ask-resources.json` o el remoto es de CodeCommit, `codecommit::` o `git-codecommit.*.amazonaws.com`): según la documentación de Amazon, un `git push` a la rama `master` de ese repo despliega en la fase *development* el código de `lambda/` y el `skill-package/` (modelo incluido). Es decir: **el push ya cambia la skill en los Echo de quien la usa en desarrollo o en beta**. Tú no haces push; lo hace la central tras la puerta de riesgo, y todo lo que toca `lambda/` o `skill-package/` es nivel 2.
- **Alexa-hosted sin ASK CLI** (remoto en GitHub, sin `.ask/`): el despliegue es manual en la consola (pegar modelo, Build Model, pegar código, Deploy). La central solo hace merge y push a GitHub; en tu documento deja escrito qué hay que pegar y dónde.
- **Lambda propia**: despliegue por `ask deploy` o por la infraestructura del repo (`infrastructure/`). Mismas reglas.
- ASK CLI: el `ask` global de {humano} está roto (falta el paquete); usa `npx -y ask-cli@2.30.7 <orden>`. Tiene perfil `default` en `~/.ask/cli_config`. No lo uses para desplegar ni para cambiar nada en Amazon: como mucho para leer (`ask smapi get-skill-manifest`, `list-skills-for-vendor`).
- Nunca pulses ni pidas «Submit for Certification»: las skills de {humano} son personales o en beta y no deben ir a la tienda pública.

## Datos y permisos

- Pedir permisos (`permissions` en el manifiesto: perfil, dirección, listas), guardar datos por usuario (persistencia en S3 o DynamoDB, que Alexa-hosted da por variables de entorno) o llamar a otros servicios es nivel 2 y debe justificarse en el encargo.
- `console.log` va a CloudWatch: nunca el `userId`, el `deviceId` ni el contenido de lo que dice el usuario. El `requestEnvelope` entero tampoco.
- Nada de claves en el código: si la skill necesita una, variable de entorno de la Lambda y que {humano} la ponga.
