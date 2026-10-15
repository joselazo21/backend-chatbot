# Descubrimiento y sesión pública del widget

El frontend del widget vive en otro repositorio. Allí se implementarán el script de carga, el iframe y la interfaz. Este backend ofrece el descubrimiento del dominio API y una sesión temporal vinculada al cliente, al sitio y a una conversación.

## Registrar un sitio

Primero debe existir el cliente y su dominio primario. Después se registra el origen exacto de la página que insertará el widget:

```powershell
docker compose run --rm web python manage.py create_widget_site --schema acme --origin https://www.acme.com
```

El comando imprime una clave `pk_...` para incluir en la configuración pública del widget. Repetirlo para el mismo cliente y origen devuelve la misma clave. Cada origen adicional se registra con otra ejecución y recibe su propia clave. Sólo se admite HTTP para `localhost`, `127.0.0.1` y subdominios `.localhost`; los demás orígenes requieren HTTPS. Un sitio puede desactivarse poniendo `is_active=False` en su registro `WidgetSite`.

Aplica la migración antes de registrar sitios:

```powershell
docker compose run --rm web python manage.py migrate_schemas --noinput
```

## Contrato para el frontend

El script de carga, servido desde el repositorio del frontend, conoce la URL central del backend y la clave pública del sitio. El navegador consulta el dominio central, por ejemplo:

```http
GET /widget/v1/bootstrap?key=pk_... HTTP/1.1
Host: api.example.com
Origin: https://www.acme.com
```

Respuesta correcta:

```http
HTTP/1.1 200 OK
Access-Control-Allow-Origin: https://www.acme.com
Vary: Origin
Cache-Control: no-store
Content-Type: application/json

{"api_base_url":"https://acme.chat.example.com"}
```

La ruta sólo funciona en el dominio del esquema `public`. La clave debe pertenecer a un sitio activo y el encabezado `Origin` debe coincidir con su origen registrado. Una clave desconocida, un origen diferente, un sitio inactivo o una petición al dominio de un cliente dan 404. La respuesta usa el dominio primario de ese cliente.

## Crear y usar una sesión

Desde la página con el origen registrado, el frontend llama a la URL `api_base_url` obtenida arriba:

```http
POST /widget/v1/sessions/ HTTP/1.1
Host: acme.chat.example.com
Origin: https://www.acme.com
Content-Type: application/json

{"key":"pk_..."}
```

La respuesta `201` incluye `conversation_id`, `access_token`, `token_type: "Bearer"` y `expires_at`. El token `ws_...` sólo se entrega en esa respuesta; el backend guarda su hash. Caduca a los 30 minutos por defecto. La creación también exige un sitio activo, la clave correspondiente y el origen exacto. La sesión y su conversación se guardan en el esquema PostgreSQL del cliente. Si Redis no está disponible, la creación responde `503` y no crea la sesión.

El frontend puede comprobar la sesión con `GET /widget/v1/sessions/current` y `Authorization: Bearer ws_...`. La respuesta incluye `conversation_id` y `expires_at`. `DELETE` en esa misma ruta revoca el token y responde `204`. Un token ausente, revocado o caducado responde `401`. Desactivar el sitio impide crear sesiones y usar sus tokens existentes. Ambos endpoints aceptan la solicitud CORS previa (`OPTIONS`) y devuelven el origen permitido de forma exacta.

## Mensajes de la conversación

Para guardar un mensaje del visitante, usa el dominio API del cliente y el token de sesión:

```http
POST /widget/v1/messages/ HTTP/1.1
Host: acme.chat.example.com
Origin: https://www.acme.com
Authorization: Bearer ws_...
Content-Type: application/json

{"content":"Hola, ¿en qué horario atienden?"}
```

La respuesta `201` contiene `id`, `role: "user"`, `content` y `created_at`. El contenido se recorta en los extremos, debe tener entre 1 y 4000 caracteres y el cuerpo JSON no puede superar 32 KiB. El cliente no envía un `conversation_id`: el backend toma la conversación vinculada al token. El límite predeterminado es de 20 mensajes por sesión y minuto; se configura con `WIDGET_MESSAGE_LIMIT_PER_MINUTE`. Un exceso devuelve `429` y `Retry-After: 60`. Si Redis falla, devuelve `503` sin guardar el mensaje.

`GET /widget/v1/messages/` devuelve `messages`, `has_more` y `next_cursor`. Ordena por ID ascendente y entrega hasta 50 mensajes por página. Para continuar o consultar mensajes nuevos, pasa `?after_id=<next_cursor>`; si todavía no hay mensajes, el cursor es `null`. El historial puede incluir mensajes con `role: "assistant"` cuando el motor de conversación los cree. Un token revocado, caducado o de otro cliente no puede leer ni escribir mensajes. El origen de la página o el origen configurado del iframe pueden llamar a esta ruta con ese token.

**`201` sólo confirma que se guardó el mensaje del visitante.** Aún no se genera ni se envía una respuesta del asistente; el motor de conversación y el streaming se implementarán después.

Si el chat se muestra en un iframe de otro dominio, el script de carga debe crear la sesión desde la página del cliente y entregarle el token al iframe mediante `postMessage` con un `targetOrigin` exacto. Configura `WIDGET_FRONTEND_ORIGIN` con el origen HTTPS del iframe; entonces ese origen podrá usar un token existente para consultar o revocar la sesión, pero no podrá crear sesiones. El iframe debe comprobar el origen de los mensajes que recibe. Mantén el token en memoria y no lo pongas en la URL ni en almacenamiento persistente del navegador. Si no hay iframe, `WIDGET_FRONTEND_ORIGIN` puede quedar vacío.

La creación se limita por sitio a 60 solicitudes por minuto y por sitio más IP a 10 solicitudes por minuto, por defecto. Se puede ajustar con `WIDGET_SESSION_SITE_LIMIT_PER_MINUTE`, `WIDGET_SESSION_IP_LIMIT_PER_MINUTE` y `WIDGET_SESSION_TTL_SECONDS`. Los límites usan Redis y una ventana de 60 segundos desde la primera solicitud; un exceso responde `429` con `Retry-After: 60`. En producción, Uvicorn debe recibir la IP real desde un proxy de confianza para que el límite por IP no agrupe a todos los visitantes bajo la IP del proxy.

El navegador envía `Origin` en las solicitudes entre orígenes. La respuesta autoriza sólo el origen permitido, sin comodín. El frontend no necesita una credencial privada. La clave `pk_...` identifica la instalación y es visible en la página: **no autentica al visitante**. El token de sesión sólo da acceso a los endpoints públicos de esa sesión; no sirve para la API de servicio `/api/v1/conversations/`, que sigue exigiendo su token privado. `Origin` se puede falsificar fuera del navegador, de modo que estos límites no sustituyen controles de abuso adicionales.

En producción, configura DNS y TLS tanto para el dominio central como para los dominios primarios de los clientes. `api_base_url` toma el esquema y el puerto de la petición al dominio central; si hay un proxy TLS delante de Uvicorn, éste debe transmitir el esquema correcto y Django debe confiar sólo en el proxy configurado. En desarrollo local, el puerto del dominio central se conserva en la URL devuelta.
