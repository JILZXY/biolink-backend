# Seguridad, Sesiones y Control de Acceso (`biolink-backend`)

## 1. Modelo de Actores en BioLink V1

- **Sin Registro Público:** En la versión V1 los usuarios públicos consultan únicamente endpoints de lectura publicados y no crean cuentas ni sesiones.
- **Usuarios Internos Autenticados (`app_user`):** Las operaciones administrativas (lotes de ingestión, curaduría, revisión y publicación) están restringidas a cuentas internas aprovisionadas directamente en PostgreSQL con roles explícitos:
  - `CURATOR` (Curador de datos)
  - `REVIEWER` (Revisor/editor científico)
  - `ADMIN` (Administración de plataforma)

### Matriz de Permisos Validada en Backend (`OP-09` a `OP-14`)

El backend aplica `ensure_has_any_role(...)` tanto en las dependencias FastAPI como en [`AdminCurationService`](../src/biolink_backend/application/services/admin_curation_service.py); nunca confía únicamente en ocultamientos de interfaz.

| Operación | Curador (`CURATOR`) | Revisor (`REVIEWER`) | Admin (`ADMIN`) | Notas de Regla |
|---|---|---|---|---|
| `OP-09` Registrar lote de importación | Permitido (`201`) | Denegado (`403`) | Denegado (`403`) | Conserva `raw_source_record` y prohíbe autopublicación. |
| `OP-10` Validar / normalizar registros | Permitido (`200`) | Denegado (`403`) | Denegado (`403`) | Registra regla, taxón o deja `UNRESOLVED` para revisión. |
| `OP-11` Crear afirmación (`DRAFT`/`IN_REVIEW`) | Permitido (`201`) | Denegado (`403`) | Denegado (`403`) | Exige evidencia (`RD-01`/`RD-02`) y animal->planta V1 (`RD-03`). |
| Preparar / enviar a revisión (`submit`) | Permitido (`200`) | Denegado (`403`) | Denegado (`403`) | Solo `DRAFT -> IN_REVIEW`. |
| `OP-12` Aprobar, rechazar, retirar, reabrir | **Denegado (`403`)** | **Permitido (`200`)** | Denegado (`403`) | Un curador no puede autoaprobar publicaciones científicas. |
| `OP-13` Actualizar ficha de taxón | Permitido (`200`) | Denegado (`403`) | Denegado (`403`) | Archiva versión previa en `taxon_dossier_versions`. |
| `OP-14` Consultar historial de procedencia | Permitido (`200`) | Permitido (`200`) | Permitido (`200`) | Reconstruye fuentes, lotes, revisiones y outbox. |
| Reconciliación multi-motor | Denegado (`403`) | Permitido (`200`) | Permitido (`200`) | Recupera Outbox/Neo4j y alinea `taxon_dossier`. |

---

## 2. Almacenamiento Seguro de Contraseñas

Implementado en [`src/biolink_backend/security/passwords.py`](../src/biolink_backend/security/passwords.py):
- **Prohibición de texto plano:** Nunca se almacenan contraseñas sin hash ni se registran en logs.
- **Algoritmo:** `PBKDF2-HMAC-SHA256` con `600,000` iteraciones y salt criptográfico aleatorio de `16 bytes` por contraseña (`$pbkdf2-sha256$600000$<salt_b64>$<hash_b64>`).
- **Verificación en tiempo constante:** Uso estricto de `hmac.compare_digest` para mitigar ataques de temporización.

---

## 3. Estrategia de Sesiones y Prohibición de `localStorage`

Implementado en [`src/biolink_backend/security/tokens.py`](../src/biolink_backend/security/tokens.py) y [`src/biolink_backend/api/dependencies.py`](../src/biolink_backend/api/dependencies.py):

1. **Sesiones Stateful en PostgreSQL (`user_session`):**
   - Al iniciar sesión exitosamente, el servidor genera un token opaco de 256 bits (`secrets.token_urlsafe(32)`).
   - En PostgreSQL **únicamente se guarda el digest SHA-256** (`user_session.token_hash`), nunca el token en claro, de modo que una fuga de solo lectura en la tabla no compromete sesiones activas.
   - Permite revocación inmediata (`revoked_at = clock_timestamp()`) por cierre de sesión o incidente de seguridad.
2. **Transporte Recomendado (`HttpOnly` Cookie):**
   - Los clientes web (`biolink-frontend`) reciben y transmiten la sesión mediante cookie con atributos `HttpOnly`, `SameSite=Lax/Strict` y `Secure=true` en producción (`SESSION_COOKIE_SECURE=true`).
   - **Prohibición de `localStorage`:** No se diseña ni expone flujo para guardar tokens en `localStorage` o `sessionStorage` del navegador, protegiendo al usuario frente a ataques XSS.
   - Como mecanismo secundario para clientes de servicio o pruebas automatizadas, el backend acepta `Authorization: Bearer <token>`, validándolo contra el mismo registro `user_session`.

---

## 4. Endurecimiento HTTP y Red

- **Cabeceras de Seguridad (`RequestContextMiddleware`):** `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `Cache-Control: no-store` y trazabilidad `X-Request-ID`.
- **CORS Restringido:** Sólo orígenes explícitamente enumerados en `CORS_ALLOWED_ORIGINS` (sin comodín `*`).
- **Aislamiento de Red:** En Docker Compose, los motores de bases de datos se comunican por `biolink_backend_net` y los puertos de desarrollo se enlazan únicamente a `127.0.0.1`.
