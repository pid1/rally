# Rally Database Migrations

Rally uses a simple, file-based migration system. All migrations are **idempotent** and safe to run multiple times.

## How Migrations Work

1. **On Container Startup**: `entrypoint.sh` runs `run_migrations.py` automatically
2. **Idempotent**: Each migration checks if changes are already applied before executing
3. **Ordered**: Migrations run in the order they're listed in `run_migrations.py`
4. **Fail-Safe**: If any migration fails, the container won't start

## Existing Migrations

| Migration | Description | Date Added |
|-----------|-------------|------------|
| `001_add_due_date` | Add `due_date` column to `todos` table | 2026-02-15 |

## Creating a New Migration

### 1. Create Migration File

Create `migrate_XXX_description.py` with this structure:

```python
#!/usr/bin/env python3
"""Migration: Brief description of what this does.

Safe to run multiple times (idempotent).
"""
import os
import sqlite3
from pathlib import Path

def migrate():
    """Run the migration. Return True on success, False on failure."""
    # Get database path from environment or use default
    db_path = os.environ.get("RALLY_DB_PATH")
    
    if not db_path:
        prod_path = Path("/data/rally.db")
        dev_path = Path(__file__).parent / "rally.db"
        db_path = str(prod_path) if prod_path.exists() else str(dev_path)
    
    db_path = Path(db_path)
    
    if not db_path.exists():
        print(f"✓ Database not found at {db_path}")
        print("  No migration needed - database will be created with correct schema.")
        return True
    
    print(f"Checking database at {db_path}...")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # CHECK: Is this migration already applied?
        # Example: Check if column exists
        cursor.execute("PRAGMA table_info(your_table)")
        columns = [col[1] for col in cursor.fetchall()]
        
        if 'your_new_column' in columns:
            print("✓ Migration: your_table.your_new_column already exists (idempotent)")
            return True
        
        # EXECUTE: Apply the migration
        print("  Applying migration...")
        cursor.execute("ALTER TABLE your_table ADD COLUMN your_new_column VARCHAR(10)")
        conn.commit()
        print("✓ Migration complete: your_table.your_new_column added")
        return True
        
    except sqlite3.Error as e:
        print(f"✗ Migration failed: {e}")
        return False
    finally:
        conn.close()

if __name__ == "__main__":
    import sys
    success = migrate()
    sys.exit(0 if success else 1)
```

### 2. Register Migration

Add your migration to `run_migrations.py`:

```python
from migrate_XXX_description import migrate as migrate_XXX_description

migrations = [
    ("001_add_due_date", migrate_001_add_due_date),
    ("XXX_description", migrate_XXX_description),  # Add your migration here
]
```

### 3. Test Locally

```bash
# Test the migration
python3 migrate_XXX_description.py

# Test the full migration runner
python3 run_migrations.py

# Test idempotency (should succeed twice)
python3 run_migrations.py && python3 run_migrations.py
```

### 4. Update Dockerfile (if needed)

If your migration file is new, add it to the Dockerfile:

```dockerfile
COPY migrate_XXX_description.py migrate_XXX_description.py
```

### 5. Deploy

The migration will run automatically on container startup.

## Migration Best Practices

### ✅ Do

- **Make migrations idempotent** - Check before changing
- **Return True/False** - Indicate success or failure
- **Use PRAGMA table_info** - Check if columns exist
- **Handle missing database** - It's fine if DB doesn't exist yet
- **Print clear messages** - Use ✓ for success, ✗ for errors
- **Test locally first** - Run multiple times to verify idempotency

### ❌ Don't

- **Don't drop data** - Migrations should be additive
- **Don't assume order** - Each migration should be independent
- **Don't use external files** - Keep migration logic self-contained
- **Don't skip idempotency checks** - Always check before executing

## SQLite Migration Patterns

### Add Column
```python
cursor.execute("PRAGMA table_info(table_name)")
columns = [col[1] for col in cursor.fetchall()]
if 'new_column' not in columns:
    cursor.execute("ALTER TABLE table_name ADD COLUMN new_column TYPE")
```

### Create Table
```python
cursor.execute("""
    CREATE TABLE IF NOT EXISTS table_name (
        id INTEGER PRIMARY KEY,
        field VARCHAR(100)
    )
""")
```

### Add Index
```python
cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_name 
    ON table_name(column)
""")
```

### Rename Column (SQLite 3.25+)
```python
cursor.execute("ALTER TABLE table_name RENAME COLUMN old TO new")
```

## Troubleshooting

### Migration fails on startup
Check logs: `docker logs rally` or `devenv shell -c dev-logs`

### Test migration manually
```bash
# Development
python3 migrate_XXX_description.py

# Docker
docker exec rally python migrate_XXX_description.py
```

### Reset database (⚠️ destroys data)
```bash
# Development
rm rally.db
devenv shell -c db-init

# Docker
docker exec rally rm /data/rally.db
docker restart rally
```

## Migration Workflow Example

```bash
# 1. Create migration
vim migrate_002_add_priority.py

# 2. Test it
python3 migrate_002_add_priority.py  # First run - applies change
python3 migrate_002_add_priority.py  # Second run - idempotent check

# 3. Register it
vim run_migrations.py  # Add to migrations list

# 4. Test full runner
python3 run_migrations.py

# 5. Update Dockerfile
vim Dockerfile  # Add COPY line

# 6. Build and test
devenv shell -c build
devenv shell -c up
devenv shell -c logs-tail

# 7. Verify
curl http://localhost:8000/api/todos | jq '.[0]'
```
