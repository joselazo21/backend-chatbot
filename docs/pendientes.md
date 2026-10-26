# Pendientes de implementación y mejora

**Fecha de revisión:** 19 de octubre de 2026.
**Base:** comparación entre los documentos de `docs/` y el código de `backend/` en `main` (último commit `2f553fa`).

## Estado actual

Implementado y con pruebas unitarias:

- Multi-cliente con un esquema PostgreSQL por cliente (`django-tenants`), alta y rotación de la credencial de servicio.
- Widget público: descubrimiento por clave `pk_...` y `Origin`, sesiones `ws_...` con caducidad y revocación, mensajes paginados y rate limiting en Redis.
- Generación asíncrona en Celery con estados persistentes, reintentos, orden por conversación y recuperación desde Beat.
- RAG básico: carga de PDF/TXT/MD, troceado por caracteres, embeddings OpenAI-compatibles y búsqueda exacta por coseno en pgvector.
- Streaming SSE del borrador al widget.
- Telegram (webhook) y WhatsApp mediante el puente Baileys, con deduplicación y entregas persistentes.
- Despliegue inicial en Railway.

Configurado en `.env` local, pendiente de prueba de extremo a extremo:

- LLM: Ollama en Railway con `qwen2.5:0.5b` (streaming verificado con `curl`).
- Embeddings: Voyage AI con `voyage-3` (llamada verificada con `curl`).

## 1. Necesario para un piloto

| # | Pendiente | Detalle |
|---|---|---|
| 1.1 | Prueba de extremo a extremo | Levantar Compose, crear un cliente, subir un documento hasta `ready`, enviar un mensaje por el widget y recibir la respuesta por SSE. Repetir con Telegram y Baileys con cuentas reales. |
| 1.2 | Configuración por cliente | Hoy el LLM es global por variables de entorno y no hay prompt de sistema. Añadir `ChatbotConfig` en el esquema del cliente: prompt de sistema, nombre y tono, proveedor, URL, modelo, token, RAG activo y límites. |
| 1.3 | Panel de administración | `django.contrib.admin` no está instalado. Instalarlo con usuarios separados para la plataforma y para cada cliente; gestionar clientes, sitios, canales, documentos y configuración sin consola. |
| 1.4 | Mensajes `unconfigured` | Los mensajes guardados sin LLM configurado nunca se responden. Añadir un comando o tarea que cree sus generaciones pendientes. |
| 1.5 | Límites de gasto | Solo se limitan mensajes por minuto. Añadir tope de tokens por mensaje y por conversación, y presupuesto o cuota por cliente con aviso, límite flexible y límite duro. |
| 1.6 | Proteger el LLM | El servicio Ollama tiene dominio público sin autenticación. Retirar el dominio y usar la red privada de Railway, o poner un proxy con token. |
| 1.7 | Modelo de mayor calidad | `qwen2.5:0.5b` sirve para probar el flujo, no para responder con contexto RAG ni para herramientas. Evaluar `qwen2.5:3b`/`7b` o un proveedor comercial. |

## 2. Núcleo previsto en la arquitectura

| # | Pendiente | Detalle |
|---|---|---|
| 2.1 | Capa `LLMProvider` | Solo existe un transporte OpenAI-compatible. Definir la interfaz (`chat`, `stream`, `capabilities`), adaptadores Anthropic y Google, y un modelo de respaldo con política explícita de privacidad. |
| 2.2 | Herramientas (MCP) | Solo hace falta si el bot debe consultar datos en vivo o ejecutar acciones (pedidos, citas, tickets). Requiere cliente MCP, bucle de herramientas con límite de iteraciones, `ServidorMCP`, `PermisoHerramienta`, `LlamadaHerramienta`, identidad inyectada por el backend y confirmación para escritura. Decidir antes los casos de uso y el modelo con tool calling. |
| 2.3 | Identidad firmada en el widget | `identify()` con JWT o HMAC firmado por el servidor del cliente. Sin ella todos los visitantes son anónimos. |
| 2.4 | Interfaz `ChannelAdapter` | Formalizar `IncomingMessage`/`OutgoingMessage` y `capabilities` (streaming, longitud, formato, botones) para añadir canales sin tocar el núcleo. |
| 2.5 | Historial largo | Hoy se recortan 10 turnos o 12 000 caracteres. Ajustar al contexto del modelo y resumir historiales largos. |

## 3. Calidad del RAG

| # | Pendiente | Detalle |
|---|---|---|
| 3.1 | Extracción | Docling para estructura y tablas; OCR para PDF escaneados (hoy fallan con `no_extractable_text`); formatos DOCX, XLSX y HTML. |
| 3.2 | Limpieza y troceado | Eliminar encabezados y pies repetidos, deduplicar por hash y trocear por sección con contexto `Documento > Sección`. |
| 3.3 | Búsqueda híbrida | Combinar pgvector con búsqueda de texto completo de PostgreSQL para nombres propios y códigos. |
| 3.4 | Índice aproximado | Valorar HNSW solo después de medir calidad y latencia con documentos reales. |
| 3.5 | Reindexación | Cambiar de modelo de embeddings exige volver a subir los documentos. Conservar el original o un derivado para reprocesar en Celery. |
| 3.6 | Evaluación | Conjunto de 30–50 preguntas reales con respuesta y fuente esperadas, ejecutado ante cada cambio del pipeline. |
| 3.7 | Permisos por documento | Hoy cualquier documento cargado puede citarse a cualquier visitante. |
| 3.8 | Cola de ingesta | La ingesta usa la cola `messages`. Separarla en `ingest` con su propio worker para que un PDF pesado no retrase los chats. |

## 4. Seguridad y privacidad

| # | Pendiente | Detalle |
|---|---|---|
| 4.1 | Aislamiento en la base de datos | El rol de la aplicación puede leer todos los esquemas. Revisar roles y permisos de PostgreSQL y ampliar las pruebas de acceso cruzado: SQL explícito, conexiones recicladas, tareas concurrentes y caché. |
| 4.2 | Cifrado de credenciales | Tokens de LLM, canales y MCP cifrados por cliente. Hoy el token de Telegram vive en una variable de entorno. |
| 4.3 | Retención | Política configurable por cliente y borrado automático desde Beat. |
| 4.4 | Ciclo de vida del cliente | Estados `provisionando → listo → suspendido → eliminado` y baja verificable de datos, claves y registros. |
| 4.5 | Registro de proveedores externos | Dejar constancia de a qué proveedor se envían datos de cada cliente (LLM y embeddings). |
| 4.6 | Datos en planes gratuitos | No subir documentos ni conversaciones reales mientras se usen planes gratuitos de proveedores externos. |

## 5. Observabilidad

| # | Pendiente | Detalle |
|---|---|---|
| 5.1 | Métricas por mensaje | Modelo, tokens de entrada y salida, coste estimado, latencia de primer token y total. El LLM ya devuelve `usage`; falta guardarlo. |
| 5.2 | Paneles por cliente | Uso y coste por cliente, canal y sitio. |
| 5.3 | Errores y trazas | Errores por proveedor y canal; integración opcional con Sentry o Langfuse. |
| 5.4 | Salud | `/health/` no comprueba Redis, PostgreSQL ni los proveedores. |

## 6. Operación y producción

| # | Pendiente | Detalle |
|---|---|---|
| 6.1 | CI/CD | Pruebas, análisis de dependencias y publicación de imágenes en cada cambio. |
| 6.2 | Backups | Copias cifradas y restauración ensayada con RTO/RPO medidos. |
| 6.3 | Proxy y TLS | IP real detrás del proxy para el rate limiting, timeouts y buffering desactivado en la ruta SSE. |
| 6.4 | Beat separado | En Railway, Beat corre dentro del worker; ese servicio no puede escalarse sin duplicar tareas. |
| 6.5 | SSE a escala | Cada conexión consulta PostgreSQL cada 250 ms. Para alto volumen, usar una señal en Redis. |
| 6.6 | Imagen de producción | Usuario sin privilegios, imágenes base fijadas por digest y revisión de vulnerabilidades. |
| 6.7 | Pruebas de carga | Mezcla de streaming, canales e ingesta con varios clientes. |

## 7. Fases posteriores

- Traspaso de la conversación a un agente humano.
- Botones, respuestas rápidas y adjuntos en el widget; archivos en Telegram y WhatsApp. Los audios ya se transcriben en el widget y en Telegram (Speaches); falta WhatsApp/Baileys (`audioMessage`) y responder con voz.
- WhatsApp mediante la Cloud API oficial si Baileys no resulta estable para clientes.
- Panel de administración propio y analítica.
- Canales adicionales: Slack, Discord, email.

## Decisiones abiertas

De la sección 19 de la [arquitectura](arquitectura-chatbot.md), solo SSE está adoptado en la práctica. Siguen abiertas, entre otras:

- Casos de uso que justifican herramientas MCP y qué servidores construir.
- Modelo de producción y modelo local definitivo.
- Proveedor de embeddings definitivo y residencia de datos.
- Política de retención por defecto.
- WhatsApp: Baileys frente a Cloud API o un intermediario.

## Orden recomendado

1. Prueba de extremo a extremo (1.1) y protección del LLM (1.6).
2. Configuración por cliente y panel de administración (1.2, 1.3).
3. Mensajes `unconfigured` y límites de gasto (1.4, 1.5).
4. Métricas por mensaje (5.1) y cola de ingesta separada (3.8).
5. Mejoras del RAG (sección 3) con el conjunto de evaluación.
6. Herramientas MCP e identidad firmada, cuando haya un caso de uso concreto.
