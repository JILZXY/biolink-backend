# `biolink-backend` — Servicio Backend y API REST de BioLink

Repositorio independiente de servidor para **BioLink**, una plataforma académica enfocada en consultar, curar y explorar interacciones ecológicas documentadas entre animales y plantas con trazabilidad científica estricta.

Siguiendo la arquitectura oficial SOA de tres repositorios independientes (`biolink-frontend`, `biolink-backend`, `biolink-benchmarking`), este repositorio implementa un **monolito modular** en Python que integra en una misma base de código la **API REST (`/api/v1`)**, el dominio científico (`RD-01`–`RD-16`), los repositorios poli-modelo sin ORM y el worker interno de sincronización hacia el grafo.

---

## 1. Stack Tecnológico Oficial

| Capa / Rol | Tecnología | Versión Objetivo / Fijada |
|---|---|---|
| Lenguaje y runtime | Python | `3.14` (compatible con `>=3.12,<3.16`; lockfile verificado) |
| API REST y validación | FastAPI + Pydantic v2 + Uvicorn | `fastapi==0.165.0`, `pydantic==2.14.4` |
| Base Relacional (SSOT) | **PostgreSQL** + `psycopg` / `psycopg_pool` | `postgres:18.7` / `psycopg==3.3.9` |
| Base Documental | **MongoDB Community** + `pymongo` | `mongo:8.0.38` / `pymongo==4.20.1` |
| Base de Grafos | **Neo4j Community** + driver oficial `neo4j` | `neo4j:2026.10.2-community` / `neo4j==6.4.1` |
| Contenedores | Docker Compose propio | Red privada `biolink_backend_net` |

> **Sin ORM:** Todas las consultas SQL residen en `repositories/relational/`, las operaciones BSON en `repositories/document/` y las consultas Cypher en `repositories/graph/`.

---

## 2. Presupuesto de Memoria (Tope Global: 4 GB / 4,096 MiB)

| Componente | Límite Docker | Parámetros Internos Clave |
|---|---:|---|
| PostgreSQL 18.7 | `768 MiB` | `shared_buffers=192MB`, `work_mem=8MB`, `max_connections=50` |
| MongoDB 8.0.38 | `768 MiB` | `--wiredTigerCacheSizeGB 0.25` |
| Neo4j Community 2026.10.2 | `1,024 MiB` | Heap `256m–384m`, PageCache `256m`, `tx.total.max=256m`, `tx.max=64m` |
| Backend API (`uvicorn`) | `320 MiB` | Pools acotados (`psycopg` max 10, `pymongo` max 10, Bolt max 15) |
| Outbox Worker | `192 MiB` | Procesamiento por lotes (`batch_size=50`, Cypher `IN TRANSACTIONS OF 1000 ROWS`) |
| **Total Contenedores** | **3,072 MiB** | **Margen libre reservado para sistema operativo: 1,024 MiB** |

---

## 3. Estructura Modular

```text
biolink-backend/
├── src/biolink_backend/
│   ├── api/                 # Controladores HTTP, middlewares, errores, dependencias y /api/v1
│   ├── domain/              # Entidades, value objects, vocabularios y reglas RD-01..RD-16
│   ├── application/         # Puertos/interfaces y servicios de aplicacion (salud, auth)
│   ├── infrastructure/      # Pools y ciclo de vida de conexiones (Postgres, MongoDB, Neo4j)
│   ├── repositories/        # Consultas nativas separadas por paradigma (sin ORM)
│   ├── workers/             # Worker del Transactional Outbox hacia Neo4j
│   ├── config/              # Settings tipados y validacion de presupuesto/secretos
│   ├── security/            # Hashing PBKDF2-HMAC-SHA256, tokens SHA-256 y RBAC
│   ├── shared/              # Logging JSON estructurado, excepciones, IDs y tiempo parcial
│   └── main.py              # Fabrica create_app() y lifespan de FastAPI
├── database/                # DDL, indices cubrientes R2, schemas JSON y scripts Cypher (Etapa 4)
├── tests/                   # Suite unitaria e integracion (pytest)
├── docs/                    # Documentacion operativa del backend
├── .env.example             # Plantilla de variables sin secretos reales
├── Dockerfile               # Imagen de produccion reproducible (python:3.14-slim-bookworm)
├── docker-compose.yaml      # Stack privado del backend (independiente de benchmarking)
├── pyproject.toml
└── requirements.lock        # Dependencias exactas bloqueadas
```

---

## 4. Arranque Rápido y Endpoints Iniciales

### 4.1 Entorno Local (Python)
```powershell
cd biolink-backend
copy .env.example .env
# Editar .env reemplazando AUTH_SECRET_KEY y contraseñas locales
python -m venv .venv
.\.venv\Scripts\python -m pip install --no-deps -r requirements.lock
.\.venv\Scripts\python -m pip install --no-deps --no-build-isolation -e .
.\.venv\Scripts\uvicorn biolink_backend.main:create_app --factory --host 127.0.0.1 --port 8000
```

### 4.2 Despliegue Completo con Docker Compose
```powershell
cd biolink-backend
copy .env.example .env
docker compose up -d --build
```

Para cargar el fixture de referencia después de que los servicios estén saludables:
```powershell
docker compose exec -T api python -c "from pathlib import Path; import os; p = Path(os.environ['BIOLINK_DATABASE_DIR']); print(p); print((p / 'postgresql/migrations/001_create_schema.sql').is_file())"
docker compose exec -T api python -c "from biolink_backend.config.settings import get_settings; from biolink_backend.infrastructure.manager import DatabaseConnectionManager; from biolink_backend.infrastructure.reference_seeder import apply_reference_fixture; m = DatabaseConnectionManager(get_settings()); m.startup(); print(apply_reference_fixture(m)); m.shutdown()"
```
*(Antes de levantar `biolink-backend`, asegúrate de que ningún contenedor de `biolink-benchmarking` esté en ejecución para respetar el presupuesto de 4 GB).*

### 4.3 Endpoints Disponibles (`/api/v1`)

#### Salud del Servicio
- `GET /api/v1/health/live` → `200 OK` si el proceso FastAPI está vivo (sin consultar bases de datos).
- `GET /api/v1/health/ready` → `200 OK` si **PostgreSQL, MongoDB y Neo4j** responden; `503 Service Unavailable` si cualquiera falla.
- `GET /api/v1/health` → Resumen consolidado (`live`, `ready`, presupuesto de memoria y latencias por motor).

#### Operaciones Públicas de Consulta (`OP-01` a `OP-08` y `OP-15`)
| Operación | Método y Ruta | Motores Coordinados | Propósito |
|---|---|---|---|
| `OP-01` | `GET /api/v1/taxa` | PostgreSQL | Buscar taxones por nombre aceptado, sinónimo, grafía original o ID externo. |
| `OP-02` | `GET /api/v1/taxa/{taxon_id}` | PostgreSQL + MongoDB | Consultar ficha de taxón integrando identidad relacional y `taxon_dossier`. |
| `OP-03` | `GET /api/v1/taxa/{taxon_id}/interactions` | Neo4j + PostgreSQL | Listar interacciones `PUBLISHED` donde participa el taxón. |
| `OP-04` | `GET /api/v1/interactions/{assertion_id}` | PostgreSQL + MongoDB + Neo4j | Consultar evidencia, citas, localizadores, límites metodológicos y estado en grafo. |
| `OP-05` | `GET /api/v1/network/subgraph` | Neo4j + PostgreSQL | Filtrar subred documentada (`max_depth` 1..3), separando localidades indeterminadas. |
| `OP-06` | `GET /api/v1/network/compare-localities` | Neo4j + PostgreSQL | Comparar redes entre dos localidades (Montes Azules vs. Playón de la Gloria). |
| `OP-07` | `GET /api/v1/taxa/{taxon_id}/connectivity` | Neo4j + PostgreSQL | Calcular conectividad descriptiva (socios distintos vs. total de afirmaciones). |
| `OP-08` | `GET /api/v1/sources/{source_id}` | PostgreSQL + MongoDB | Consultar cita bibliográfica, versiones de dataset y expediente `study_dossier`. |
| `OP-15` | `GET /api/v1/occurrences` | PostgreSQL + MongoDB | Consultar registros de presencia separados de interacciones (`is_interaction_evidence=false`). |

#### Autenticación y Flujo Administrativo (`OP-09` a `OP-14`)
| Operación / Recurso | Método y Ruta | Roles Permitidos | Motores Coordinados | Propósito |
|---|---|---|---|---|
| Login | `POST /api/v1/auth/login` | Interno (`app_user`) | PostgreSQL | Validar credenciales PBKDF2, emitir cookie `HttpOnly` + sesión `user_session` y auditar `LOGIN`. |
| Logout | `POST /api/v1/auth/logout` | Autenticado | PostgreSQL | Revocar sesión inmediatamente y auditar `LOGOUT`. |
| Perfil | `GET /api/v1/auth/me` | Autenticado | PostgreSQL | Consultar identidad y roles activos. |
| `OP-09` | `POST /api/v1/admin/import-batches` | `CURATOR` | MongoDB + PostgreSQL | Registrar lote de importación con `raw_source_record` inmutable sin autopublicar. |
| `OP-09` (lectura) | `GET /api/v1/admin/import-batches/{id}` | `CURATOR`, `REVIEWER` | PostgreSQL | Consultar lote, métricas de aceptación y registros asociados. |
| `OP-10` (bandeja) | `GET /api/v1/admin/import-records` | `CURATOR`, `REVIEWER` | PostgreSQL | Listar registros `PENDING` y `UNRESOLVED` disponibles para curaduría. |
| `OP-10` | `PATCH /api/v1/admin/import-records/{id}/normalize` | `CURATOR` | PostgreSQL + MongoDB | Normalizar taxón canónico, guardar nombre original, regla y metadata sin tocar `raw_payload`. |
| `OP-11` | `POST /api/v1/admin/assertions` | `CURATOR` | PostgreSQL | Crear afirmación en `DRAFT` o `IN_REVIEW` con evidencia explícita (`RD-01`–`RD-06`). |
| `OP-11`/`OP-12` | `POST /api/v1/admin/assertions/{id}/submit` | `CURATOR` | PostgreSQL | Enviar afirmación `DRAFT` a revisión (`IN_REVIEW`). |
| `OP-12` | `POST /api/v1/admin/assertions/{id}/review` | `REVIEWER` | PostgreSQL (+ Outbox) | Aprobar (`PUBLISHED`), rechazar (`REJECTED`), retirar (`WITHDRAWN`) o reabrir (`IN_REVIEW`) en transacción ACID con `review_event`, `outbox_event` y `audit_log`. |
| `OP-13` | `PUT /api/v1/admin/taxa/{taxon_id}/dossier` | `CURATOR` | MongoDB + PostgreSQL | Actualizar `taxon_dossier` archivando versión previa en `taxon_dossier_versions` con control optimista y auditoría. |
| `OP-14` | `GET /api/v1/admin/provenance/{entity_type}/{id}` | `CURATOR`, `REVIEWER` | PostgreSQL + MongoDB | Reconstruir cronología completa (fuentes, lotes, normalizaciones, revisiones, outbox y versiones). |
| Reconciliación | `POST /api/v1/admin/reconcile` | `REVIEWER`, `ADMIN` | PostgreSQL + Neo4j + MongoDB | Reencolar `FAILED`/`DEAD_LETTER` del Outbox, reconciliar nodos en Neo4j y alinear versiones documentales. |

Para ejecutar el worker independiente del **Transactional Outbox** en local:
```powershell
.\.venv\Scripts\python.exe -m biolink_backend.workers.outbox_worker
```

> **Credenciales de referencia creadas por el seeder de desarrollo:** `curador1@biolink.org` (`CuratorBioLink2026!`) y `revisora1@biolink.org` (`ReviewerBioLink2026!`). En producción (`APP_ENV=production`) deben reemplazarse.

El contrato OpenAPI versionado para `biolink-frontend` se encuentra exportado en [`contracts/openapi-v1.json`](contracts/openapi-v1.json).

---

## 5. Ejecución de Pruebas

```powershell
.\.venv\Scripts\pytest
```

---

## 6. Documentación Interna
- [Arquitectura Modular](docs/architecture.md)
- [Configuración y Variables de Entorno](docs/configuration.md)
- [Seguridad y Control de Acceso](docs/security.md)
- [Conexiones y Resiliencia de Motores](docs/database_connections.md)
- [Guía de Desarrollo y Pruebas](docs/development.md)
# biolink-backend
