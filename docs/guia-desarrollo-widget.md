# Guía para desarrollar el widget (frontend)

Para quien construya la UI del chatbot en el repositorio del frontend. Resume el contrato real del backend (verificado contra el código en `main`, 19 de octubre de 2026), lo que ya está desplegado y lo que todavía no existe.

Documentos de referencia: [Descubrimiento y sesión pública](widget-bootstrap-api.md), [RAG y streaming](rag-y-streaming.md) y la sección 5.6 de la [arquitectura](arquitectura-chatbot.md).

## 1. Qué tiene que hacer el widget

1. Cargarse con un fragmento `<script>` en la web de un cliente, sin bloquearla.
2. Crear una **sesión pública** (token `ws_...` de 30 minutos) que representa a un visitante anónimo y a una conversación.
3. Enviar los mensajes del visitante y mostrar la respuesta del asistente **en streaming** (SSE).
4. Mostrar las fuentes citadas cuando la respuesta use documentos del cliente.

El backend nunca recibe credenciales privadas del widget. La clave `pk_...` es **pública** (visible en la página) y no autentica al visitante: solo identifica la instalación.

## 2. Entorno desplegado hoy

| Dato | Valor |
|---|---|
| URL base de la API | `https://web-production-c4c6e.up.railway.app` |
| Cliente | `demo` |
| Clave pública del widget | `pk_BYvJuiuTs47dyX39mAvWYSFVMU7Pi40H` |
| Origen registrado | `https://prueba.example.com` (no existe como web real) |
| Documento cargado | `estudiantes.txt` con datos inventados: preguntar por "Marlene Quispe Arroyo" debería citarlo |

**Para desarrollar en local hay que registrar tu origen.** El backend solo acepta peticiones cuyo `Origin` esté registrado para esa clave. Pide al responsable del backend que ejecute `create_widget_site --schema demo --origin http://localhost:5173` (o el puerto que uses). Solo se admite HTTP para `localhost`, `127.0.0.1` y `*.localhost`; cualquier otro origen exige HTTPS. Cada origen recibe su propia clave `pk_...`.

### Limitaciones del entorno actual
- **El LLM es muy lento y poco fiable.** Corre en CPU en un plan gratuito (`qwen2.5:0.5b`). Una respuesta tarda entre 1 y 3 minutos y puede ser incorrecta. Diseña la UI para esperas largas y no la juzgues por la calidad del texto.
- **Un despliegue en `main` interrumpe las generaciones en curso.** Esa respuesta tarda unos 6 minutos más en recuperarse.
- **Aún no existe el flujo de descubrimiento (`/widget/v1/bootstrap`).** Solo responde en el dominio del tenant público, y hoy el único dominio público del servicio pertenece al cliente `demo`. Mientras tanto, **usa la URL base fija** y salta el paso de bootstrap (ver sección 4).

## 3. Reglas generales de la API

- Todas las rutas empiezan por `/widget/v1/` y son JSON, salvo el stream.
- El navegador debe enviar `Origin` (lo hace solo en peticiones entre orígenes). Si falta, o no está registrado, o la clave no coincide, la respuesta es **404**, sin más información.
- **CORS:** la respuesta devuelve `Access-Control-Allow-Origin` con el origen exacto (nunca `*`) y `Vary: Origin`. No se usan cookies ni `credentials`.
- Todas las respuestas llevan `Cache-Control: no-store`.
- Los errores tienen la forma `{"error": "<codigo>"}`.
- **Preflight (`OPTIONS`)** devuelve 204 con estos `Access-Control-Allow-Headers`:

| Ruta | Cabeceras permitidas |
|---|---|
| `POST /sessions/` | `Content-Type` |
| `GET, DELETE /sessions/current` | `Authorization` |
| `GET, POST /messages/` | `Authorization, Content-Type` |
| `GET /messages/{id}/generation/` y `/events/` | `Authorization` |

No envíes otras cabeceras personalizadas: provocarían un fallo de CORS.

## 4. Flujo completo

```
página del cliente (origen registrado)
 │ 1. POST /widget/v1/sessions/            {"key":"pk_..."}            → access_token (ws_...)
 │ 2. POST /widget/v1/messages/            {"content":"..."}           → id del mensaje del usuario
 │ 3. GET  /widget/v1/messages/{id}/events/                            → stream SSE de la respuesta
 │ 4. GET  /widget/v1/messages/?after_id=N                             → historial / recuperación
 └ 5. DELETE /widget/v1/sessions/current                               → cerrar sesión (opcional)
```

### 4.1 Crear la sesión
```http
POST /widget/v1/sessions/
Origin: https://tu-origen-registrado
Content-Type: application/json

{"key":"pk_..."}
```
`201`:
```json
{"conversation_id":"<uuid>","access_token":"ws_...","token_type":"Bearer","expires_at":"2026-10-19T19:30:00+00:00"}
```
- El body debe contener **exactamente** la clave `key`.
- El token solo se entrega aquí. Caduca a los **30 minutos** y **no hay renovación**: al caducar hay que crear otra sesión, que abre una **conversación nueva** (se pierde el contexto anterior).
- Límites: 10 por minuto por sitio e IP, y 60 por minuto por sitio → `429` con `Retry-After: 60`.
- `503 {"error":"unavailable"}` si el limitador (Redis) no responde: no se crea la sesión.

Errores: `415 json_required`, `400 invalid_json` / `invalid_request`, `404` (clave, origen o sitio inválidos).

### 4.2 Comprobar o cerrar la sesión
`GET /widget/v1/sessions/current` con `Authorization: Bearer ws_...` devuelve `{"conversation_id","expires_at"}`. `DELETE` revoca el token (`204`). Un token ausente, caducado o revocado devuelve `401 {"error":"unauthorized"}` con `WWW-Authenticate: Bearer`.

### 4.3 Enviar un mensaje
```http
POST /widget/v1/messages/
Authorization: Bearer ws_...
Content-Type: application/json

{"content":"¿De dónde es Marlene Quispe Arroyo?"}
```
`201`:
```json
{"id":42,"role":"user","content":"...","created_at":"...","generation_status":"pending"}
```
- El body debe contener solo `content` (cadena). Se recortan los espacios; debe tener entre 1 y 4000 caracteres y no puede incluir `\x00`. Máximo 32 KiB de cuerpo.
- **No se envía `conversation_id`**: el backend usa la del token.
- **Límite: 20 mensajes por minuto por sesión** → `429` con `Retry-After: 60`. Deshabilita el botón de enviar mientras haya una respuesta en curso: no hace falta llegar al límite.
- `201` solo significa "mensaje guardado". La respuesta del asistente llega después.
- `generation_status`: `pending` (se generará), `running`, `completed`, `failed`, o `unconfigured` (el backend no tiene LLM configurado: no habrá respuesta).

Errores: `415 json_required`, `413 body_too_large`, `400 invalid_json` / `invalid_request` / `invalid_content`, `401 unauthorized`, `429 rate_limited`, `503 unavailable`.

### 4.4 Recibir la respuesta por SSE
```http
GET /widget/v1/messages/42/events/
Authorization: Bearer ws_...
Accept: text/event-stream
```
**No uses `EventSource`**: no permite la cabecera `Authorization`. Usa `fetch` con `ReadableStream` y analiza el formato SSE tú mismo. **Nunca pongas el token en la URL.**

Eventos:

| Evento | `data` | Qué hacer |
|---|---|---|
| `retry: 1000` | (línea inicial) | Ignorar. |
| `reset` | `{"attempt": n}` | **Vaciar el texto provisional** y empezar de nuevo. |
| `delta` | `{"text": "..."}` | Añadir `text` al borrador. |
| `complete` | `{"message_id": n, "sources": [...]}` | Cerrar el borrador. `message_id` es el id del mensaje definitivo. |
| `error` | `{"code": "..."}` | Mostrar un error; ver 4.6. |
| `reconnect` | `{}` | El servidor cierra tras **120 s**; **vuelve a abrir** `/events/` y aplica el nuevo `reset`. |
| `: ping` | (comentario) | Se envía cada 15 s. Ignorar. |

Detalles que importan:
- **El `reset` es obligatorio.** Cada conexión nueva reproduce desde el inicio el intento actual, y un reintento del backend también lo emite. Si no vacías el borrador, verás texto duplicado.
- Con el LLM actual, el primer `delta` puede tardar minutos. Como la conexión se cierra a los 120 s con `reconnect`, **el flujo normal incluye varias reconexiones**. Muestra un indicador de "escribiendo" hasta el primer `delta`.
- Los `delta` llegan por lotes (el backend sondea cada 250 ms), no token a token.
- Si abres `/events/` de una generación ya terminada, recibes `reset`, un `delta` con todo el texto y `complete`.
- Cerrar la conexión **no cancela** la generación. Tampoco hay endpoint para cancelarla.

Errores HTTP del stream, antes de empezar: `401 unauthorized`, `404` (mensaje de otra conversación o inexistente), `409 {"error":"generation_unconfigured"}`.

### 4.5 Fuentes y citas
`sources` es una lista como esta:
```json
[{"document_id":"<uuid>","filename":"estudiantes.txt","page":null,"chunk":0}]
```
El texto del asistente puede contener marcadores `[1]`, `[2]`… que se corresponden con la **posición + 1** en `sources`. Puedes mostrarlos como notas enlazadas. `page` es `null` si el documento no tiene páginas. Hoy no hay ruta para descargar o abrir el documento: solo se muestra el nombre. Si no se recuperó ningún fragmento, `sources` es `[]`.

### 4.6 Errores durante la generación
`error` emite un código como `upstream_unavailable`, `upstream_http_error`, `upstream_response_too_large`, `invalid_upstream_response`, `invalid_configuration`, `session_inactive` o `generation_failed`. Trata el código como opaco: muestra un mensaje genérico ("No he podido responder, inténtalo de nuevo") y ofrece reintentar enviando otro mensaje. El backend reintenta solo hasta 3 veces antes de dar el fallo.

### 4.7 Historial y recuperación
`GET /widget/v1/messages/?after_id=N` devuelve, ordenados por id ascendente:
```json
{"messages":[{"id":42,"role":"user","content":"...","created_at":"...","generation_status":"completed"},
             {"id":43,"role":"assistant","content":"...","created_at":"..."}],
 "has_more":false,"next_cursor":43}
```
- Hasta 50 mensajes por página; usa `next_cursor` como siguiente `after_id`. Es `null` si no hay mensajes.
- Es la fuente de verdad: úsala tras `complete` para confirmar el mensaje definitivo, o tras un fallo de red.
- `generation_status` solo aparece en los mensajes del usuario.
- `GET /widget/v1/messages/{id}/generation/` devuelve `{"message_id","status","sources"}` (`sources` solo cuando `status` es `completed`). Úsala como alternativa de sondeo si el SSE no es posible.

## 5. Requisitos de la arquitectura para el widget

Estos requisitos vienen de la [arquitectura](arquitectura-chatbot.md) y no los impone el backend; decide su implementación con el equipo:

- **Loader mínimo y asíncrono** (`async`): el botón flotante en **Shadow DOM**; el chat completo en un **iframe** servido desde un dominio propio. Peso objetivo de 20–50 KB.
- **Comunicación página ↔ iframe con `postMessage`** y `targetOrigin` exacto, comprobando siempre `event.origin` al recibir.
- **API pública en `window`** (`open()`, `close()`, `identify()`) para webs SPA.
- **Versionado en la URL** del loader (`/widget/v1/`).
- **Responsive y accesible:** pantalla completa en móvil, navegación por teclado, etiquetas ARIA.
- **Sin cookies de terceros.**
- **Atributos del script:** `data-widget-key`, `data-lang`, etc.

### Si el chat va en un iframe de otro dominio
- La **página del cliente** crea la sesión (solo su origen registrado puede hacerlo) y pasa el token al iframe por `postMessage`.
- Para que el iframe pueda usar ese token, el backend necesita la variable `WIDGET_FRONTEND_ORIGIN` con el origen del iframe (HTTPS). Con ella, el iframe puede **leer y escribir mensajes y revocar la sesión**, pero **no crear sesiones**. Pídela cuando sepas el dominio.
- Mantén el token **solo en memoria**. No uses `localStorage` ni la URL.

## 6. Seguridad en el frontend

- **Nunca insertes la respuesta del asistente con `innerHTML`.** El texto viene de un LLM y puede contener HTML o enlaces maliciosos. Renderiza como texto, o usa un renderizador de Markdown con **sanitización** estricta. El backend devuelve texto plano, sin formato.
- No registres el token `ws_...` en consola, analítica ni URLs.
- Valida `event.origin` en cada `postMessage`.
- Documenta a los clientes la **CSP** que necesitan: dominios para `script-src`, `frame-src` y `connect-src`.
- `Origin` no es autenticación: se puede falsificar fuera del navegador. No dependas de él para nada sensible.

### 4.3 bis Enviar un mensaje de voz
```http
POST /widget/v1/messages/audio/
Authorization: Bearer ws_...
Content-Type: multipart/form-data; boundary=...

audio=<archivo>
```
`201` (misma forma que un mensaje de texto, con el texto aún vacío):
```json
{"id":43,"role":"user","content":"","created_at":"...","generation_status":"pending","awaiting_transcription":true}
```
- Un único campo `audio`, sin más campos. Formatos: `audio/webm`, `audio/ogg`, `audio/mp4`, `audio/mpeg`, `audio/wav` (el `MediaRecorder` de Chrome/Firefox da `webm`/`ogg`; Safari, `mp4`). Máximo **5 MiB**. Usa el `Content-Type` real del `Blob`; no hace falta ponerlo a mano en el `FormData`.
- No hay que fijar `Content-Type` en `fetch`: el navegador añade el `boundary`. Solo `Authorization`.
- El backend transcribe en segundo plano y **reemplaza `content` por el texto**. Mientras `awaiting_transcription` sea `true`, muestra "Transcribiendo…". Cuando se complete, `GET /widget/v1/messages/` devuelve `content` con la transcripción y `awaiting_transcription: false`; muestra ese texto en la burbuja del usuario. Después sigue el mismo flujo SSE (4.4) que un mensaje de texto.
- Comparte el límite de 20 mensajes por minuto con el texto.
- Si falla la transcripción (audio vacío o ininteligible), `generation_status` pasa a `failed` (la API no expone el motivo); ofrece reintentar grabando de nuevo.

Errores: `503 audio_unavailable` (el backend no tiene transcripción configurada: oculta el botón de grabar), `415 multipart_required` / `unsupported_audio`, `413 body_too_large`, `400 invalid_request`, `401 unauthorized`, `429 rate_limited`.

## 7. Lo que todavía no existe en el backend

Si la UI lo necesita, hay que pedirlo al equipo del backend antes de diseñarla:

| Funcionalidad | Estado |
|---|---|
| Descubrimiento (`/widget/v1/bootstrap`) en el dominio de producción | Implementado, pero sin dominio público utilizable hoy |
| **Configuración visual por cliente** (nombre, colores, saludo, avatar) | No existe: ninguna ruta devuelve configuración del widget |
| **Identificación de usuarios** (`identify()`, JWT/HMAC firmado por el cliente) | No existe: todos los visitantes son anónimos |
| **Renovar sesión** sin perder la conversación | No existe |
| **Recuperar la conversación tras recargar la página** | No es posible con el token solo en memoria; decidir si se admite `sessionStorage` |
| Cancelar una generación | No existe |
| Botones, respuestas rápidas, adjuntos, traspaso a humano | No existen |
| Abrir o descargar el documento citado | No existe |
| Valoración (👍/👎) de respuestas | No existe |

## 8. Probar sin UI

```bash
BASE=https://web-production-c4c6e.up.railway.app
ORIGIN=https://prueba.example.com   # debe estar registrado para esa clave
KEY=pk_BYvJuiuTs47dyX39mAvWYSFVMU7Pi40H

# 1. Sesión
TOKEN=$(curl -s -X POST "$BASE/widget/v1/sessions/" -H "Origin: $ORIGIN" \
  -H "Content-Type: application/json" -d "{\"key\":\"$KEY\"}" | jq -r .access_token)

# 2. Mensaje
ID=$(curl -s -X POST "$BASE/widget/v1/messages/" -H "Origin: $ORIGIN" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"content":"¿De dónde es Marlene Quispe Arroyo?"}' | jq -r .id)

# 3. Stream (puede tardar minutos; reconecta si ves "reconnect")
curl -sN --max-time 400 "$BASE/widget/v1/messages/$ID/events/" \
  -H "Origin: $ORIGIN" -H "Authorization: Bearer $TOKEN" -H "Accept: text/event-stream"
```
`Origin` no se puede falsificar en el navegador, pero sí con `curl`, lo que permite probar sin una página real.

## 9. Ejemplo mínimo del lector SSE

```ts
async function streamReply(base: string, token: string, messageId: number, origin: string,
  on: { reset(): void; delta(t: string): void; complete(id: number, sources: unknown[]): void; error(code: string): void }) {
  for (;;) {                                   // reabre tras `reconnect`
    const res = await fetch(`${base}/widget/v1/messages/${messageId}/events/`, {
      headers: { Authorization: `Bearer ${token}`, Accept: 'text/event-stream' },
    });
    if (!res.ok || !res.body) return on.error(`http_${res.status}`);
    const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += value;
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const block = buf.slice(0, i); buf = buf.slice(i + 2);
        let name = 'message', data = '';
        for (const line of block.split('\n')) {
          if (line.startsWith('event: ')) name = line.slice(7);
          else if (line.startsWith('data: ')) data += line.slice(6);
        }
        if (!data) continue;                   // comentarios (`: ping`) y `retry:`
        const payload = JSON.parse(data);
        if (name === 'reset') on.reset();
        else if (name === 'delta') on.delta(payload.text);
        else if (name === 'complete') return on.complete(payload.message_id, payload.sources);
        else if (name === 'error') return on.error(payload.code);
        else if (name === 'reconnect') break;  // sale del bucle interno y reabre
      }
    }
  }
}
```
El ejemplo no controla cancelación ni reintentos de red; añádelos según la UI.

## 10. Checklist de integración

- [ ] El origen de desarrollo está registrado y tengo su clave `pk_...`.
- [ ] Creo la sesión desde la página y guardo el token solo en memoria.
- [ ] Uso `fetch` + `ReadableStream`, no `EventSource`.
- [ ] Manejo `reset`, `reconnect` y reintentos del backend sin duplicar texto.
- [ ] Muestro "escribiendo" hasta el primer `delta`, aun si tarda minutos.
- [ ] Gestiono `401` (sesión caducada → nueva sesión) y `429` (`Retry-After`).
- [ ] Sanitizo todo el contenido del asistente.
- [ ] Valido `event.origin` en los `postMessage`.
- [ ] Muestro las fuentes y respeto el mapeo `[n]` → `sources[n-1]`.
