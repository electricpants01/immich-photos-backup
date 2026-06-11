#!/usr/bin/env python3
"""
Immich → S3 Backup Script
Backups incrementales de fotos (library) y base de datos (pg_dump).

Uso:
  python3 backup-to-s3.py              # Backup completo
  python3 backup-to-s3.py --dry-run    # Solo ver qué se subiría
  python3 backup-to-s3.py --db-only    # Solo base de datos
  python3 backup-to-s3.py --lib-only   # Solo fotos
"""

import os, sys, time, argparse, subprocess, tempfile
from pathlib import Path
from datetime import datetime

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


def log(msg, emoji="📋"):
    t = datetime.now().strftime("%H:%M:%S")
    print(f"{emoji} [{t}] {msg}", flush=True)


# ================================================================
#  VERIFICACIONES INICIALES
# ================================================================

def check_prerequisites():
    log("Verificando requisitos...", "🔍")
    
    session = boto3.Session()
    if not session.get_credentials():
        log("ERROR: No hay credenciales AWS. Ejecutá: aws configure", "❌")
        sys.exit(1)
    
    s3 = session.client("s3")
    try:
        s3.head_bucket(Bucket=BUCKET_NAME)
        log(f"Bucket S3 '{BUCKET_NAME}' OK", "✅")
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            log(f"Bucket '{BUCKET_NAME}' no existe", "❌")
            sys.exit(1)
        raise
    
    # Library → fotos
    lib = PROJECT_DIR / "library"
    if lib.exists():
        files = [f for f in lib.rglob("*") if f.is_file()]
        size = sum(f.stat().st_size for f in files)
        log(f"  library/ → {len(files)} archivos, {size/1024/1024:.0f} MB", "📁")
    else:
        log(f"  library/ → NO EXISTE", "⚠️")
    
    # Postgres → verificar contenedor corriendo
    try:
        r = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}", "--filter", "name=immich_postgres"],
            capture_output=True, text=True, timeout=5
        )
        if "immich_postgres" in r.stdout:
            log(f"  postgres → contenedor corriendo OK", "✅")
        else:
            log(f"  postgres → contenedor NO está corriendo", "⚠️")
    except Exception:
        log(f"  postgres → Docker no disponible", "⚠️")


# ================================================================
#  BACKUP DE FOTOS (library)
# ================================================================

def backup_library(dry_run=False):
    """Sincroniza la carpeta library/ con S3. Solo sube archivos nuevos."""
    local = PROJECT_DIR / "library"
    if not local.exists():
        log("library/ no existe, salteando", "⚠️")
        return 0, 0
    
    s3 = boto3.client("s3")
    uploaded = 0
    skipped = 0
    total_bytes = 0
    
    files = [f for f in local.rglob("*") if f.is_file()]
    total_files = len(files)
    
    log(f"FOTOS → s3://{BUCKET_NAME}/library/ ({total_files} archivos)", "📸")
    
    for i, filepath in enumerate(files):
        rel = filepath.relative_to(local)
        key = f"library/{rel}"
        
        # ¿Ya existe en S3 con mismo tamaño?
        try:
            obj = s3.head_object(Bucket=BUCKET_NAME, Key=key)
            if obj["ContentLength"] == filepath.stat().st_size:
                skipped += 1
                if (i + 1) % 500 == 0:
                    log(f"  {i+1}/{total_files} | {uploaded} nuevos | {skipped} sin cambios", "📊")
                continue
        except ClientError:
            pass
        
        if dry_run:
            uploaded += 1
            total_bytes += filepath.stat().st_size
            if (i + 1) % 200 == 0:
                log(f"  [DRY] {i+1}/{total_files} nuevos detectados", "🔍")
            continue
        
        try:
            s3.upload_file(
                str(filepath), BUCKET_NAME, key,
                ExtraArgs={"StorageClass": "STANDARD_IA"}
            )
            uploaded += 1
            total_bytes += filepath.stat().st_size
            if (i + 1) % 100 == 0:
                log(f"  {i+1}/{total_files} | {uploaded} subidos | {total_bytes/1024/1024:.0f} MB", "📊")
        except Exception as e:
            log(f"  ERROR {rel.name}: {e}", "❌")
    
    log(f"  Fotos: {uploaded} nuevos, {skipped} sin cambios ({total_bytes/1024/1024:.0f} MB)", "✅")
    return uploaded, skipped


# ================================================================
#  BACKUP DE BASE DE DATOS (pg_dump)
# ================================================================

def backup_database(dry_run=False):
    """
    Genera un dump SQL de Postgres usando pg_dump dentro del contenedor,
    comprime con gzip y sube a S3.
    
    El dump usa --clean --if-exists para que al restaurar
    borre tablas existentes y las recree limpiamente.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dump_name = f"immich-db-{timestamp}.sql.gz"
    
    log(f"BASE DE DATOS → pg_dump → {dump_name}", "🗄️")
    
    if dry_run:
        log(f"  [DRY] Generaría dump SQL y subiría a s3://{BUCKET_NAME}/database/{dump_name}", "🔍")
        return 1, 0
    
    # 1. Ejecutar pg_dump dentro del contenedor
    log(f"  Ejecutando pg_dump en immich_postgres...", "🐘")
    
    result = subprocess.run(
        [
            "docker", "exec", "immich_postgres",
            "pg_dump",
            "-U", "postgres",
            "-d", "immich",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-acl"
        ],
        capture_output=True, text=True, timeout=120
    )
    
    if result.returncode != 0:
        log(f"  ERROR: pg_dump falló: {result.stderr[:300]}", "❌")
        return 0, 1
    
    raw_size = len(result.stdout.encode())
    log(f"  Dump SQL generado: {raw_size/1024/1024:.1f} MB", "✅")
    
    # 2. Comprimir con gzip
    import gzip
    compressed = gzip.compress(result.stdout.encode())
    compressed_size = len(compressed)
    log(f"  Comprimido: {compressed_size/1024/1024:.1f} MB ({(1 - compressed_size/max(raw_size,1))*100:.0f}% menos)", "📦")
    
    # 3. Guardar localmente y subir a S3
    with tempfile.NamedTemporaryFile(suffix=".sql.gz", delete=False) as tmp:
        tmp.write(compressed)
        tmp_path = tmp.name
    
    try:
        s3 = boto3.client("s3")
        key = f"database/{dump_name}"
        s3.upload_file(
            tmp_path, BUCKET_NAME, key,
            ExtraArgs={"StorageClass": "STANDARD_IA"}
        )
        log(f"  Subido: s3://{BUCKET_NAME}/{key}", "✅")
        
        # 4. Limpiar dumps viejos (mantener últimos 7)
        cleanup_old_dumps(s3)
        
        os.unlink(tmp_path)
        return 1, 0
        
    except Exception as e:
        log(f"  ERROR subiendo dump: {e}", "❌")
        os.unlink(tmp_path)
        return 0, 1


# ================================================================
#  BACKUP DE CONFIGURACIÓN (.env, docker-compose.yml, scripts)
# ================================================================

def backup_config(dry_run=False):
    """Sube archivos de configuración críticos a S3 para disaster recovery."""
    config_files = [
        PROJECT_DIR / ".env",
        PROJECT_DIR / "docker-compose.yml",
        PROJECT_DIR / "AI" / "pull-images.py",
    ]

    s3 = boto3.client("s3")
    uploaded = 0

    log(f"CONFIG → s3://{BUCKET_NAME}/config/", "⚙️")

    for filepath in config_files:
        if not filepath.exists():
            log(f"  {filepath.name} → NO EXISTE, salteando", "⚠️")
            continue

        key = f"config/{filepath.name}"

        if dry_run:
            log(f"  [DRY] Subiría {filepath.name} ({filepath.stat().st_size} bytes)", "🔍")
            uploaded += 1
            continue

        try:
            s3.upload_file(
                str(filepath), BUCKET_NAME, key,
                ExtraArgs={"StorageClass": "STANDARD_IA"}
            )
            log(f"  {filepath.name} → OK ({filepath.stat().st_size} bytes)", "✅")
            uploaded += 1
        except Exception as e:
            log(f"  ERROR subiendo {filepath.name}: {e}", "❌")

    log(f"  Config: {uploaded}/{len(config_files)} archivos subidos", "✅")
    return uploaded


def cleanup_old_dumps(s3, keep=7):
    """Borra dumps antiguos, manteniendo los últimos N."""
    try:
        objs = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix="database/immich-db-")
        if not objs.get("Contents"):
            return
        
        # Ordenar por fecha (más nuevo primero)
        sorted_objs = sorted(
            objs["Contents"],
            key=lambda x: x["LastModified"],
            reverse=True
        )
        
        if len(sorted_objs) <= keep:
            return
        
        # Borrar los más viejos
        to_delete = [{"Key": obj["Key"]} for obj in sorted_objs[keep:]]
        s3.delete_objects(Bucket=BUCKET_NAME, Delete={"Objects": to_delete})
        log(f"  Limpieza: {len(to_delete)} dumps antiguos borrados (quedan {keep})", "🧹")
    except Exception as e:
        log(f"  Limpieza falló (no crítico): {e}", "⚠️")


# ================================================================
#  RESTAURAR (comando manual)
# ================================================================

def print_restore_instructions():
    print()
    log("Para restaurar desde S3:", "📖")
    print(f"""
  # ⚡ Automático (recomendado)
  python3 restore-from-s3.py

  # 📖 Manual:
  # 1. Bajar config
  aws s3 cp s3://{BUCKET_NAME}/config/.env .env
  aws s3 cp s3://{BUCKET_NAME}/config/docker-compose.yml docker-compose.yml

  # 2. Descargar el dump más reciente
  aws s3 cp s3://{BUCKET_NAME}/database/immich-db-YYYY-MM-DD_HHMMSS.sql.gz .

  # 3. Descomprimir y restaurar
  gunzip immich-db-YYYY-MM-DD_HHMMSS.sql.gz
  docker compose down
  docker compose up -d database
  docker exec -i immich_postgres psql -U postgres -d immich < immich-db-YYYY-MM-DD_HHMMSS.sql
  docker compose up -d --pull never
""")


# ================================================================
#  MAIN
# ================================================================

def main():
    parser = argparse.ArgumentParser(description="Immich → S3 Backup")
    parser.add_argument("--dry-run", action="store_true", help="Solo mostrar qué se subiría")
    parser.add_argument("--lib-only", action="store_true", help="Solo fotos")
    parser.add_argument("--db-only", action="store_true", help="Solo base de datos")
    args = parser.parse_args()
    
    do_lib = not args.db_only
    do_db  = not args.lib_only
    
    print()
    log("IMMICH → S3 BACKUP", "🚀")
    log(f"Bucket: {BUCKET_NAME}", "📦")
    log(f"Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "🕐")
    if args.dry_run:
        log("MODO DRY-RUN (no se sube nada)", "🔍")
    print()
    
    check_prerequisites()
    print()
    
    start = time.time()
    total_uploads = 0
    
    if do_lib:
        up, sk = backup_library(dry_run=args.dry_run)
        total_uploads += up
        print()

    if do_db:
        up, err = backup_database(dry_run=args.dry_run)
        total_uploads += up
        print()

    # Config siempre se respalda (es chico y crítico)
    up = backup_config(dry_run=args.dry_run)
    total_uploads += up
    print()

    elapsed = time.time() - start
    m, s = int(elapsed // 60), int(elapsed % 60)

    if args.dry_run:
        log(f"Dry-run completado en {m}m {s}s", "🔍")
    else:
        log(f"BACKUP COMPLETO en {m}m {s}s", "🎉")

    print_restore_instructions()


if __name__ == "__main__":
    main()
