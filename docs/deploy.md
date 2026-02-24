# Deployment Guide

This guide covers deployment options for nanobot, from development setups to production deployments.

## Deployment Options

### 1. Development Mode (File Backend)

**Use Case**: Local development, testing, simple deployments
**Backend**: File-based memory storage
**Setup**: Zero configuration required

```bash
# Clone and install
git clone https://github.com/HKUDS/nanobot.git
cd nanobot
uv sync

# Configure
uv run nanobot onboard

# Start development server
uv run nanobot gateway
```

**Features**:
- Daily and long-term memory in Markdown files
- No database setup required
- Ideal for development and small-scale use
- Easy to backup and migrate

### 2. Production Mode (PostgreSQL Backend)

**Use Case**: Production deployments, multi-user systems, large-scale usage
**Backend**: PostgreSQL with pgvector and pgmq extensions
**Setup**: Docker container provided

#### 2.1 Self-Hosted PostgreSQL

**Prerequisites**:
- Docker and Docker Compose
- PostgreSQL 14+ with pgvector and pgmq extensions

**Setup Steps**:

1. **Start PostgreSQL with extensions**:
```bash
# Using provided Docker setup
docker-compose -f docker/pg.yml up -d
```

2. **Configure nanobot**:
```bash
# Run onboard with PostgreSQL backend
uv run nanobot onboard --backend postgres
```

3. **Verify setup**:
```bash
# Test database connection
uv run nanobot db check

# Start services
uv run nanobot gateway
```

**Configuration**:
```yaml
# config.yaml
database:
  url: "postgresql://user:password@localhost:5432/nanobot"
  pool_size: 10
  max_overflow: 20
```

#### 2.2 Cloud PostgreSQL Options

##### Supabase (Recommended)

**Setup Steps**:

1. **Create Supabase Project**:
   - Go to [supabase.com](https://supabase.com)
   - Create a new project
   - Note your project URL and API key

2. **Enable Required Extensions**:
   ```sql
   -- In Supabase SQL Editor
   CREATE EXTENSION IF NOT EXISTS vector;
   CREATE EXTENSION IF NOT EXISTS pgmq;
   ```

3. **Configure Connection**:
   ```yaml
   # config.yaml
   database:
     # Use Session Pooler URL (NOT Direct Connection)
     url: "postgresql://postgres:[YOUR-PASSWORD]@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres"
     pool_size: 5
     max_overflow: 10
   ```

**Why Session Pooler?**
- **Connection Pooling**: Manages database connections efficiently
- **Better Performance**: Reduces connection overhead
- **Cost Optimization**: Fewer active connections to your database
- **Reliability**: Automatic connection recovery and load balancing

**Connection String Format**:
```
postgresql://postgres:[YOUR-PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres
```

##### Other Cloud Providers

**AWS RDS**:
```yaml
database:
  url: "postgresql://user:password@your-rds-instance.region.rds.amazonaws.com:5432/nanobot"
```

**Google Cloud SQL**:
```yaml
database:
  url: "postgresql://user:password@your-instance:5432/nanobot"
```

**Neon**:
```yaml
database:
  url: "postgresql://user:password@your-neon-project.neon.tech:5432/nanobot"
```

## Environment Variables

```bash
# Required
DATABASE_URL="postgresql://user:password@host:5432/nanobot"

# Optional
OPENAI_API_KEY="your-openai-key"
ANTHROPIC_API_KEY="your-anthropic-key"
EMBEDDING_API_KEY="your-embedding-key"

# Performance tuning
DATABASE_POOL_SIZE=10
DATABASE_MAX_OVERFLOW=20
```

## Monitoring and Maintenance

### Health Checks

```bash
# Check database connection
uv run nanobot db check

# Check embedding queue
uv run nanobot queue status

# System health
uv run nanobot health
```

### Backup Strategy

**PostgreSQL Backup**:
```bash
# Daily backup
pg_dump nanobot > backup_$(date +%Y%m%d).sql
```

**File Backend Backup**:
```bash
# Simple tar backup
tar -czf memory_backup_$(date +%Y%m%d).tar.gz memory/
```

## Migration Guide

### From File to PostgreSQL Backend

1. **Backup existing data**:
```bash
cp -r memory/ memory_backup/
```

2. **Set up PostgreSQL**:
```bash
docker-compose -f docker/pg.yml up -d
uv run nanobot db migrate
```

3. **Migrate data**:
```bash
uv run nanobot migrate --from-file --to-postgres
```

4. **Update configuration**:
```yaml
memory:
  backend: postgres
```

This migration preserves all existing daily and long-term memories while enabling the full hybrid memory system.
