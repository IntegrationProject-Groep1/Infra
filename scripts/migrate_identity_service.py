#!/usr/bin/env python3
"""
Identity Service — UUID Bulk Migration Script
==============================================
Team Infra | Integration Project
Erasmus Hogeschool Brussel

Migrates UUID-keyed records from a source database to a target database.
Supports dry-run mode: nothing is written when --dry-run is passed.

Supported DB engines: PostgreSQL, MySQL/MariaDB, SQLite (via SQLAlchemy URI)

Usage
-----
  # Dry-run (read-only preview)
  python migrate_identity_service.py --dry-run

  # Live migration
  python migrate_identity_service.py

  # Override connection strings at runtime
  python migrate_identity_service.py \
      --source "postgresql://user:pass@old-host:5432/identity_db" \
      --target "postgresql://user:pass@new-host:5432/identity_db"

Dependencies
------------
  pip install sqlalchemy psycopg2-binary pymysql tabulate
"""

import argparse
import logging
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Optional rich deps — graceful degradation if not installed
# ---------------------------------------------------------------------------
try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False

try:
    from sqlalchemy import (
        create_engine, text, inspect,
        MetaData, Table, Column,
        String, DateTime, Boolean,
    )
    from sqlalchemy.exc import SQLAlchemyError
    HAS_SQLALCHEMY = True
except ImportError:
    HAS_SQLALCHEMY = False

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_FORMAT = "%(asctime)s  %(levelname)-8s  %(message)s"
logging.basicConfig(format=LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S", level=logging.INFO)
log = logging.getLogger("identity_migration")

# ---------------------------------------------------------------------------
# Configuration — override via CLI or environment variables
# ---------------------------------------------------------------------------
DEFAULT_SOURCE_URI = "postgresql://user:password@localhost:5432/identity_db_old"
DEFAULT_TARGET_URI = "postgresql://user:password@localhost:5432/identity_db_new"

# Table that holds identity / user records
IDENTITY_TABLE = "identities"

# Column names in the source table
COL_UUID        = "id"           # UUID primary key
COL_USERNAME    = "username"
COL_EMAIL       = "email"
COL_CREATED_AT  = "created_at"
COL_UPDATED_AT  = "updated_at"
COL_IS_ACTIVE   = "is_active"

# Batch size — how many rows to migrate per transaction
BATCH_SIZE = 500

# ---------------------------------------------------------------------------
# Data class for a single identity record
# ---------------------------------------------------------------------------
@dataclass
class IdentityRecord:
    id:         str
    username:   str
    email:      str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    is_active:  bool = True

# ---------------------------------------------------------------------------
# Migration stats
# ---------------------------------------------------------------------------
@dataclass
class MigrationStats:
    total_source:   int = 0
    already_exists: int = 0
    migrated:       int = 0
    skipped:        int = 0
    errors:         int = 0
    dry_run:        bool = False
    records_preview: list = field(default_factory=list)  # used in dry-run

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def validate_uuid(value: str) -> bool:
    """Return True when *value* is a valid UUID string."""
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError):
        return False


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def print_stats(stats: MigrationStats) -> None:
    mode = "DRY-RUN" if stats.dry_run else "LIVE"
    separator = "=" * 55
    log.info(separator)
    log.info(f"  Migration summary  [{mode}]")
    log.info(separator)
    log.info(f"  Source records found  : {stats.total_source}")
    log.info(f"  Already in target     : {stats.already_exists}")
    log.info(f"  Migrated              : {stats.migrated}")
    log.info(f"  Skipped (bad UUID)    : {stats.skipped}")
    log.info(f"  Errors                : {stats.errors}")
    log.info(separator)


def print_dry_run_preview(stats: MigrationStats) -> None:
    """Print a table of what *would* be migrated."""
    if not stats.records_preview:
        log.info("[DRY-RUN] No new records to migrate.")
        return

    log.info(f"[DRY-RUN] The following {len(stats.records_preview)} record(s) would be migrated:\n")
    if HAS_TABULATE:
        headers = ["UUID", "Username", "Email", "Created At", "Active"]
        rows = [
            [r.id, r.username, r.email,
             r.created_at if r.created_at else "N/A",
             r.is_active]
            for r in stats.records_preview
        ]
        print(tabulate(rows, headers=headers, tablefmt="rounded_outline"))
    else:
        for r in stats.records_preview:
            print(f"  • {r.id}  |  {r.username}  |  {r.email}")
    print()

# ---------------------------------------------------------------------------
# Core migration logic
# ---------------------------------------------------------------------------

def fetch_source_records(conn) -> list[IdentityRecord]:
    """Read all rows from the source identity table."""
    query = text(f"""
        SELECT
            {COL_UUID},
            {COL_USERNAME},
            {COL_EMAIL},
            {COL_CREATED_AT},
            {COL_UPDATED_AT},
            {COL_IS_ACTIVE}
        FROM {IDENTITY_TABLE}
        ORDER BY {COL_CREATED_AT} ASC
    """)
    result = conn.execute(query)
    records = []
    for row in result.mappings():
        records.append(IdentityRecord(
            id=str(row[COL_UUID]),
            username=row[COL_USERNAME],
            email=row[COL_EMAIL],
            created_at=row.get(COL_CREATED_AT),
            updated_at=row.get(COL_UPDATED_AT),
            is_active=bool(row.get(COL_IS_ACTIVE, True)),
        ))
    return records


def fetch_existing_uuids(conn) -> set[str]:
    """Return the set of UUIDs already present in the target table."""
    query = text(f"SELECT {COL_UUID} FROM {IDENTITY_TABLE}")
    result = conn.execute(query)
    return {str(row[0]) for row in result}


def ensure_target_table(engine) -> None:
    """
    Create the target table if it does not exist yet.
    In a real project you would use Alembic migrations instead.
    """
    meta = MetaData()
    Table(
        IDENTITY_TABLE, meta,
        Column(COL_UUID,       String(36),  primary_key=True),
        Column(COL_USERNAME,   String(255), nullable=False),
        Column(COL_EMAIL,      String(255), nullable=False, unique=True),
        Column(COL_CREATED_AT, DateTime(timezone=True)),
        Column(COL_UPDATED_AT, DateTime(timezone=True)),
        Column(COL_IS_ACTIVE,  Boolean, default=True),
    )
    meta.create_all(engine, checkfirst=True)
    log.info(f"Target table '{IDENTITY_TABLE}' is ready.")


def insert_batch(conn, batch: list[IdentityRecord]) -> int:
    """Insert a batch of records; returns the number successfully inserted."""
    if not batch:
        return 0

    rows = [
        {
            "id":         r.id,
            "username":   r.username,
            "email":      r.email,
            "created_at": r.created_at or utcnow(),
            "updated_at": r.updated_at or utcnow(),
            "is_active":  r.is_active,
        }
        for r in batch
    ]

    stmt = text(f"""
        INSERT INTO {IDENTITY_TABLE}
            ({COL_UUID}, {COL_USERNAME}, {COL_EMAIL},
             {COL_CREATED_AT}, {COL_UPDATED_AT}, {COL_IS_ACTIVE})
        VALUES
            (:id, :username, :email,
             :created_at, :updated_at, :is_active)
    """)
    conn.execute(stmt, rows)
    return len(rows)


def run_migration(
    source_uri: str,
    target_uri: str,
    dry_run: bool = True,
    batch_size: int = BATCH_SIZE,
) -> MigrationStats:

    if not HAS_SQLALCHEMY:
        log.error("SQLAlchemy is not installed. Run: pip install sqlalchemy psycopg2-binary")
        sys.exit(1)

    stats = MigrationStats(dry_run=dry_run)
    mode_label = "[DRY-RUN]" if dry_run else "[LIVE]"

    log.info(f"{mode_label} Starting Identity Service migration")
    log.info(f"  Source : {source_uri}")
    log.info(f"  Target : {target_uri}")
    log.info(f"  Batch  : {batch_size} records per transaction")

    # ------------------------------------------------------------------ #
    # 1. Connect to source and load all records                           #
    # ------------------------------------------------------------------ #
    try:
        src_engine = create_engine(source_uri, pool_pre_ping=True)
        with src_engine.connect() as src_conn:
            log.info("Connected to source database.")
            source_records = fetch_source_records(src_conn)
    except SQLAlchemyError as exc:
        log.error(f"Cannot connect to source database: {exc}")
        sys.exit(1)

    stats.total_source = len(source_records)
    log.info(f"Found {stats.total_source} record(s) in source.")

    if stats.total_source == 0:
        log.warning("Source table is empty — nothing to migrate.")
        print_stats(stats)
        return stats

    # ------------------------------------------------------------------ #
    # 2. Validate UUIDs                                                   #
    # ------------------------------------------------------------------ #
    valid_records: list[IdentityRecord] = []
    for r in source_records:
        if validate_uuid(r.id):
            valid_records.append(r)
        else:
            log.warning(f"  SKIP invalid UUID: '{r.id}' (username={r.username})")
            stats.skipped += 1

    log.info(f"Valid records after UUID check: {len(valid_records)}")

    # ------------------------------------------------------------------ #
    # 3. Connect to target and find existing UUIDs (duplicate detection)  #
    # ------------------------------------------------------------------ #
    try:
        tgt_engine = create_engine(target_uri, pool_pre_ping=True)
        if not dry_run:
            ensure_target_table(tgt_engine)

        with tgt_engine.connect() as tgt_conn:
            log.info("Connected to target database.")
            existing_uuids = fetch_existing_uuids(tgt_conn)
    except SQLAlchemyError as exc:
        log.error(f"Cannot connect to target database: {exc}")
        sys.exit(1)

    stats.already_exists = sum(1 for r in valid_records if r.id in existing_uuids)
    to_migrate = [r for r in valid_records if r.id not in existing_uuids]

    log.info(f"Already in target (will be skipped) : {stats.already_exists}")
    log.info(f"New records to migrate               : {len(to_migrate)}")

    # ------------------------------------------------------------------ #
    # 4. Dry-run path — show preview, no writes                           #
    # ------------------------------------------------------------------ #
    if dry_run:
        stats.records_preview = to_migrate
        stats.migrated = len(to_migrate)   # "would be migrated"
        print_dry_run_preview(stats)
        print_stats(stats)
        log.info("[DRY-RUN] No changes were made to any database.")
        return stats

    # ------------------------------------------------------------------ #
    # 5. Live migration — insert in batches                               #
    # ------------------------------------------------------------------ #
    batches = [to_migrate[i:i + batch_size] for i in range(0, len(to_migrate), batch_size)]
    log.info(f"Migrating in {len(batches)} batch(es) of up to {batch_size}…")

    try:
        with tgt_engine.begin() as tgt_conn:   # single transaction
            for idx, batch in enumerate(batches, start=1):
                try:
                    inserted = insert_batch(tgt_conn, batch)
                    stats.migrated += inserted
                    log.info(f"  Batch {idx}/{len(batches)}: inserted {inserted} record(s)  "
                             f"(total so far: {stats.migrated})")
                except SQLAlchemyError as exc:
                    log.error(f"  Batch {idx} failed: {exc}")
                    stats.errors += len(batch)
                    raise   # triggers rollback of the whole transaction
    except SQLAlchemyError:
        log.error("Transaction rolled back — no records were written to target.")
        stats.migrated = 0

    print_stats(stats)
    return stats


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Identity Service UUID bulk migration script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Preview what would be migrated without touching the databases.",
    )
    parser.add_argument(
        "--source",
        default=DEFAULT_SOURCE_URI,
        metavar="URI",
        help="SQLAlchemy URI for the SOURCE database.",
    )
    parser.add_argument(
        "--target",
        default=DEFAULT_TARGET_URI,
        metavar="URI",
        help="SQLAlchemy URI for the TARGET database.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        metavar="N",
        help=f"Records per transaction batch (default: {BATCH_SIZE}).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.verbose:
        log.setLevel(logging.DEBUG)

    if args.dry_run:
        log.info("=" * 55)
        log.info("  DRY-RUN MODE — databases will NOT be modified")
        log.info("=" * 55)

    stats = run_migration(
        source_uri=args.source,
        target_uri=args.target,
        dry_run=args.dry_run,
        batch_size=args.batch_size,
    )

    # Exit with non-zero code when errors occurred during live run
    if not args.dry_run and stats.errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
