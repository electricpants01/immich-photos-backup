# 📸 Immich + S3 Backup

Self-hosted [Immich](https://immich.app) deployment with automated incremental backups to AWS S3 and one-command disaster recovery.

## 🗂️ What's in this repo

```
├── docker-compose.yml        # 4 services (server, ML, postgres, valkey)
├── .env.example              # Template — copy to .env and edit
├── backup-to-s3.py           # 🔄 Incremental backup → S3
├── restore-from-s3.py        # 🔥 One-command disaster recovery
├── AI/
│   ├── immich-knowledge.md   # Full Immich reference
│   ├── disaster-recovery.md  # Restore guide (auto + manual)
│   ├── commands-cheatsheet.md
│   ├── docker-network-fix.md
│   └── pull-images.py        # Pull images from ghcr.io via registry API
├── .clinerules               # AI assistant rules
└── .gitignore
```

## 🚀 Quick Start

### 1. Clone & configure

```bash
git clone <your-repo-url> immich
cd immich

# Create your .env from the template
cp .env.example .env

# Edit .env — at minimum change:
#   - DB_PASSWORD  (random password, A-Za-z0-9 only)
#   - S3_BUCKET    (your actual bucket name)
#   - TZ           (your timezone)
```

### 2. Pull images

```bash
# Option A: docker pull (if ghcr.io is reachable)
docker compose pull

# Option B: registry API (if docker pull has TLS issues)
python3 AI/pull-images.py ghcr.io immich-app/immich-server release
python3 AI/pull-images.py ghcr.io immich-app/immich-machine-learning release
python3 AI/pull-images.py ghcr.io immich-app/postgres 14-vectorchord0.4.3-pgvectors0.2.0
docker pull docker.io/valkey/valkey:9
```

### 3. Start Immich

```bash
docker compose up -d --pull never
open http://localhost:2283
```

## 🔄 Backup to S3

```bash
# Full backup: photos + database + config files
python3 backup-to-s3.py

# Preview what would upload (no changes)
python3 backup-to-s3.py --dry-run

# Database only
python3 backup-to-s3.py --db-only

# Photos only
python3 backup-to-s3.py --lib-only
```

### What gets backed up

| Data | S3 Path | Strategy |
|---|---|---|
| Photos, thumbs, videos | `library/` | Incremental (new/changed only) |
| PostgreSQL dump | `database/immich-db-*.sql.gz` | Full each run (keeps last 7) |
| `.env` | `config/.env` | Every backup |
| `docker-compose.yml` | `config/docker-compose.yml` | Every backup |
| `pull-images.py` | `config/pull-images.py` | Every backup |

### Schedule with cron

```bash
# Daily at 3 AM
0 3 * * * cd /path/to/immich && python3 backup-to-s3.py >> backup.log 2>&1
```

## 🔥 Disaster Recovery

If you lose your machine — restore everything to a new one with a single command:

```bash
python3 restore-from-s3.py
```

The script automates all 7 steps:
1. Verifies Docker, AWS CLI, credentials, S3 bucket
2. Creates the target directory
3. Downloads `.env`, `docker-compose.yml`, `pull-images.py` from S3 (falls back to GitHub)
4. Pulls Docker images (falls back to registry API if needed)
5. Syncs all photos via `aws s3 sync`
6. Downloads & restores the latest PostgreSQL dump
7. Starts Immich with `docker compose up -d --pull never`

```bash
# Preview what would happen
python3 restore-from-s3.py --dry-run

# Custom directory
python3 restore-from-s3.py --dir ~/my-immich

# Skip steps you've already done
python3 restore-from-s3.py --skip-pull --skip-photos
```

## 📋 Prerequisites

- **Docker** + Docker Compose (v2)
- **Python 3.8+** with `boto3` (`pip3 install boto3`)
- **AWS CLI** installed and configured (`aws configure`)
- An **S3 bucket** (any name — set it in `.env`)

## ⚠️ Network Note

If you're in a region where Docker's internal proxy has TLS issues with `ghcr.io` (Bolivia, some ISPs), the scripts handle it:
- `backup-to-s3.py` / `restore-from-s3.py` → use `boto3` directly (no Docker network involved)
- `AI/pull-images.py` → pulls images via registry HTTPS API, then `docker load`
- **Never** run `docker compose up -d` without `--pull never` if ghcr.io is unreachable

## 🔐 Security

- `.env` is in `.gitignore` — never committed
- `S3_BUCKET` lives in `.env`, not hardcoded in scripts
- Use `.env.example` as a template for new clones
- Rotate `DB_PASSWORD` and restrict S3 bucket access with IAM policies

## 📚 More Info

- [Immich Docs](https://docs.immich.app)
- [AI/disaster-recovery.md](AI/disaster-recovery.md) — detailed restore guide
- [AI/immich-knowledge.md](AI/immich-knowledge.md) — full Immich reference
- [AI/commands-cheatsheet.md](AI/commands-cheatsheet.md) — quick commands
