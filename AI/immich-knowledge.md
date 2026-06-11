# 🖼️ Immich — Guía Completa de Instalación y Uso

> **Proyecto**: `/Users/chrismac/AndroidStudioProjects/immich`
> **Fecha**: Junio 2026
> **Immich**: Self-hosted photo & video management (Google Photos alternative)

---

## 📦 Arquitectura

Immich corre sobre 4 contenedores Docker:

| Servicio | Imagen | Puerto | Función |
|---|---|---|---|
| `immich-server` | `ghcr.io/immich-app/immich-server:release` | **2283** | Web + API principal |
| `immich-machine-learning` | `ghcr.io/immich-app/immich-machine-learning:release` | interno | ML: rostros, objetos, CLIP |
| `immich_postgres` | `ghcr.io/immich-app/postgres:14-vectorchord...` | interno (5432) | Base de datos con pgvectors |
| `immich_redis` | `docker.io/valkey/valkey:9` | interno (6379) | Caché y colas |

---

## ⚙️ Archivos de Configuración

### `.env` (variables de entorno)
```env
UPLOAD_LOCATION=./library          # Donde se guardan las fotos/videos
DB_DATA_LOCATION=./postgres        # Datos de la base de datos (NO usar network shares)
TZ=America/La_Paz                  # Zona horaria (La Paz, Bolivia)
IMMICH_VERSION=release             # Versión de Immich
DB_PASSWORD=postgres               # ⚠️ CAMBIAR por contraseña segura (solo A-Za-z0-9)
DB_USERNAME=postgres
DB_DATABASE_NAME=immich
```

### `docker-compose.yml`
Define los 4 servicios. Configuración clave:
- El volumen `UPLOAD_LOCATION` se monta en `/data` dentro de immich-server
- El puerto `2283:2283` expone la interfaz web
- `restart: always` en todos los servicios
- Healthchecks habilitados para monitoreo

---

## 🚀 Comandos de Operación

```bash
# Iniciar Immich
cd /Users/chrismac/AndroidStudioProjects/immich
docker compose up -d --pull never

# Ver estado
docker compose ps
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

# Ver logs
docker compose logs -f immich-server
docker compose logs -f --tail=50   # últimos 50 líneas de todos

# Detener
docker compose down

# Detener y borrar volúmenes (⚠️ borra datos!)
docker compose down -v

# Reiniciar un servicio
docker compose restart immich-server

# Actualizar (cuando haya nueva versión)
docker compose pull
docker compose up -d
```

---

## 📸 Cómo Subir Imágenes

### 1. App Móvil (RECOMENDADO)
- iOS: App Store → "Immich"
- Android: Google Play → "Immich"
- URL del servidor: `http://localhost:2283` (misma red WiFi)
  - O usar IP local: `http://192.168.x.x:2283`
- Backup automático, álbumes selectivos, background sync

### 2. Vía Web
- Abrir `http://localhost:2283`
- Crear cuenta admin (primer inicio)
- Drag & drop de imágenes o botón Upload

### 3. CLI (Bulk Import desde PC)
```bash
npm install -g @immich/cli
immich login http://localhost:2283 TU_API_KEY
immich upload /ruta/a/mis/fotos/
```
> API Key: se genera en Settings → API Keys (desde la web)

### ⚠️ Importante
**NO copies archivos manualmente a `./library/`**. Immich gestiona su propia estructura interna.

---

## 🔧 Troubleshooting

### Docker no puede hacer pull de ghcr.io
**Síntoma**: `TLS handshake timeout` al hacer pull de imágenes de `ghcr.io`
**Causa**: Docker Desktop tiene un proxy interno (`http.docker.internal:3128`) que falla con ghcr.io desde ciertas regiones (Bolivia)

**Solución**: Script Python para descargar imágenes vía API directa → `docker load`
```bash
python3 /tmp/pull_one.py ghcr.io immich-app/immich-server release
```
> Ver script completo en `scripts/pull-images.py`

### Contenedores no inician
```bash
# Ver logs específicos
docker compose logs immich-server
docker compose logs database

# Verificar que los puertos no están ocupados
lsof -i :2283
lsof -i :5432
```

### La web no carga
- Esperar ~15-20 segundos después del inicio (el servidor tarda en inicializar)
- Verificar: `curl -s http://localhost:2283 | head -5`
- Debe devolver HTML de la página de login

---

## 📊 Tamaños de Imágenes Docker

| Imagen | Tamaño |
|---|---|
| immich-server | ~785 MB |
| immich-machine-learning | ~468 MB |
| postgres | ~205 MB |
| valkey/redis | ~187 MB |
| **Total** | **~1.6 GB** |

---

## 🔐 Seguridad

- ⚠️ **Cambiar `DB_PASSWORD`** en `.env` (solo A-Za-z0-9)
- El puerto 2283 solo está expuesto localmente
- Para acceso remoto seguro: usar VPN (WireGuard/Tailscale) o reverse proxy con HTTPS (Nginx + Let's Encrypt)
- Immich soporta OAuth para login

---

## 🔄 Backup & Restore (S3)

El proyecto incluye scripts Python para backup incremental a S3 y restauración completa.

### Backup → S3

```bash
# Backup completo (fotos + base de datos + archivos de config)
python3 backup-to-s3.py

# Solo ver qué subiría (sin subir nada)
python3 backup-to-s3.py --dry-run

# Solo base de datos
python3 backup-to-s3.py --db-only

# Solo fotos
python3 backup-to-s3.py --lib-only
```

Qué sube a `s3://$S3_BUCKET/` (definido en `.env`):
| Path en S3 | Contenido | Frecuencia |
|---|---|---|
| `library/` | Fotos, thumbs, encoded videos | Incremental (solo archivos nuevos/cambiados) |
| `database/immich-db-*.sql.gz` | pg_dump comprimido con `--clean --if-exists` | Completo (mantiene últimos 7) |
| `config/.env` | Variables de entorno | Cada backup |
| `config/docker-compose.yml` | Docker Compose config | Cada backup |
| `config/pull-images.py` | Script alternativo de pull | Cada backup |

### Restore ← S3 (Disaster Recovery)

```bash
# Restauración completa (1 comando)
python3 restore-from-s3.py

# Ver qué haría
python3 restore-from-s3.py --dry-run
```

El script automático:
1. Verifica requisitos (Docker, AWS CLI, credenciales)
2. Baja config desde S3 (fallback: GitHub)
3. Descarga imágenes Docker (fallback: registry directo)
4. `aws s3 sync` de todas las fotos
5. Restaura el dump SQL más reciente
6. Inicia Immich

> Ver `AI/disaster-recovery.md` para la guía detallada paso a paso.

### Regla 3-2-1

Immich recomienda seguir la regla **3-2-1** de backup:
- **3** copias de tus datos
- **2** medios diferentes
- **1** copia off-site

Con S3 tenés la copia off-site. Para la segunda copia local, podés usar Time Machine o un disco externo.

---

## 📚 Recursos

- [Documentación oficial](https://docs.immich.app/)
- [GitHub](https://github.com/immich-app/immich)
- [Demo](https://demo.immich.app) (user: `demo@immich.app` / pass: `demo`)
- [Guía de instalación](https://docs.immich.app/install/docker-compose)
