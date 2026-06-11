# 🖥️ Comandos Rápidos — Immich

## Iniciar / Detener

```bash
# Iniciar (sin intentar pull de internet)
cd /Users/chrismac/AndroidStudioProjects/immich && docker compose up -d --pull never

# Detener
docker compose down

# Reiniciar todo
docker compose restart
```

## Estado

```bash
# Ver todos los contenedores
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

# Ver logs en tiempo real
docker compose logs -f

# Logs de un servicio específico
docker compose logs -f immich-server

# Últimas 100 líneas
docker compose logs --tail=100
```

## Backup & Restore (S3)

```bash
# Backup completo (fotos + DB + config)
python3 backup-to-s3.py

# Solo ver qué subiría
python3 backup-to-s3.py --dry-run

# Solo base de datos
python3 backup-to-s3.py --db-only

# Solo fotos
python3 backup-to-s3.py --lib-only

# ─── Restauración (máquina nueva) ───

# Restauración completa
python3 restore-from-s3.py

# Ver qué haría
python3 restore-from-s3.py --dry-run

# Directorio personalizado
python3 restore-from-s3.py --dir ~/otro/dir

# Saltear descarga de imágenes
python3 restore-from-s3.py --skip-pull
```

## Imágenes Docker

```bash
# Ver imágenes locales
docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}'

# Descargar una imagen manualmente (si Docker pull funciona)
docker pull ghcr.io/immich-app/immich-server:release

# Descargar con el script alternativo (si Docker pull falla)
python3 AI/pull-images.py ghcr.io immich-app/immich-server release

# Cargar imágenes desde archivos .tar
docker load -i imagen.tar

# Etiquetar una imagen
docker tag ID_IMAGEN ghcr.io/immich-app/immich-server:release
```

## Acceso

```bash
# Web local
open http://localhost:2283

# Verificar que responde
curl -s http://localhost:2283 | head -5
```

## S3 Manual

```bash
# Ver qué hay en S3
aws s3 ls s3://immich-backup-photos-aa12c3/
aws s3 ls s3://immich-backup-photos-aa12c3/config/
aws s3 ls s3://immich-backup-photos-aa12c3/database/

# Bajar archivos de config manualmente
aws s3 cp s3://immich-backup-photos-aa12c3/config/.env .env
aws s3 cp s3://immich-backup-photos-aa12c3/config/docker-compose.yml docker-compose.yml

# Sincronizar fotos
aws s3 sync s3://immich-backup-photos-aa12c3/library ./library
```

## Troubleshooting

```bash
# ¿Qué puertos están ocupados?
lsof -i :2283
lsof -i :5432

# Espacio en disco de Docker
docker system df

# Limpiar imágenes no usadas
docker image prune -a

# ¿Docker Desktop está corriendo?
docker info 2>&1 | head -5

# Verificar conectividad a ghcr.io (host)
curl -I https://ghcr.io

# Verificar conectividad a ghcr.io (desde Docker)
docker run --rm busybox wget -O- https://ghcr.io
```
