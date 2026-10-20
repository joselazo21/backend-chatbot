# Canales Telegram y WhatsApp con Baileys

## Flujo común

Los dos adaptadores entregan `{canal, chat externo, mensaje externo, texto}` al núcleo de conversación. Django identifica la cuenta de canal en el esquema `public`, entra al esquema PostgreSQL del cliente y guarda la conversación, el mensaje entrante y una generación pendiente. La restricción única `(cuenta, identificador de mensaje externo)` evita generar dos respuestas al recibir el mismo evento varias veces. Celery usa el mismo historial, RAG y LLM que el widget. Al terminar crea una entrega saliente persistente.

Telegram entrega eventos a Django por HTTPS. La respuesta sale por `sendMessage` desde Celery. Baileys abre una conexión saliente a WhatsApp Web; su proceso Node entrega mensajes entrantes a Django y consulta las respuestas pendientes por la API interna. **WhatsApp no llama a un webhook de Django cuando se usa Baileys.**

Se admiten mensajes de texto de chats privados/individuales. Grupos, archivos, audios, ediciones y reacciones quedan fuera de esta primera integración.

## Preparación

1. Aplica las migraciones compartidas y de clientes: `docker compose run --rm web python manage.py migrate_schemas --noinput`.
2. Configura `LLM_CHAT_URL` y `LLM_MODEL` para generar respuestas. Si el servicio de embeddings está configurado se añadirá contexto RAG.
3. Mantén al menos un proceso `web`, `worker` y `beat` activo. Beat recupera generaciones y entregas Telegram pendientes.
4. Define un dominio para el esquema `public`. El puente Node llama a `web:8000` por la red interna y envía ese dominio en el encabezado `Host`. En desarrollo, el dominio es `localhost`; en producción, configura `DJANGO_PUBLIC_HOST` con el dominio público registrado.

## Telegram

El token del bot se guarda como variable de entorno del backend; la base de datos solo guarda el nombre de esa variable. Para un bot de Acme, añade `TELEGRAM_BOT_TOKEN_ACME` al entorno de `web` y `worker`. En Compose de desarrollo puede añadirse a `.env`.

```powershell
docker compose run --rm web python manage.py create_channel --schema acme --provider telegram --name principal --telegram-token-env TELEGRAM_BOT_TOKEN_ACME
```

El comando imprime el ID de la cuenta y un secreto de webhook una sola vez. Guárdalos fuera del repositorio. Para registrar el webhook, coloca el secreto en `TELEGRAM_WEBHOOK_SECRET` del entorno del comando y ejecuta:

```powershell
docker compose run --rm -e TELEGRAM_WEBHOOK_SECRET web python manage.py set_telegram_webhook --account ID_DE_CUENTA --base-url https://chat.example.com
```

La URL registrada será `https://chat.example.com/channels/telegram/ID_DE_CUENTA/webhook/`. Debe llegar a `web` por HTTPS; no hay que exponer Redis, Celery ni PostgreSQL. Django compara `X-Telegram-Bot-Api-Secret-Token` con el digest del secreto. Los eventos no válidos no entran al esquema del cliente.

Las respuestas largas se dividen en fragmentos de hasta 4000 caracteres y se registra el avance tras cada envío. `sendMessage` se reintenta hasta cinco veces. Telegram no proporciona una clave de idempotencia para ese envío: si el proceso se detiene después de que Telegram acepte un fragmento y antes de registrar el avance, ese fragmento puede enviarse dos veces.

## WhatsApp con Baileys

Registra una cuenta de canal:

```powershell
docker compose run --rm web python manage.py create_channel --schema acme --provider baileys --name principal
```

Configura en el servicio Node `BAILEYS_CHANNEL_ID` con el ID impreso y `BAILEYS_CHANNEL_SECRET` con el secreto impreso. Cada cuenta requiere **un proceso Baileys y un volumen de autenticación propios**; no escales varias réplicas de la misma cuenta. En Compose, el servicio opcional `baileys` sirve para una cuenta local:

```powershell
docker compose --profile baileys up -d --build baileys
docker compose logs -f baileys
```

Escanea el QR que aparece en los logs con WhatsApp. El estado de autenticación y las claves de Signal se guardan en el volumen `baileys_auth`, nunca en Git. Protege y respalda ese volumen; perderlo exige volver a emparejar. El puente guarda también los eventos entrantes pendientes y los comprobantes de envío en ese volumen. Para varias cuentas, despliega una instancia del puente por cuenta con volumen y variables propios.

Para que el puente responda solo a ciertos remitentes, define `BAILEYS_ALLOWED_NUMBERS` con una lista separada por comas, por ejemplo `55161759` o `5355161759, 8855161759@lid`. Un número se compara por sufijo, así que el prefijo del país es opcional, y solo vale para chats individuales (`@s.whatsapp.net`). Algunos contactos llegan como `@lid` en lugar de su número; para esos hay que indicar el identificador exacto, que el puente registra en los logs al descartar un mensaje (`Ignored message from a sender that is not in BAILEYS_ALLOWED_NUMBERS: ...`). Sin la variable el puente responde a cualquier chat individual y lo avisa al arrancar. Una lista con entradas inválidas hace fallar el arranque, en lugar de permitir a todos.

La API interna del puente es:

| Método | Ruta | Propósito |
|---|---|---|
| `POST` | `/channels/baileys/{id}/inbound/` | Entregar un mensaje recibido. |
| `GET` | `/channels/baileys/{id}/outbound/` | Reservar una respuesta durante 60 segundos. |
| `POST` | `/channels/baileys/{id}/outbound/{delivery_id}/ack/` | Confirmar el envío con el token de reserva. |

Se exige `Authorization: Bearer <BAILEYS_CHANNEL_SECRET>` y el dominio del esquema `public` en `Host`. No publiques estas rutas como API de uso general. El puente usa la red interna; en un despliegue distribuido usa una conexión privada o TLS entre servicios.

Baileys usa la interfaz no oficial de WhatsApp Web, no la Cloud API de Meta. Puede requerir cambios cuando cambie WhatsApp, y la sesión puede cerrarse y necesitar otro QR. Esta integración debe validarse con el número real antes de considerarla apta para clientes. Una caída exactamente entre `sendMessage` y la escritura del comprobante local todavía puede duplicar una respuesta.

## Operación

- Desactivar `ChannelAccount.is_active` corta nuevas entradas y salidas de esa cuenta.
- Los eventos duplicados reutilizan el mismo mensaje y no vuelven a crear una generación.
- Las entregas Baileys no confirmadas vuelven a estar disponibles tras 60 segundos, hasta cinco intentos.
- Los mensajes y respuestas viven en el esquema PostgreSQL del cliente, igual que las conversaciones del widget.
- El puente no necesita una URL pública. Solo Django debe exponer la URL HTTPS para Telegram.

Referencias: [Telegram Bot API](https://core.telegram.org/bots/api), [guía de inicio de Baileys](https://github.com/WhiskeySockets/docs/blob/main/quickstart.mdx), [autenticación de Baileys](https://github.com/WhiskeySockets/Baileys/blob/master/README.md).
