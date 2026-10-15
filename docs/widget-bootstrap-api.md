# Descubrimiento de la API para el widget

El frontend del widget vive en otro repositorio. Allí se implementarán el script de carga, el iframe y la interfaz. Este backend ofrece un punto de descubrimiento para que ese frontend encuentre el dominio API de cada cliente a partir de una clave pública.

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

La ruta sólo funciona en el dominio del esquema `public`. La clave debe pertenecer a un sitio activo y el encabezado `Origin` debe coincidir con su origen registrado. Una clave desconocida, un origen diferente, un sitio inactivo o una petición al dominio de un cliente dan 404. La respuesta usa el dominio primario de ese cliente. El frontend puede usar `api_base_url` para iniciar el flujo público del widget cuando éste exista.

El navegador envía `Origin` en esta solicitud entre orígenes. La respuesta autoriza sólo ese origen, sin comodín. El frontend no necesita enviar una credencial privada para el descubrimiento. La clave `pk_...` identifica la instalación y es visible en la página: **no autentica al visitante ni autoriza acceso a conversaciones**. La API de servicio `/api/v1/conversations/` sigue exigiendo su token privado y no debe llamarse desde el navegador. El siguiente paso del backend es crear sesiones públicas del widget con permisos y límites propios, antes de exponer mensajes o conversaciones al frontend.

En producción, configura DNS y TLS tanto para el dominio central como para los dominios primarios de los clientes. `api_base_url` toma el esquema y el puerto de la petición al dominio central; si hay un proxy TLS delante de Uvicorn, éste debe transmitir el esquema correcto y Django debe confiar sólo en el proxy configurado. En desarrollo local, el puerto del dominio central se conserva en la URL devuelta.
