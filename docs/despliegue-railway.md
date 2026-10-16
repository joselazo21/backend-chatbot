# Despliegue inicial en Railway

El backend usa cuatro servicios dentro de un mismo proyecto y entorno: `postgres` con la imagen `pgvector/pgvector:pg17` y un volumen persistente en `/var/lib/postgresql/data`, `redis` con `redis:7-alpine`, `web` y `worker`. Web y worker usan el mismo repositorio con directorio raíz `/backend`; Railway construye el `Dockerfile` de esa carpeta. No hace falta bucket: los archivos RAG de hasta 5 MB pasan temporalmente por PostgreSQL.

Esta topología mantiene separados la API, las tareas y la base de datos. Para el primer despliegue de poco tráfico, el único worker ejecuta Celery y Beat mediante `celery -A config worker -B --loglevel=INFO --queues=messages`. No aumentes el número de réplicas de ese worker: cada réplica iniciaría otro Beat y duplicaría tareas periódicas. Cuando haya presupuesto para otro servicio, separa Beat del worker.

## Configuración

1. Crea un dominio público de Railway solo para `web`, dirigido al puerto `8000`. PostgreSQL, Redis y worker permanecen privados.
2. Configura en `postgres` `POSTGRES_DB=chatbot`, `POSTGRES_USER=chatbot`, una contraseña aleatoria en `POSTGRES_PASSWORD` y `PGDATA=/var/lib/postgresql/data/pgdata`.
3. Configura en web y worker los mismos `DJANGO_SECRET_KEY`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` y `POSTGRES_HOST` mediante referencias a las variables del servicio PostgreSQL. Usa el dominio privado y el puerto `5432`.
4. Configura `CELERY_BROKER_URL=redis://redis.railway.internal:6379/0` y `REDIS_CACHE_URL=redis://redis.railway.internal:6379/2` en web y worker. Redis puede ser efímero en este MVP: las tareas pendientes se detectan de nuevo desde PostgreSQL por Beat.
5. Configura `DJANGO_DEBUG=0`. Railway añade automáticamente `RAILWAY_PUBLIC_DOMAIN` al servicio web y el backend lo incorpora a `ALLOWED_HOSTS`. Define `LLM_CHAT_URL` y `LLM_MODEL` cuando el servicio Qwen esté listo; no incluyas un token inexistente. La carga RAG requiere además `EMBEDDING_URL` y `EMBEDDING_MODEL`.
6. En web, fija el comando previo al despliegue en `python manage.py prepare_railway`. Aplica migraciones, crea el dominio público para `django-tenants` y registra `healthcheck.railway.app`. Configura la ruta de comprobación `/health/`.

La primera puesta en marcha puede requerir reintentos: el comando previo de web necesita PostgreSQL disponible y worker necesita PostgreSQL y Redis. Después verifica `https://<dominio>/health/`, el estado de los cuatro servicios y sus registros. No expongas PostgreSQL ni Redis con dominios públicos.

## Límite de recursos

Antes de aprovisionar, revisa el plan y los límites del espacio de Railway. Cuatro servicios continuos, aunque no usen bucket, pueden agotar el crédito gratuito. La base de datos necesita un volumen persistente; no sustituyas ese volumen por el disco efímero del contenedor. Si Railway rechaza la creación de recursos, no retires servicios de otros proyectos sin decidir primero cuáles pueden dejar de funcionar.
