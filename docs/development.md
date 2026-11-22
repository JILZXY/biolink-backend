# Guía de Desarrollo y Pruebas (`biolink-backend`)

## 1. Requisitos Previos

- **Python:** 3.14 recomendado (`>=3.12,<3.16` soportado en `pyproject.toml`).
- **Docker Desktop / Docker Engine:** con soporte de Docker Compose v2 (`docker compose`).
- **Regla de aislamiento:** No ejecutar `biolink-benchmarking` al mismo tiempo que el stack de `biolink-backend` para no superar el techo global de 4 GB de RAM.

---

## 2. Configuración del Entorno Reproducible

```powershell
cd biolink-backend
python -m venv .venv
.\.venv\Scripts\python -m pip install --no-deps -r requirements.lock
.\.venv\Scripts\python -m pip install --no-deps --no-build-isolation -e .
```

Si agregas una dependencia verificada en `pyproject.toml`:
1. Instala en el entorno virtual con `pip install -e ".[dev]"`.
2. Regenera `requirements.lock` excluyendo la ruta editable local y líneas comentadas.
3. Ejecuta toda la suite de pruebas antes de commitear.

---

## 3. Convenciones de Código

- **No introduzcas un ORM:** Mantén el SQL parametrizado en `repositories/relational/`, las consultas PyMongo en `repositories/document/` y las sentencias Cypher en `repositories/graph/`.
- **Reglas en `domain/`:** Cualquier validación biológica o de transición de estados (`RD-01`–`RD-16`) debe implementarse primero en `src/biolink_backend/domain/` con pruebas unitarias sin dependencias externas.
- **Errores Controlados:** Lanza subclases de `BioLinkError` (`DomainValidationError`, `BusinessRuleViolationError`, `EntityNotFoundError`, `AuthenticationError`, `AuthorizationError`) para que el manejador global devuelva respuestas uniformes con `request_id`.

---

## 4. Ejecución de Pruebas

### Pruebas Unitarias y de Arranque (sin Docker requerido)
```powershell
.\.venv\Scripts\pytest -m "not live"
```

### Validación de Sintaxis Docker Compose
```powershell
copy .env.example .env
docker compose config --quiet
```

### Arranque del Stack Completo y Verificación en Vivo
```powershell
docker compose up -d --build
# Esperar healthchecks (docker compose ps)
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/live
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/ready
# Apagar al terminar
docker compose down
```
