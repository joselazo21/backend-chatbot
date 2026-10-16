# Documentos RAG y respuestas SSE

El frontend vive en otro repositorio. Estas rutas ofrecen ingestión privada de documentos y un flujo público autenticado para la respuesta del asistente.

## Configuración y despliegue

Configura el endpoint de embeddings compatible con OpenAI en `web` y `worker`:

```dotenv
EMBEDDING_URL=https://embeddings.example.com/v1/embeddings
EMBEDDING_MODEL=nombre-del-modelo
EMBEDDING_API_TOKEN=
EMBEDDING_TIMEOUT_SECONDS=60
```

`EMBEDDING_URL` es la ruta completa. El backend envía `{"model": "...", "input": ["texto"]}` y espera `data` con objetos `index` y `embedding`. El modelo debe devolver vectores de la misma dimensión en todas las solicitudes. No se presupone una dimensión concreta. Si cambia el modelo o su dimensión, vuelve a procesar los documentos para ese modelo. Deja `EMBEDDING_URL` vacío mientras no exista el servicio; la carga responderá `503`.

Configura también `LLM_CHAT_URL`, `LLM_MODEL` y, si procede, `LLM_API_TOKEN`. Para streaming, el LLM debe aceptar `stream: true` y devolver eventos SSE de chat completions compatibles con OpenAI (`choices[0].delta.content`, seguidos de `data: [DONE]`). El adaptador anterior para la respuesta JSON completa sigue disponible, pero el worker usa streaming.

Tras actualizar el código, ejecuta `docker compose run --rm web python manage.py migrate_schemas --noinput` antes de reiniciar `web`, `worker` y `beat`. La migración compartida habilita `vector` en PostgreSQL; la imagen `pgvector/pgvector:pg17` ya contiene la extensión. En desarrollo, Compose monta `media_data` en `/app/media` tanto para `web` como para `worker`. En producción, todos los procesos que procesen documentos necesitan el mismo almacenamiento privado y persistente; una carpeta local independiente por réplica no sirve. No publiques `/app/media` como archivos estáticos.

## Cargar documentos

La API de documentos exige la credencial privada de servicio del cliente en su propio dominio. Sólo admite PDF con texto extraíble, UTF-8 `.txt`, `.md` y `.markdown`. Tamaño máximo: 5 MB; PDF máximo: 100 páginas. Los PDF escaneados sin texto dan `no_extractable_text`; requieren OCR fuera de este flujo.

```http
POST /api/v1/documents/ HTTP/1.1
Host: acme.chat.example.com
Authorization: Bearer <credencial-de-servicio>
Content-Type: multipart/form-data

file=@manual.pdf
```

Responde `202` con `id`, `filename`, `size_bytes`, `status: "queued"`, `error_code`, `chunk_count` y `created_at`. `GET /api/v1/documents/` lista los 100 documentos más recientes; `GET /api/v1/documents/{id}/` muestra el estado y el número de fragmentos cuando está listo. `DELETE /api/v1/documents/{id}/` borra el documento, sus fragmentos y el archivo. Las rutas no aceptan tokens públicos `ws_...`.

Celery extrae el texto, lo divide en fragmentos de hasta 1.200 caracteres con 150 de solapamiento, genera embeddings en lotes de 16 y guarda vectores y texto en el esquema PostgreSQL del cliente. Los documentos pasan por `queued`, `processing`, `ready` o `failed`. Los errores temporales se reintentan hasta tres veces; Beat recupera trabajos sin despachar y procesos interrumpidos. El campo `error_code` indica un fallo sin exponer el documento ni la respuesta del proveedor.

El worker vectoriza la última pregunta del visitante, busca hasta cuatro fragmentos del mismo cliente y modelo mediante distancia coseno en pgvector y los incluye como contexto numerado. Guarda las fuentes usadas en `AssistantRun.retrieved_sources`. Se usa búsqueda exacta; antes de añadir un índice aproximado hay que fijar dimensiones y medir calidad y latencia con documentos reales. **Carga sólo material que pueda citarse al visitante**: este primer alcance no define permisos por documento ni por visitante. Tampoco aplica OCR, deduplicación o evaluación automática de relevancia. La recuperación puede incluir fragmentos poco útiles; prepara preguntas de evaluación antes de ajustar umbrales y tamaño de fragmentos.

## Recibir la respuesta progresivamente

Después de `POST /widget/v1/messages/`, usa el `id` del mensaje del visitante:

```http
GET /widget/v1/messages/{id}/events/ HTTP/1.1
Host: acme.chat.example.com
Origin: https://www.acme.com
Authorization: Bearer ws_...
Accept: text/event-stream
```

El endpoint exige el mismo origen y token de sesión que la API de mensajes. Una petición de otra conversación devuelve `404`; una sesión inválida, `401`; una generación sin LLM configurado, `409`. Responde con `text/event-stream`, `Cache-Control: no-store` y `X-Accel-Buffering: no`. El proxy debe permitir conexiones HTTP largas sin buffering ni compresión de esa ruta.

Eventos:

```text
retry: 1000

event: reset
data: {"attempt":1}

event: delta
data: {"text":"Hola"}

event: complete
data: {"message_id":42,"sources":[]}

```

`reset` indica que el frontend debe vaciar el texto provisional; también aparece si Celery reintenta la generación. `delta` agrega texto al borrador. `complete` contiene el ID del mensaje definitivo en PostgreSQL y las fuentes recuperadas. Un fallo definitivo emite `error` con `code`. La conexión envía comentarios `: ping` periódicos y un evento `reconnect` tras 120 segundos para que el cliente vuelva a abrirla. Al reconectar, el backend reproduce desde el inicio el intento actual, así que el widget debe aplicar `reset`. `GET /widget/v1/messages/` sigue siendo la fuente para recuperar mensajes definitivos después de cerrar o recargar la página. `GET /widget/v1/messages/{id}/generation/` devuelve `status` y `sources` una vez completado.

El navegador debe leer este SSE con `fetch` y `ReadableStream`, porque `EventSource` nativo no permite añadir el encabezado `Authorization: Bearer`. Nunca pongas el token en la URL. El trabajo del modelo sigue en Celery; la conexión SSE sólo observa su progreso guardado en PostgreSQL. Esta primera implementación consulta ese progreso cada 250 ms por conexión. Mide conexiones concurrentes y carga de PostgreSQL antes de escalar; para alto volumen convendría una señal por Redis o un mecanismo equivalente.
