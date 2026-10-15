# API de servicio v1

Esta primera API permite crear y consultar conversaciones desde un servidor autorizado del cliente. Todavía no recibe mensajes ni genera respuestas. La credencial de servicio es privada y da acceso a las conversaciones del cliente: no debe aparecer en JavaScript, HTML ni en el widget público.

## Alta y credenciales

Tras ejecutar las migraciones compartidas, el operador crea un cliente desde la raíz del repositorio:

```powershell
docker compose run --rm web python manage.py create_customer --schema acme --name Acme --domain acme.localhost
```

El comando crea el esquema y el dominio, ejecuta las migraciones del nuevo cliente y muestra una credencial aleatoria **una sola vez**. En PostgreSQL solo se guarda su hash. Repetir el comando con el mismo nombre, esquema y dominio no cambia la credencial. Una combinación distinta se rechaza. No ejecutes este comando en registros de shell compartidos ni copies la credencial al repositorio.

Para invalidar la credencial anterior y obtener otra:

```powershell
docker compose run --rm web python manage.py rotate_customer_token --schema acme
```

Los comandos de alta y rotación son operaciones de administración que requieren acceso a la consola del backend. No hay una ruta HTTP pública para esas operaciones.

## Peticiones

Usa el dominio registrado para el cliente y envía `Authorization: Bearer <credencial>`. El dominio selecciona el esquema y la credencial se compara con el hash de ese mismo cliente. Una credencial de otro cliente devuelve `401`; una conversación que no existe en el esquema seleccionado devuelve `404`.

| Método y ruta | Resultado |
|---|---|
| `POST /api/v1/conversations/` | Crea una conversación vacía. Acepta cuerpo vacío o `{}` JSON. Devuelve `201`, `id`, `created_at` y cabecera `Location`. |
| `GET /api/v1/conversations/{id}/` | Devuelve `id` y `created_at` para una conversación del cliente. |

Ejemplo local, sustituyendo la credencial recibida por el comando de alta:

```powershell
$token = '<credencial-de-servicio>'
curl.exe --noproxy '*' --resolve acme.localhost:8000:127.0.0.1 -H "Authorization: Bearer $token" -H 'Content-Type: application/json' -d '{}' http://acme.localhost:8000/api/v1/conversations/
```

La opción `--resolve` dirige el subdominio al servidor local sin cambiar el archivo `hosts`. La configuración de desarrollo permite `.localhost`; para otros dominios, actualiza `DJANGO_ALLOWED_HOSTS` y DNS. Antes de poner esta API en internet faltan TLS, límites de petición y tráfico, observabilidad y una política de credenciales de servicio. La sesión pública del widget usará otra credencial y otro flujo.
