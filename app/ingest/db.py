from app.store import connect
def ensure_schema():
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS device_watermarks(
            device_id TEXT PRIMARY KEY,
            contiguous_seq INTEGER NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS device_segments(
            device_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            checksum TEXT NOT NULL,
            payload TEXT NOT NULL,
            received_at TEXT NOT NULL,
            duplicates INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(device_id,seq))""")
        db.execute("""CREATE TABLE IF NOT EXISTS day_settlements(
            device_id TEXT NOT NULL,
            business_day TEXT NOT NULL,
            sealed_at TEXT NOT NULL,
            snapshot TEXT NOT NULL,
            PRIMARY KEY(device_id,business_day))""")
