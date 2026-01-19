#!/usr/bin/env python3
"""
Database Index Creation Script for AutoSafe
Creates indexes to optimize query performance.
"""
import os
import sys

DATABASE_URL = os.environ.get("DATABASE_URL")

def create_sqlite_indexes():
    """Create indexes on SQLite database."""
    import sqlite3
    
    db_file = 'autosafe.db'
    if not os.path.exists(db_file):
        print(f"SQLite database {db_file} not found")
        return False
    
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    
    print("Creating SQLite indexes...")
    
    # Index for DISTINCT model_id queries (used by /api/makes)
    print("  Creating idx_risks_model_id...")
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_risks_model_id 
        ON risks(model_id)
    """)
    
    # Composite index for risk lookups (used by /api/risk)
    print("  Creating idx_risks_lookup...")
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_risks_lookup 
        ON risks(model_id, age_band, mileage_band)
    """)
    
    conn.commit()
    conn.close()
    print("SQLite indexes created successfully")
    return True


async def create_postgres_indexes():
    """Create indexes on PostgreSQL database."""
    import asyncpg
    
    if not DATABASE_URL:
        print("No DATABASE_URL set, skipping PostgreSQL indexes")
        return False
    
    db_url = DATABASE_URL.replace("postgres://", "postgresql://")
    
    try:
        conn = await asyncpg.connect(db_url)
        
        print("Creating PostgreSQL indexes...")
        
        # Index for DISTINCT model_id queries (used by /api/makes)
        print("  Creating idx_mot_risk_model_id...")
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mot_risk_model_id 
            ON mot_risk(model_id)
        """)
        
        # Composite index for risk lookups (used by /api/risk)
        print("  Creating idx_mot_risk_lookup...")
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mot_risk_lookup 
            ON mot_risk(model_id, age_band, mileage_band)
        """)
        
        # Additional index for text pattern matching on model_id
        # Using btree_gin extension for LIKE prefix queries
        print("  Creating text pattern index (if supported)...")
        try:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS btree_gin")
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_mot_risk_model_id_prefix 
                ON mot_risk USING btree(model_id text_pattern_ops)
            """)
        except Exception as e:
            print(f"    Note: text_pattern_ops index not created: {e}")
        
        await conn.close()
        print("PostgreSQL indexes created successfully")
        return True
        
    except Exception as e:
        print(f"Error creating PostgreSQL indexes: {e}")
        return False


def main():
    print("=" * 50)
    print("AutoSafe Database Index Creation")
    print("=" * 50)
    
    # Try SQLite first
    sqlite_ok = create_sqlite_indexes()
    
    # Try PostgreSQL if DATABASE_URL is set
    if DATABASE_URL:
        import asyncio
        pg_ok = asyncio.run(create_postgres_indexes())
    else:
        print("\nNo DATABASE_URL set - PostgreSQL indexes skipped")
        pg_ok = False
    
    print("\n" + "=" * 50)
    if sqlite_ok or pg_ok:
        print("Index creation complete!")
        print("\nTo verify indexes:")
        if sqlite_ok:
            print("  SQLite: sqlite3 autosafe.db '.indexes risks'")
        if pg_ok:
            print("  PostgreSQL: \\di mot_risk*")
    else:
        print("No indexes were created.")
    
    return 0 if (sqlite_ok or pg_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
