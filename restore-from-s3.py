#!/usr/bin/env python3
"""
🔥 Immich Disaster Recovery — Restaurar desde S3
Restaura completamente Immich desde un backup en S3 en una máquina nueva.

Uso:
  python3 restore-from-s3.py                    # Restauración completa
  python3 restore-from-s3.py --dry-run          # Solo ver qué haría
  python3 restore-from-s3.py --dir ~/immich2    # Directorio destino
  python3 restore-from-s3.py --skip-pull        # Saltear pull de imágenes
  python3 restore-from-s3.py --skip-photos      # Saltear fotos
  python3 restore-from-s3.py --skip-db          # Saltear base de datos
"""

import os, sys, time, argparse, subprocess, tempfile, shutil, re, json, hashlib
from pathlib import Path
from datetime import datetime

import urllib.request, urllib.error
import boto3
from botocore.exceptions import ClientError

PROJECT_DIR = Path(__file__).resolve().parent


def load_env():
    """Carga variables del .env del proyecto."""
    env_file = PROJECT_DIR / ".env"
    if not env_file.exists():
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


load_env()
BUCKET_NAME = os.environ.get("S3_BUCKET")
if not BUCKET_NAME:
    print("ERROR: S3_BUCKET no está definido en .env")
    sys.exit(1)

DEFAULT_DIR = os.path.expanduser("~/immich")

# Colores ANSI
C = {
    "R": "\033[91m", "G": "\033[92m", "Y": "\033[93m",
    "B": "\033[94m", "M": "\033[95m", "C": "\033[96m",
    "W": "\033[0m", "D": "\033[90m"
}


def log(msg, emoji="📋", style=""):
    t = datetime.now().strftime("%H:%M:%S")
    print(f"{style}[{t}] {emoji}  {msg}{C['W']}", flush=True)

def ok(msg):   log(msg, "✅", C["G"])
def info(msg): log(msg, "ℹ️", C["C"])
def warn(msg): log(msg, "⚠️", C["Y"])
def err(msg):  log(msg, "❌", C["R"])
def step(n, msg):
    print(f"\n{C['B']}━━━ Paso {n}: {msg}{C['W']}")
    print(f"{C['D']}{'─' * 60}{C['W']}")


# ================================================================
#  VERIFICACIONES INICIALES
# ================================================================

def check_prerequisites():
    """Verifica que Docker, AWS CLI, y boto3 estén disponibles."""
    log("Verificando requisitos...", "🔍")

    if sys.version_info < (3, 8):
        err("Necesitás Python 3.8+")
        sys.exit(1)
    ok(f"Python {sys.version_info.major}.{sys.version_info.minor}")

    # Docker
    try:
        r = subprocess.run(["docker", "--version"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            ok(f"Docker: {r.stdout.strip()}")
        else:
            err("Docker no encontrado. Instalalo: https://docs.docker.com/get-docker/")
            sys.exit(1)
    except FileNotFoundError:
        err("Docker no encontrado.")
        sys.exit(1)

    # Docker Compose
    try:
        r = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            ok("Docker Compose disponible")
        else:
            subprocess.run(["docker-compose", "--version"], check=True, capture_output=True, timeout=5)
            ok("docker-compose disponible")
    except (FileNotFoundError, subprocess.CalledProcessError):
        err("Docker Compose no encontrado.")
        sys.exit(1)

    # boto3 + AWS creds
    session = boto3.Session()
    if not session.get_credentials():
        err("No hay credenciales AWS. Ejecutá: aws configure")
        sys.exit(1)
    ok("Credenciales AWS OK")

    # aws CLI
    try:
        subprocess.run(["aws", "--version"], capture_output=True, text=True, timeout=5, check=True)
        ok("AWS CLI disponible")
    except (FileNotFoundError, subprocess.CalledProcessError):
        err("AWS CLI no encontrado. Instalalo: brew install awscli")
        sys.exit(1)

    # Bucket S3
    s3 = session.client("s3")
    try:
        s3.head_bucket(Bucket=BUCKET_NAME)
        ok(f"Bucket S3 '{BUCKET_NAME}' OK")
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            err(f"Bucket '{BUCKET_NAME}' no existe.")
            sys.exit(1)
        raise


# ================================================================
#  PULL DE IMÁGENES (lógica de pull-images.py)
# ================================================================

def registry_req(url, headers=None, timeout=60):
    r = urllib.request.Request(url, headers=headers or {})
    return urllib.request.urlopen(r, timeout=timeout)


def registry_download(path, url, headers, desc):
    if os.path.exists(path):
        log(f"  {desc} (cached)")
        return
    log(f"  {desc}...", "⏳")
    r = urllib.request.Request(url, headers=headers)
    total = 0
    with urllib.request.urlopen(r, timeout=300) as resp:
        with open(path, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                total += len(chunk)
    log(f"  {desc} → {total // 1024 // 1024} MB", "✅")


def pull_image_direct(registry, repo, tag):
    """Descarga una imagen directamente del registry y la carga en Docker."""
    log(f"Descargando {repo}:{tag}...", "🐳")

    realm_url = f"https://{registry}/v2/"
    try:
        registry_req(realm_url)
    except urllib.error.HTTPError as e:
        auth = e.headers.get("Www-Authenticate", "")
        realm = re.search(r'realm="([^"]+)"', auth)
        service = re.search(r'service="([^"]+)"', auth)
        if not realm:
            err(f"No se pudo obtener realm/auth del registry {registry}")
            return False

    token_url = f"{realm.group(1)}?service={service.group(1)}&scope=repository:{repo}:pull"
    with registry_req(token_url) as r:
        token = json.loads(r.read())["token"]

    accept = ("application/vnd.oci.image.index.v1+json, "
              "application/vnd.docker.distribution.manifest.list.v2+json, "
              "application/vnd.docker.distribution.manifest.v2+json, "
              "application/vnd.oci.image.manifest.v1+json")
    auth_headers = {"Authorization": f"Bearer {token}", "Accept": accept}

    # Get manifest
    with registry_req(f"https://{registry}/v2/{repo}/manifests/{tag}", auth_headers) as r:
        manifest = json.loads(r.read())

    # Handle manifest list
    if "manifests" in manifest:
        selected = None
        for m in manifest["manifests"]:
            p = m.get("platform", {})
            if p.get("os") == "linux" and (p.get("architecture") in ("amd64", "arm64")):
                selected = m
                break
        if not selected:
            selected = manifest["manifests"][0]
        log(f"  Platform: {selected.get('platform', {})}")
        with registry_req(f"https://{registry}/v2/{repo}/manifests/{selected['digest']}", auth_headers) as r:
            manifest = json.loads(r.read())

    layers = manifest.get("layers", [])
    config = manifest.get("config", {})
    log(f"  Layers: {len(layers)}")

    tmpdir = tempfile.mkdtemp(prefix="img_")
    blobs = os.path.join(tmpdir, "blobs", "sha256")
    os.makedirs(blobs, exist_ok=True)

    # Download config
    if config.get("digest"):
        h = config["digest"].split(":")[1]
        registry_download(
            os.path.join(blobs, h),
            f"https://{registry}/v2/{repo}/blobs/{config['digest']}",
            auth_headers, f"Config {h[:12]}"
        )

    # Download layers
    for i, layer in enumerate(layers):
        h = layer["digest"].split(":")[1]
        registry_download(
            os.path.join(blobs, h),
            f"https://{registry}/v2/{repo}/blobs/{layer['digest']}",
            auth_headers, f"Layer {i+1}/{len(layers)} {h[:12]}"
        )

    # Write manifest
    mraw = json.dumps(manifest).encode()
    mhash = hashlib.sha256(mraw).hexdigest()
    with open(os.path.join(blobs, mhash), "wb") as f:
        f.write(mraw)

    # OCI layout
    with open(os.path.join(tmpdir, "oci-layout"), "w") as f:
        json.dump({"imageLayoutVersion": "1.0.0"}, f)

    # index.json
    with open(os.path.join(tmpdir, "index.json"), "w") as f:
        json.dump({
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [{
                "mediaType": manifest.get("mediaType", "application/vnd.oci.image.manifest.v1+json"),
                "digest": f"sha256:{mhash}",
                "size": len(mraw),
                "annotations": {"org.opencontainers.image.ref.name": tag}
            }]
        }, f, indent=2)

    # Create tar
    import tarfile
    safe_name = f"{repo.replace('/','_')}_{tag}.tar"
    tar_path = os.path.join(tempfile.gettempdir(), safe_name)
    log(f"  Empaquetando...", "📦")
    with tarfile.open(tar_path, "w") as tar:
        tar.add(tmpdir, arcname=".")
    log(f"  Tar: {os.path.getsize(tar_path) // 1024 // 1024} MB")

    # Load into Docker
    log(f"  Cargando en Docker...", "⏳")
    r = subprocess.run(
        ["docker", "load", "-i", tar_path],
        capture_output=True, text=True, timeout=120
    )
    shutil.rmtree(tmpdir, ignore_errors=True)
    os.remove(tar_path)

    if r.returncode != 0:
        err(f"  docker load falló: {r.stderr[:300]}")
        return False
    ok(f"  {r.stdout.strip()}")
    return True


def try_docker_pull(image):
    """Intenta docker pull normal primero. Si falla, usa pull directo."""
    log(f"Probando docker pull {image}...", "🐳")
    try:
        r = subprocess.run(
            ["docker", "pull", image],
            capture_output=True, text=True, timeout=180
        )
        if r.returncode == 0:
            ok(f"docker pull {image} OK")
            return True
        else:
            warn(f"docker pull falló: {r.stderr.strip()[:200]}")
    except subprocess.TimeoutExpired:
        warn("docker pull timeout")

    # Fallback: pull directo
    parts = image.split("/")
    if len(parts) >= 2:
        registry = parts[0]
        rest = "/".join(parts[1:])
        repo, tag = rest.split(":") if ":" in rest else (rest, "latest")
        info(f"Usando pull directo del registry {registry}...")
        return pull_image_direct(registry, repo, tag)

    err(f"No se pudo parsear la imagen: {image}")
    return False


def pull_all_images():
    """Descarga todas las imágenes necesarias para Immich."""
    images = [
        "ghcr.io/immich-app/immich-server:release",
        "ghcr.io/immich-app/immich-machine-learning:release",
        "ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0",
        "docker.io/valkey/valkey:9",
    ]
    ok_count = 0
    for img in images:
        log(f"\n{'─'*50}", style=C["D"])
        if try_docker_pull(img):
            ok_count += 1
        else:
            err(f"No se pudo descargar {img}")
    log(f"\nImágenes descargadas: {ok_count}/{len(images)}")
    return ok_count == len(images)


# ================================================================
#  DESCARGAR docker-compose.yml Y .env
# ================================================================

def download_compose_files(target_dir, dry_run=False):
    """Baja archivos de config desde S3 (con fallback a GitHub)."""
    compose_path = target_dir / "docker-compose.yml"
    env_path = target_dir / ".env"
    pull_script_path = target_dir / "pull-images.py"

    if dry_run:
        log(f"[DRY] Intentaría bajar config de S3 (fallback: GitHub)")
        return

    s3 = boto3.client("s3")

    def try_s3_download(key, local_path, desc):
        """Intenta bajar de S3, returns True si éxito."""
        try:
            s3.download_file(BUCKET_NAME, f"config/{key}", str(local_path))
            ok(f"{desc} → desde S3 ({local_path.stat().st_size} bytes)")
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                warn(f"{desc} → no está en S3, intentando GitHub...")
            else:
                warn(f"{desc} → error S3: {e}")
            return False
        except Exception as e:
            warn(f"{desc} → error: {e}")
            return False

    # ── docker-compose.yml ──
    log("Obteniendo docker-compose.yml...")
    if not try_s3_download("docker-compose.yml", compose_path, "docker-compose.yml"):
        compose_url = "https://github.com/immich-app/immich/releases/latest/download/docker-compose.yml"
        try:
            urllib.request.urlretrieve(compose_url, str(compose_path))
            ok(f"docker-compose.yml → desde GitHub ({compose_path.stat().st_size} bytes)")
        except Exception as e:
            err(f"No se pudo descargar docker-compose.yml: {e}")
            sys.exit(1)

    # Limpiar @sha256:
    content = compose_path.read_text()
    cleaned = re.sub(r'@sha256:[a-f0-9]+', '', content)
    compose_path.write_text(cleaned)
    if "@sha256" in compose_path.read_text():
        warn("Aún hay @sha256 en docker-compose.yml, verificá manualmente")
    else:
        ok("docker-compose.yml limpio (sin pins @sha256)")

    # ── .env ──
    log("Obteniendo .env...")
    if not try_s3_download(".env", env_path, ".env"):
        env_url = "https://github.com/immich-app/immich/releases/latest/download/example.env"
        try:
            urllib.request.urlretrieve(env_url, str(env_path))
            ok(f".env → desde GitHub ({env_path.stat().st_size} bytes)")
        except Exception as e:
            err(f"No se pudo descargar .env: {e}")
            sys.exit(1)

    # Asegurar config mínima
    env_content = env_path.read_text()
    defaults = {
        "UPLOAD_LOCATION": "./library",
        "DB_DATA_LOCATION": "./postgres",
        "IMMICH_VERSION": "release",
        "DB_PASSWORD": "postgres",
        "DB_USERNAME": "postgres",
        "DB_DATABASE_NAME": "immich",
    }
    for key, val in defaults.items():
        if f"{key}=" not in env_content:
            env_content += f"\n{key}={val}\n"
            info(f"  Agregado: {key}={val}")

    if "TZ=" not in env_content:
        env_content += "\nTZ=America/La_Paz\n"
        info("  Agregado: TZ=America/La_Paz")

    env_path.write_text(env_content)
    ok(".env configurado")

    # ── pull-images.py ──
    log("Obteniendo pull-images.py...")
    if not try_s3_download("pull-images.py", pull_script_path, "pull-images.py"):
        warn("pull-images.py no está en S3 y no hay fallback. Salteando.")
    else:
        os.chmod(pull_script_path, 0o755)


# ================================================================
#  SINCRONIZAR FOTOS DESDE S3
# ================================================================

def sync_photos(target_dir, dry_run=False):
    """Sincroniza library/ desde S3 usando aws s3 sync."""
    local_lib = target_dir / "library"

    if dry_run:
        log(f"[DRY] aws s3 sync s3://{BUCKET_NAME}/library {local_lib}")
        return

    log("Sincronizando fotos desde S3...", "📸")
    log(f"  Origen:  s3://{BUCKET_NAME}/library")
    log(f"  Destino: {local_lib}")
    log("  ⏳ Esto puede tomar 1-3 horas. El sync es reanudable si se corta.")

    start = time.time()
    try:
        result = subprocess.run(
            ["aws", "s3", "sync", f"s3://{BUCKET_NAME}/library", str(local_lib)],
            timeout=10800  # 3 horas
        )
        elapsed = time.time() - start
        m, s = int(elapsed // 60), int(elapsed % 60)

        if result.returncode == 0:
            files = [f for f in local_lib.rglob("*") if f.is_file()] if local_lib.exists() else []
            total = len(files)
            size = sum(f.stat().st_size for f in files)
            ok(f"Fotos sincronizadas: {total} archivos, {size/1024/1024/1024:.1f} GB en {m}m {s}s")
        else:
            warn(f"aws s3 sync terminó con código {result.returncode}. Revisá logs arriba.")
    except subprocess.TimeoutExpired:
        warn("Sync interrumpido por timeout (3h). Re-ejecutá, es reanudable.")
    except FileNotFoundError:
        err("aws CLI no encontrado. Instalalo: brew install awscli")
        sys.exit(1)


# ================================================================
#  RESTAURAR BASE DE DATOS
# ================================================================

def restore_database(target_dir, dry_run=False):
    """Baja el dump más reciente de S3 y lo restaura en postgres."""

    if dry_run:
        log(f"[DRY] Buscaría el dump más reciente en S3 y lo restauraría")
        return True

    s3 = boto3.client("s3")

    log("Buscando dumps en S3...", "🔍")
    objs = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix="database/immich-db-")
    if not objs.get("Contents"):
        warn("No hay dumps de base de datos en S3. Salteando.")
        return False

    sorted_objs = sorted(objs["Contents"], key=lambda x: x["LastModified"], reverse=True)
    latest = sorted_objs[0]
    dump_key = latest["Key"]
    dump_name = dump_key.split("/")[-1]

    log(f"Dump más reciente: {dump_name}")
    log(f"  Fecha:  {latest['LastModified']}")
    log(f"  Tamaño: {latest['Size'] / 1024 / 1024:.1f} MB")

    # Descargar
    dump_path = target_dir / dump_name
    log(f"Descargando {dump_name}...")
    s3.download_file(BUCKET_NAME, dump_key, str(dump_path))
    ok(f"Dump descargado: {dump_path.stat().st_size / 1024 / 1024:.1f} MB")

    # Descomprimir
    import gzip
    sql_path = dump_path.with_suffix("")
    log("Descomprimiendo...")
    with gzip.open(dump_path, "rb") as f_in:
        with open(sql_path, "wb") as f_out:
            f_out.write(f_in.read())
    ok(f"SQL descomprimido: {sql_path.stat().st_size / 1024 / 1024:.1f} MB")

    # Arrancar solo postgres
    log("Iniciando solo postgres...")
    os.chdir(target_dir)
    subprocess.run(["docker", "compose", "down"], capture_output=True, timeout=30)

    r = subprocess.run(
        ["docker", "compose", "up", "-d", "database"],
        capture_output=True, text=True, timeout=60
    )
    if r.returncode != 0:
        err(f"No se pudo iniciar postgres: {r.stderr[:300]}")
        return False

    # Esperar a que postgres esté listo
    log("Esperando que postgres esté listo...")
    for i in range(30):
        time.sleep(2)
        r = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}",
             "--filter", "name=immich_postgres"],
            capture_output=True, text=True, timeout=5
        )
        if "healthy" in r.stdout.lower() or "Up" in r.stdout:
            ok("Postgres listo")
            break
        if i % 5 == 0:
            log(f"  Esperando... ({i*2}s)")

    time.sleep(3)

    # Restaurar dump
    log("Restaurando base de datos...", "🗄️")
    log("  ⏳ Esto puede tomar unos minutos...")

    with open(sql_path, "rb") as f:
        r = subprocess.run(
            ["docker", "exec", "-i", "immich_postgres",
             "psql", "-U", "postgres", "-d", "immich"],
            stdin=f,
            capture_output=True, text=True, timeout=300
        )

    # Ignorar errores normales de DROP (--clean)
    stderr_filtered = "\n".join(
        line for line in r.stderr.split("\n")
        if "does not exist" not in line.lower()
        and "skipping" not in line.lower()
    )
    if stderr_filtered.strip():
        warn(f"Algunos avisos durante restauración (normal): {stderr_filtered[:300]}")

    if r.returncode != 0 and "ERROR" in r.stderr.upper():
        err(f"Error crítico en restauración: {r.stderr[:500]}")
        return False

    ok("Base de datos restaurada")

    # Limpiar temporales
    dump_path.unlink(missing_ok=True)
    sql_path.unlink(missing_ok=True)

    return True


# ================================================================
#  ARRANCAR IMMICH
# ================================================================

def start_immich(target_dir, dry_run=False):
    """Arranca todos los servicios de Immich."""
    if dry_run:
        log(f"[DRY] docker compose up -d --pull never en {target_dir}")
        return

    log("Iniciando Immich completo...", "🚀")
    os.chdir(target_dir)

    r = subprocess.run(
        ["docker", "compose", "up", "-d", "--pull", "never"],
        capture_output=True, text=True, timeout=120
    )

    if r.returncode != 0:
        err(f"Error al iniciar Immich: {r.stderr[:500]}")
        return False

    ok("Immich iniciado")

    # Verificar contenedores
    time.sleep(5)
    r = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}", "--filter", "name=immich"],
        capture_output=True, text=True, timeout=5
    )

    containers = r.stdout.strip().split("\n")
    log(f"\nContenedores corriendo: {len(containers)}")
    for c in containers:
        if c:
            log(f"  🟢 {c}")

    log(f"\n{'='*60}", style=C["B"])
    log(f"  🎉  Immich debería estar en: http://localhost:2283")
    log(f"{'='*60}\n", style=C["B"])

    return True


# ================================================================
#  MAIN
# ================================================================

def main():
    parser = argparse.ArgumentParser(
        description="🔥 Immich Disaster Recovery — Restaurar desde S3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python3 restore-from-s3.py                     # Restauración completa
  python3 restore-from-s3.py --dry-run           # Solo ver qué haría
  python3 restore-from-s3.py --dir ~/immich2     # Directorio destino
  python3 restore-from-s3.py --skip-pull         # Saltear pull de imágenes
  python3 restore-from-s3.py --skip-photos       # Saltear fotos
  python3 restore-from-s3.py --skip-db           # Saltear base de datos
        """
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Solo mostrar qué se haría, sin ejecutar")
    parser.add_argument("--dir", type=str, default=DEFAULT_DIR,
                        help=f"Directorio destino (default: {DEFAULT_DIR})")
    parser.add_argument("--skip-pull", action="store_true",
                        help="Saltear descarga de imágenes Docker")
    parser.add_argument("--skip-photos", action="store_true",
                        help="Saltear sincronización de fotos")
    parser.add_argument("--skip-db", action="store_true",
                        help="Saltear restauración de base de datos")
    parser.add_argument("--no-start", action="store_true",
                        help="No iniciar Immich al final")
    args = parser.parse_args()

    target_dir = Path(args.dir).expanduser().resolve()

    print(f"\n{C['M']}╔{'═' * 58}╗")
    print(f"║{'🔥  IMMICH DISASTER RECOVERY'.center(58)}║")
    print(f"╚{'═' * 58}╝{C['W']}\n")

    info(f"Bucket S3:  {BUCKET_NAME}")
    info(f"Destino:    {target_dir}")
    info(f"Fecha:      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    if args.dry_run:
        warn("MODO DRY-RUN — no se ejecuta nada, solo se muestra qué haría")
    if args.skip_pull:
        warn("Saltando pull de imágenes (--skip-pull)")
    if args.skip_photos:
        warn("Saltando fotos (--skip-photos)")
    if args.skip_db:
        warn("Saltando base de datos (--skip-db)")

    # ── Paso 1: Verificar requisitos ──
    step(1, "Verificar requisitos")
    if not args.dry_run:
        check_prerequisites()
    else:
        log("[DRY] Verificaría Docker, AWS CLI, credenciales, bucket S3")

    # ── Paso 2: Preparar directorio ──
    step(2, "Preparar directorio")
    if not args.dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)
        ok(f"Directorio creado: {target_dir}")
    else:
        log(f"[DRY] Crearía {target_dir}")

    # ── Paso 3: Descargar docker-compose.yml y .env ──
    step(3, "Descargar docker-compose.yml y .env")
    download_compose_files(target_dir, dry_run=args.dry_run)

    # ── Paso 4: Pull de imágenes ──
    if not args.skip_pull:
        step(4, "Descargar imágenes Docker")
        if not args.dry_run:
            if not pull_all_images():
                warn("Algunas imágenes no se pudieron descargar. Intentá docker compose pull manualmente.")
        else:
            log("[DRY] Descargaría 4 imágenes Docker")
    else:
        step(4, "Descargar imágenes Docker [SALTEADO]")

    # ── Paso 5: Sincronizar fotos ──
    if not args.skip_photos:
        step(5, "Sincronizar fotos desde S3")
        sync_photos(target_dir, dry_run=args.dry_run)
    else:
        step(5, "Sincronizar fotos desde S3 [SALTEADO]")

    # ── Paso 6: Restaurar base de datos ──
    if not args.skip_db:
        step(6, "Restaurar base de datos")
        restore_database(target_dir, dry_run=args.dry_run)
    else:
        step(6, "Restaurar base de datos [SALTEADO]")

    # ── Paso 7: Iniciar Immich ──
    if not args.no_start:
        step(7, "Iniciar Immich")
        start_immich(target_dir, dry_run=args.dry_run)
    else:
        step(7, "Iniciar Immich [SALTEADO]")
        log(f"Para iniciar manualmente: cd {target_dir} && docker compose up -d --pull never")

    # ── Resumen final ──
    print(f"\n{C['M']}╔{'═' * 58}╗")
    if args.dry_run:
        print(f"║{'✅  DRY-RUN COMPLETADO'.center(58)}║")
    else:
        print(f"║{'🎉  RESTAURACIÓN COMPLETA'.center(58)}║")
    print(f"╚{'═' * 58}╝{C['W']}\n")

    if not args.dry_run and not args.no_start:
        log(f"Abrí http://localhost:2283 en tu navegador", "🌐")


if __name__ == "__main__":
    main()
