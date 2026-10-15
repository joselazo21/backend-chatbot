# Backend del chatbot multicanal

Backend para un chatbot multiempresa que podrá atender un widget web y canales de mensajería. El diseño separa el núcleo de conversación de los adaptadores de canales y modelos, usa RAG para consultar documentos y limita las acciones a herramientas MCP autorizadas.

> **Estado:** base Django ASGI con alta de clientes, API de servicio, descubrimiento del dominio API y sesiones públicas temporales del widget. El frontend está en otro repositorio. Aún no hay mensajes, respuestas del modelo ni integraciones de canales.

## Documentación

- [Arquitectura propuesta](docs/arquitectura-chatbot.md): requisitos, componentes, aislamiento por cliente, canales, modelos, RAG y MCP.
- [Guía de despliegue](docs/guidelines-despliegue-chatbot.md): decisiones pendientes y controles operativos. Las plantillas de esa guía son orientativas; los archivos ejecutables de este repositorio están en `backend/` y `compose.yaml`.
- [API de servicio v1](docs/api-servicio-v1.md): alta de clientes, credenciales y rutas disponibles.
- [Widget público](docs/widget-bootstrap-api.md): registro de sitios, descubrimiento y contrato de las sesiones que consumirá el frontend.

## Stack previsto

| Área | Tecnología prevista |
|---|---|
| API y streaming web | Python, Django ASGI, Uvicorn, SSE |
| Datos | PostgreSQL con pgvector |
| Trabajo en segundo plano | Celery, Celery Beat y Redis |
| Aislamiento de clientes | Esquemas PostgreSQL con `django-tenants`, sujetos a pruebas de permisos y acceso cruzado |
| Dependencias Python | `pyproject.toml`, `uv` y `uv.lock` |
| Herramientas externas | Servidores MCP propios, autorizados por cliente y usuario |

Las elecciones marcadas como pendientes en la arquitectura siguen abiertas. En particular, el proveedor de nube, los modelos, la residencia de datos y el nivel de aislamiento contratado deben definirse antes de admitir datos de clientes.

## Arranque local

Necesitas Docker Compose. Desde la raíz del repositorio, en PowerShell:

```powershell
Copy-Item .env.example .env
# Cambia POSTGRES_PASSWORD y DJANGO_SECRET_KEY en .env.
docker compose build
docker compose up -d db redis
docker compose run --rm web python manage.py migrate_schemas --shared --noinput
docker compose run --rm web python manage.py bootstrap_public_tenant --domain localhost
docker compose up -d
Invoke-RestMethod http://localhost:8000/health/
docker compose run --rm web python manage.py test
```

La ruta `/health/` comprueba que Django responde para un dominio registrado; no comprueba la disponibilidad de Redis. El comando `bootstrap_public_tenant` puede repetirse sin duplicar el cliente o el dominio. Para aplicar migraciones posteriores a todos los clientes, ejecuta `docker compose run --rm web python manage.py migrate_schemas --noinput` antes de actualizar los procesos. El script SQL habilita pgvector al crear por primera vez el volumen de PostgreSQL. El volumen de código y `--reload` son para desarrollo local. Celery tiene un worker para la cola `messages` y una instancia de Beat. Por ahora no hay tareas ni horarios configurados, así que ambos quedan a la espera.

`django-tenants` resuelve el cliente a partir del dominio de la petición. Los modelos `Customer` y `Domain` viven en `public`; `Conversation` vive en cada esquema de cliente. Un dominio desconocido devuelve 404. Esta separación lógica aún no es una barrera de permisos entre clientes: el rol de base de datos de la aplicación puede acceder a todos los esquemas.

Para dar de alta un cliente de desarrollo y recibir su credencial de servicio una sola vez:

```powershell
docker compose run --rm web python manage.py create_customer --schema acme --name Acme --domain acme.localhost
```

Guarda la credencial fuera del repositorio. La API de servicio acepta `Authorization: Bearer <token>` en el dominio del cliente. La clave es privada y está pensada para llamadas entre servidores; **no la incluyas en el widget**. Si ya tienes un `.env` creado, añade `.localhost` a `DJANGO_ALLOWED_HOSTS` para las pruebas locales. Consulta los ejemplos y la rotación de credenciales en la [guía de la API](docs/api-servicio-v1.md).

Para cambiar dependencias, entra en `backend/`, usa `uv add` o `uv remove`, guarda `pyproject.toml` y `uv.lock`, y reconstruye la imagen.

## Próximo hito

Incorporar el envío y la lectura de mensajes con el token de sesión del widget, seguido del motor de conversación y las primeras tareas de ingesta en una cola y un worker separados. RAG y los canales se incorporarán sobre esa base.

## Seguridad y contribuciones

- No subir `.env`, API keys, tokens, documentos de clientes ni conversaciones al repositorio.
- Revisar cambios en dependencias y conservar `uv.lock` junto a `pyproject.toml`.
- Añadir pruebas de aislamiento antes de activar el alta de clientes.
- Mantener las decisiones nuevas en `docs/` y actualizar el README cuando exista un arranque local real.

No se ha definido una licencia para este repositorio.
