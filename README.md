# Backend del chatbot multicanal

Backend para un chatbot multiempresa que podrá atender un widget web y canales de mensajería. El diseño separa el núcleo de conversación de los adaptadores de canales y modelos, usa RAG para consultar documentos y limita las acciones a herramientas MCP autorizadas.

> **Estado:** repositorio inicial de documentación. Todavía no contiene una aplicación Django ejecutable.

## Documentación

- [Arquitectura propuesta](docs/arquitectura-chatbot.md): requisitos, componentes, aislamiento por cliente, canales, modelos, RAG y MCP.
- [Guía de despliegue](docs/guidelines-despliegue-chatbot.md): decisiones pendientes, controles operativos y una plantilla de Docker Compose para cuando exista el proyecto Django.

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

## Primer hito de implementación

Crear una base Django mínima en `backend/` con `pyproject.toml` y `uv.lock`, `config/asgi.py`, configuración por variables de entorno y una ruta `/health/`. A continuación, conectar PostgreSQL y Redis mediante Compose y añadir una prueba que arranque la API. Este hito debe poder ejecutarse localmente sin claves de modelos ni cuentas de Telegram o WhatsApp.

Después vendrían el modelo de clientes y dominios, las migraciones por esquema y las pruebas de aislamiento. La lógica de conversación, RAG y los canales se incorporarán sobre esa base.

## Seguridad y contribuciones

- No subir `.env`, API keys, tokens, documentos de clientes ni conversaciones al repositorio.
- Revisar cambios en dependencias y conservar `uv.lock` junto a `pyproject.toml`.
- Añadir pruebas de aislamiento antes de activar el alta de clientes.
- Mantener las decisiones nuevas en `docs/` y actualizar el README cuando exista un arranque local real.

No se ha definido una licencia para este repositorio.
