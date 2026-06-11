# 🔧 Docker + ghcr.io: TLS Handshake Timeout Fix

## Problema

En ciertas regiones (ej. Bolivia), Docker Desktop no puede hacer pull de imágenes
desde `ghcr.io` (GitHub Container Registry). El error es:

```
Error response from daemon: failed to resolve reference "ghcr.io/immich-app/immich-server:release":
failed to do request: Head "https://ghcr.io/v2/.../manifests/release": net/http: TLS handshake timeout
```

## Causa

Docker Desktop usa un proxy interno `http.docker.internal:3128` para todas las
operaciones de pull. Este proxy tiene timeouts TLS más cortos que el sistema host.
Desde regiones con latencia alta a ghcr.io (>600ms ping), el handshake TLS expira
antes de completarse.

## Verificación

```bash
# Docker Hub funciona (registry-1.docker.io)
docker pull alpine

# ghcr.io falla
docker pull ghcr.io/immich-app/immich-server:release

# Pero el host sí puede conectar:
curl -I https://ghcr.io

# Y los contenedores también:
docker run --rm busybox wget -O- https://ghcr.io
```

## Solución: Pull Manual vía API

Usar un script Python que descarga las imágenes por HTTP directo (sin el proxy de Docker)
y las carga con `docker load`.

### Script: `pull-images.py`

```bash
# Uso:
python3 pull-images.py ghcr.io immich-app/immich-server release

# Flujo:
# 1. Obtiene token OAuth anónimo de ghcr.io/token
# 2. Descarga el manifest (maneja multi-arch, selecciona linux/amd64)
# 3. Descarga cada capa (blob) por HTTP
# 4. Construye OCI image layout
# 5. Empaqueta como .tar
# 6. docker load -i imagen.tar
# 7. docker tag para poner el nombre correcto
```

### ⚠️ Notas

- **Rate limiting**: ghcr.io limita requests anónimos. No descargues más de 2-3
  imágenes simultáneamente (HTTP 429 = esperar y reintentar)
- **Tags con @sha256**: El docker-compose.yml oficial incluye digests de pinned
  version. El script no los maneja — solo usa el tag base. Luego edita el
  compose para quitar `@sha256:...` de las líneas `image:`.
- **Tiempo**: Con ~1-2 MB/s, las 4 imágenes (~1.6 GB total) toman ~20-30 minutos

### Alternativas exploradas (no funcionaron)

| Enfoque | Resultado |
|---|---|
| `HTTP_PROXY=` docker pull | ❌ El daemon ignora variables de entorno del CLI |
| `NO_PROXY=ghcr.io` docker pull | ❌ El proxy es interno del daemon, no configurable |
| `docker --config` alternativo | ❌ Mismo daemon, mismo proxy |
| Modificar `daemon.json` DNS | ❌ Requiere reiniciar Docker Desktop |
| Instalar `crane` vía brew | ❌ Timeout de red también |
| Usar mirrors de Docker Hub | ⚠️ Existen pero son community, no oficiales |
| `docker run --rm -v /var/run/docker.sock docker:cli pull` | ❌ docker:cli también necesita pull primero |
