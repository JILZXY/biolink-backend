# Configuración y Gestión de Variables (`biolink-backend`)

La configuración del servicio está centralizada en [`src/biolink_backend/config/settings.py`](../src/biolink_backend/config/settings.py) usando **Pydantic Settings v2** (`BaseSettings`).

---

## 1. Validaciones Obligatorias al Arranque

Si alguna de las siguientes reglas no se cumple, la aplicación falla rápidamente (*fail-fast*) antes de abrir puertos HTTP o pools:

1. **Prohibición de `:latest` en imágenes Docker:** `POSTGRES_IMAGE`, `MONGODB_IMAGE` y `NEO4J_IMAGE` deben declarar versión explícita (`postgres:18.7`, `mongo:8.0.38`, `neo4j:2026.10.2-community`).
2. **CORS Restringido:** `CORS_ALLOWED_ORIGINS` rechaza el comodín `"*"` y exige esquemas `http://` o `https://`.
3. **Longitud Mínima de Secretos:** `AUTH_SECRET_KEY` debe tener al menos 32 caracteres y las contraseñas de las bases no pueden estar vacías.
4. **Endurecimiento en `APP_ENV=production`:** Prohíbe marcadores de ejemplo (`CHANGE_ME`, `REPLACE_WITH`, `DEV_ONLY`) y exige `SESSION_COOKIE_SECURE=true`.
5. **Control Estricto de Presupuesto de Memoria (4 GB = 4,096 MiB):**
   - `POSTGRES_MEM_LIMIT_MB <= 768`
   - `MONGODB_MEM_LIMIT_MB <= 768`
   - `NEO4J_MEM_LIMIT_MB <= 1024`
   - `BACKEND_MEM_LIMIT_MB + WORKER_MEM_LIMIT_MB <= 512`
   - Suma total de contenedores `<= 3,072 MiB` (preservando al menos `1,024 MiB` para el host).

---

## 2. Catálogo de Variables Principales (`.env.example`)

| Grupo | Variable | Valor por Defecto | Descripción |
|---|---|---|---|
| App | `APP_ENV` | `development` | `development`, `test`, `staging` o `production` |
| App | `API_V1_PREFIX` | `/api/v1` | Prefijo versionado de rutas |
| App | `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `json` | Logging estructurado en salida estándar |
| Red | `BACKEND_HOST` / `BACKEND_PORT` | `127.0.0.1` / `8000` | Enlace local de Uvicorn |
| Red | `CORS_ALLOWED_ORIGINS` | `http://127.0.0.1:3000,http://localhost:3000` | Orígenes explícitos permitidos (lista o CSV) |
| Auth | `AUTH_SECRET_KEY` | *(secreto >= 32 chars)* | Clave de servidor en `SecretStr` |
| Auth | `SESSION_COOKIE_NAME` / `SESSION_TTL_HOURS` | `biolink_session` / `12` | Cookie HttpOnly y duración en horas |
| Postgres | `POSTGRES_POOL_MIN_SIZE` / `MAX_SIZE` | `2` / `10` | Límites del pool `psycopg_pool` |
| Postgres | `POSTGRES_STATEMENT_TIMEOUT_MS` | `10000` | Timeout máximo por sentencia SQL |
| MongoDB | `MONGODB_SERVER_SELECTION_TIMEOUT_MS` | `5000` | Límite de espera de selección de nodo |
| Neo4j | `NEO4J_HEAP_INITIAL` / `NEO4J_HEAP_MAX` | `256m` / `384m` | Límites JVM dentro de los 1,024 MiB |
| Neo4j | `NEO4J_TX_TOTAL_MAX` / `NEO4J_TX_MAX` | `256m` / `64m` | Control del pool transaccional (ADR-004) |
| Worker | `OUTBOX_POLL_INTERVAL_SEC` / `OUTBOX_BATCH_SIZE` | `2.0` / `50` | Cadencia y tamaño de lote del outbox |

---

## 3. Enmascaramiento de Secretos

- Todos los secretos utilizan `pydantic.SecretStr`.
- El formateador `StructuredJsonFormatter` redacta automáticamente cualquier clave cuyo nombre contenga `password`, `token`, `secret`, `authorization` o `cookie`.
- Las propiedades `postgres_masked_dsn` y `mongodb_masked_uri` ocultan credenciales en trazas de arranque.
