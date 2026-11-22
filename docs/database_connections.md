# Conexiones a Bases de Datos y Resiliencia (`biolink-backend`)

Este documento detalla la configuración física de los tres pools de conexión nativos (sin ORM) y la distinción operativa entre *liveness* y *readiness*.

---

## 1. PostgreSQL 18.7 (`psycopg` + `psycopg_pool`)

- **Archivo:** [`src/biolink_backend/infrastructure/postgresql.py`](../src/biolink_backend/infrastructure/postgresql.py)
- **Pool:** `psycopg_pool.ConnectionPool` con `min_size=2`, `max_size=10`, `row_factory=dict_row`.
- **Protección contra bloqueos:**
  - `connect_timeout`: `5 s` por defecto.
  - `statement_timeout`: `10,000 ms` por defecto a nivel de sesión (`options="-c statement_timeout=..."`).
  - `open=False` al construir el pool para evitar que la API aborte su proceso si PostgreSQL tarda unos segundos adicionales en iniciar; las verificaciones de readiness detectan el estado real.
- **Sonda de Salud:** `SELECT 1 AS ok, current_setting('server_version') AS version`.

---

## 2. MongoDB Community 8.0.38 (`pymongo`)

- **Archivo:** [`src/biolink_backend/infrastructure/mongodb.py`](../src/biolink_backend/infrastructure/mongodb.py)
- **Pool:** `pymongo.MongoClient` con `minPoolSize=1`, `maxPoolSize=10`.
- **Timeouts explícitos:**
  - `connectTimeoutMS=5000`
  - `serverSelectionTimeoutMS=5000` (evita esperas por defecto de 30s cuando el servidor no está disponible)
  - `socketTimeoutMS=10000`
- **Presupuesto del motor en Docker:** `--wiredTigerCacheSizeGB 0.25` dentro de `768 MiB`.
- **Sonda de Salud:** `db.command("ping")` y consulta de versión del servidor.

---

## 3. Neo4j Community 2026.10.2 (`neo4j` Bolt Driver)

- **Archivo:** [`src/biolink_backend/infrastructure/neo4j.py`](../src/biolink_backend/infrastructure/neo4j.py)
- **Driver:** `neo4j.GraphDatabase.driver` sobre `bolt://` con `max_connection_pool_size=15`, `connection_timeout=5.0 s`, `max_transaction_retry_time=15.0 s`.
- **Mitigación del Problema Observado en el Benchmark (ADR-004):**
  - En la campaña de benchmarking de 50,000 afirmaciones, Neo4j agotó el pool interno de transacción (`268.8 MiB`) al ejecutar `DETACH DELETE` masivo en una sola transacción.
  - En `docker-compose.yaml` del backend se fijan explícitamente:
    - `NEO4J_server_memory_heap_initial__size: 256m`
    - `NEO4J_server_memory_heap_max__size: 384m`
    - `NEO4J_server_memory_pagecache_size: 256m`
    - `NEO4J_dbms_memory_transaction_total_max: 256m`
    - `NEO4J_db_memory_transaction_max: 64m`
  - En [`Neo4jProjectionRepository`](../src/biolink_backend/repositories/graph/projection_repository.py), las operaciones por lotes utilizan `CALL { ... } IN TRANSACTIONS OF 1000 ROWS`.
- **Sonda de Salud:** `RETURN 1 AS ok` + consulta de versión mediante `CALL dbms.components()`.

---

## 4. Liveness vs Readiness (`/api/v1/health/*`)

| Endpoint | Propósito | Consulta Bases de Datos | Código HTTP Esperado |
|---|---|---|---|
| `GET /api/v1/health/live` | Confirmar que el proceso Python/FastAPI responde sin bloqueo del bucle de eventos. | **No** | Siempre `200 OK` mientras el proceso viva. |
| `GET /api/v1/health/ready` | Determinar si el backend puede recibir tráfico real verificando PostgreSQL, MongoDB y Neo4j. | **Sí (los 3 motores)** | `200 OK` si los 3 están `healthy`; **`503 Service Unavailable`** si falla al menos 1. |
| `GET /api/v1/health` | Diagnóstico consolidado (proceso + presupuesto de memoria + detalle de latencias). | **Sí (los 3 motores)** | `200 OK` si `ready=true`; `503 Service Unavailable` si está `degraded`. |
