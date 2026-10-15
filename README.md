# Backend del chatbot multicanal

Backend para un chatbot multiempresa que podrá atender un widget web y canales de mensajería. El diseño separa el núcleo de conversación de los adaptadores de canales y modelos, usa RAG para consultar documentos y limita las acciones a herramientas MCP autorizadas.

> **Estado:** base Django ASGI con clientes y conversaciones separados por esquema, ruta de salud y entorno local con PostgreSQL, Redis y Celery. Aún no hay API de conversación ni integraciones de canales.

## Documentación

- [Arquitectura propuesta](docs/arquitectura-chatbot.md): requisitos, componentes, aislamiento por cliente, canales, modelos, RAG y MCP.
- [Guía de despliegue](docs/guidelines-despliegue-chatbot.md): decisiones pendientes y controles operativos. Las plantillas de esa guía son orientativas; los archivos ejecutables de este repositorio están en `backend/` y `compose.yaml`.

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

Para cambiar dependencias, entra en `backend/`, usa `uv add` o `uv remove`, guarda `pyproject.toml` y `uv.lock`, y reconstruye la imagen.

## Próximo hito

Implementar un alta de clientes controlada y la primera API de conversaciones, con autorización vinculada al cliente. Después, incorporar las primeras tareas de ingesta en una cola y un worker separados. RAG y los canales se incorporarán sobre esa base.

## Seguridad y contribuciones

- No subir `.env`, API keys, tokens, documentos de clientes ni conversaciones al repositorio.
- Revisar cambios en dependencias y conservar `uv.lock` junto a `pyproject.toml`.
- Añadir pruebas de aislamiento antes de activar el alta de clientes.
- Mantener las decisiones nuevas en `docs/` y actualizar el README cuando exista un arranque local real.

No se ha definido una licencia para este repositorio.
