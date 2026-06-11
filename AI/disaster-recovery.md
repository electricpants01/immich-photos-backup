# 🔥 Disaster Recovery — Restaurar Immich desde S3

> Escenario: perdiste tu Mac. Solo tenés el bucket S3.
> Objetivo: dejar Immich corriendo en una máquina nueva como si nada hubiera pasado.

---

## 🚀 Automático (1 comando)

```bash
# Restauración completa con un solo comando
python3 restore-from-s3.py

# Ver qué haría sin ejecutar
python3 restore-from-s3.py --dry-run

# Directorio personalizado
python3 restore-from-s3.py --dir ~/otro/dir
```

El script automático ejecuta:
1. Verifica Docker, AWS CLI, credenciales, bucket S3
2. Crea el directorio destino
3. Descarga `.env`, `docker-compose.yml`, `pull-images.py` desde S3 (fallback: GitHub)
4. Descarga las 4 imágenes Docker (con fallback a registry directo si `docker pull` falla)
5. `aws s3 sync` de todas las fotos
6. Busca el dump SQL más reciente, lo descarga, descomprime y restaura
7. `docker compose up -d --pull never`

---

## 📋 Requisitos previos

- Docker + Docker Compose instalados
- AWS CLI instalado y configurado (`aws configure`)
- Python 3 + boto3 (`pip3 install boto3`)

---

## 📖 Manual (paso a paso)

Si preferís hacerlo manualmente o debuggear:

### Paso 1 — Bajar archivos de config desde S3

```bash
mkdir ~/immich && cd ~/immich

# Bajar config desde S3
aws s3 cp s3://$S3_BUCKET/config/.env .env
aws s3 cp s3://$S3_BUCKET/config/docker-compose.yml docker-compose.yml
aws s3 cp s3://$S3_BUCKET/config/pull-images.py pull-images.py
```

Si no están en S3, bajalos de GitHub:
```bash
curl -L -o docker-compose.yml https://github.com/immich-app/immich/releases/latest/download/docker-compose.yml
curl -L -o .env https://github.com/immich-app/immich/releases/latest/download/example.env
```

### Paso 2 — Limpiar @sha256 del compose

```bash
python3 -c "
import re
with open('docker-compose.yml') as f: c = f.read()
c = re.sub(r'@sha256:[a-f0-9]+', '', c)
with open('docker-compose.yml', 'w') as f: f.write(c)
print('OK')
"
```

### Paso 3 — Bajar imágenes Docker

```bash
# Opción A: docker pull normal
docker compose pull

# Opción B: script alternativo (si docker pull falla)
python3 pull-images.py ghcr.io immich-app/immich-server release
python3 pull-images.py ghcr.io immich-app/immich-machine-learning release
python3 pull-images.py ghcr.io immich-app/postgres 14-vectorchord0.4.3-pgvectors0.2.0
docker pull docker.io/valkey/valkey:9
```

### Paso 4 — Sincronizar fotos

```bash
aws s3 sync s3://$S3_BUCKET/library ./library
```

### Paso 5 — Restaurar DB

```bash
# Bajar dump más reciente
aws s3 cp s3://$S3_BUCKET/database/immich-db-YYYY-MM-DD_HHMMSS.sql.gz .
gunzip immich-db-*.sql.gz

# Iniciar solo postgres
docker compose up -d database

# Esperar a que esté healthy (~30s)
docker ps --filter name=immich_postgres

# Restaurar
docker exec -i immich_postgres psql -U postgres -d immich < immich-db-*.sql
```

⚠️ Ignorá los errores de `DROP` al principio (normal, limpia tablas que no existen).

### Paso 6 — Arrancar Immich

```bash
docker compose up -d --pull never
open http://localhost:2283
```

---

## ⏱️ Tiempo estimado

| Paso | Automático | Manual |
|---|---|---|
| Verificar requisitos | 5s | 5s |
| Bajar config | 2s | 1 min |
| Pull de imágenes | 10-30 min | 10-30 min |
| `aws s3 sync` de fotos | 1-3 horas | 1-3 horas |
| Restaurar dump SQL | 1-2 min | 3-5 min |
| Arrancar Immich | 1 min | 1 min |
| **Total** | **~2-4 horas** | **~2-4 horas** |

---

## 📦 Lo que está en S3

| Dato | Path en S3 | Backup |
|---|---|---|
| Fotos originales | `library/` | ✅ `backup-to-s3.py` |
| Thumbnails | `library/thumbs/` | ✅ |
| Videos transcodificados | `library/encoded-video/` | ✅ |
| Base de datos | `database/immich-db-*.sql.gz` | ✅ |
| `.env` | `config/.env` | ✅ 🆕 |
| `docker-compose.yml` | `config/docker-compose.yml` | ✅ 🆕 |
| `pull-images.py` | `config/pull-images.py` | ✅ 🆕 |
| Immich-server config (settings) | en el dump SQL | ✅ |

**Nada se pierde.** Todo lo necesario para restaurar está en S3.
