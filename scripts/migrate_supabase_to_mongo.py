#!/usr/bin/env python
"""Migrate TransplantCare data from Supabase Postgres and Storage to MongoDB."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, unquote, urlparse
from uuid import UUID

import httpx
from dotenv import load_dotenv
from gridfs import GridFSBucket
from pymongo import MongoClient

PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.mongo import INDEXES
from app.models import (
    AppointmentDocument,
    CaregiverLinkDocument,
    ChatMessageDocument,
    ConsentDocument,
    DietaryLimitDocument,
    DoseLogDocument,
    EmergencyContactDocument,
    FoodCompositionDocument,
    FoodLogDocument,
    GuidanceContentDocument,
    LabReportDocument,
    LabValueDocument,
    MedicineDocument,
    ProfileDocument,
    SosEventDocument,
)


TABLE_MODELS = {
    "profiles": ProfileDocument,
    "consents": ConsentDocument,
    "emergency_contacts": EmergencyContactDocument,
    "medicines": MedicineDocument,
    "dose_logs": DoseLogDocument,
    "dietary_limits": DietaryLimitDocument,
    "food_composition": FoodCompositionDocument,
    "food_logs": FoodLogDocument,
    "appointments": AppointmentDocument,
    "lab_reports": LabReportDocument,
    "lab_values": LabValueDocument,
    "guidance_content": GuidanceContentDocument,
    "caregiver_links": CaregiverLinkDocument,
    "chat_messages": ChatMessageDocument,
    "sos_events": SosEventDocument,
}

MIGRATION_ORDER = tuple(TABLE_MODELS)
TABLE_ORDER_COLUMNS = {"dietary_limits": "patient_id"}
DISABLED_PASSWORD_HASH = "!password-login-disabled!"


class MigrationError(RuntimeError):
    pass


def _mongo_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    if isinstance(value, time):
        return value.isoformat(timespec="minutes")
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _mongo_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mongo_value(item) for item in value]
    return value


def transform_public_row(table: str, source: dict[str, Any]) -> dict[str, Any]:
    if table not in TABLE_MODELS:
        raise ValueError(f"Unsupported source table: {table}")

    row = {key: _mongo_value(value) for key, value in source.items()}
    if table == "appointments":
        row["scheduled_at"] = row.pop("starts_at")
    if table == "profiles" and isinstance(source.get("transplant_date"), date):
        row["transplant_date"] = source["transplant_date"].isoformat()

    document_id = row.pop("id", None)
    if table == "dietary_limits" and document_id is None:
        document_id = row["patient_id"]
    if document_id is None:
        raise MigrationError(f"Source row in {table} has no stable primary key")
    row["_id"] = str(document_id)

    if table == "caregiver_links":
        if row.get("status") == "pending" and row.get("created_at"):
            row["expires_at"] = row["created_at"] + timedelta(hours=24)
        else:
            row["expires_at"] = None

    model = TABLE_MODELS[table]
    if model is not None:
        row = model.model_validate(row).model_dump(
            mode="python",
            by_alias=True,
            exclude_none=False,
        )
    return row


def transform_auth_user(
    source: dict[str, Any],
    role: str,
    consent: dict[str, Any] | None,
) -> dict[str, Any]:
    user_id = str(source["id"])
    email = source.get("email") or f"no-email-{user_id}@migration.invalid"
    accepted_consent = consent if consent and consent.get("accepted") else None
    password_hash = source.get("encrypted_password") or DISABLED_PASSWORD_HASH
    return {
        "_id": user_id,
        "email": email.strip().lower(),
        "password_hash": password_hash,
        "role": role,
        "created_at": _mongo_value(source["created_at"]),
        "consent_version": (
            accepted_consent.get("policy_version") if accepted_consent else None
        ),
        "consent_at": (
            _mongo_value(accepted_consent.get("accepted_at"))
            if accepted_consent
            else None
        ),
    }


def _report_storage_path(value: str) -> str:
    parsed = urlparse(value)
    path = parsed.path if parsed.scheme else value
    bucket_prefix = "/report-uploads/"
    if bucket_prefix in path:
        path = path.split(bucket_prefix, 1)[1]
    elif path.startswith("report-uploads/"):
        path = path[len("report-uploads/") :]
    path = path.lstrip("/")
    if not path:
        raise MigrationError("A lab report has an empty Supabase storage path")
    return unquote(path)


def _download_report(
    client: httpx.Client,
    supabase_url: str,
    service_key: str,
    storage_path: str,
) -> tuple[bytes, str]:
    path = _report_storage_path(storage_path)
    url = (
        f"{supabase_url.rstrip('/')}/storage/v1/object/authenticated/"
        f"report-uploads/{quote(path, safe='/')}"
    )
    try:
        response = client.get(
            url,
            headers={
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
            },
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        suffix = f" (HTTP {status})" if status else ""
        raise MigrationError(f"Unable to download a lab report from Supabase{suffix}") from exc

    content_type = response.headers.get("content-type", "application/octet-stream")
    return response.content, content_type


def _ensure_target_indexes(database: Any) -> None:
    for collection_name, keys, options in INDEXES:
        database[collection_name].create_index(keys, **options)
    database["lab_reports.files"].create_index(
        [("metadata.source_report_id", 1)],
        unique=True,
        partialFilterExpression={"metadata.source_report_id": {"$exists": True}},
        name="lab_reports_source_report_unique",
    )


def _upsert_collection(database: Any, table: str, rows: list[dict[str, Any]]) -> int:
    collection = database[table]
    for row in rows:
        document_id = row["_id"]
        updates = {key: value for key, value in row.items() if key != "_id"}
        collection.update_one(
            {"_id": document_id},
            {"$set": updates},
            upsert=True,
        )
    return len(rows)


def _migrate_report_files(
    source_cursor: Any,
    mongo_database: Any,
    supabase_url: str,
    service_key: str,
) -> int:
    source_cursor.execute(
        "SELECT id, patient_id, storage_path FROM public.lab_reports "
        "WHERE storage_path IS NOT NULL ORDER BY id"
    )
    bucket = GridFSBucket(mongo_database, bucket_name="lab_reports")
    reports_collection = mongo_database["lab_reports"]
    migrated = 0
    with httpx.Client(timeout=60) as client:
        while True:
            reports = source_cursor.fetchmany(100)
            if not reports:
                break
            for report in reports:
                report_id = str(report["id"])
                patient_id = str(report["patient_id"])
                existing = mongo_database["lab_reports.files"].find_one(
                    {"metadata.source_report_id": report_id},
                    {"_id": 1},
                )
                if existing is None:
                    content, content_type = _download_report(
                        client,
                        supabase_url,
                        service_key,
                        report["storage_path"],
                    )
                    filename = PurePosixPath(
                        _report_storage_path(report["storage_path"])
                    ).name
                    file_id = bucket.upload_from_stream(
                        filename,
                        content,
                        metadata={
                            "patient_id": patient_id,
                            "content_type": content_type,
                            "original_filename": filename,
                            "source_report_id": report_id,
                            "source_storage_path": report["storage_path"],
                        },
                    )
                else:
                    file_id = existing["_id"]
                    metadata = mongo_database["lab_reports.files"].find_one(
                        {"_id": file_id},
                        {"metadata.content_type": 1, "filename": 1},
                    )
                    content_type = (
                        (metadata or {}).get("metadata", {}).get(
                            "content_type", "application/octet-stream"
                        )
                    )
                    filename = (metadata or {}).get("filename", "lab-report")

                reports_collection.update_one(
                    {"_id": report_id},
                    {
                        "$set": {
                            "storage_file_id": str(file_id),
                            "content_type": content_type,
                            "original_filename": filename,
                        }
                    },
                )
                migrated += 1
    return migrated


def _read_auth_context(
    source_cursor: Any,
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    source_cursor.execute("SELECT user_id, role FROM public.profiles")
    roles = {
        str(row["user_id"]): str(row["role"])
        for row in source_cursor.fetchall()
    }
    source_cursor.execute(
        "SELECT DISTINCT ON (user_id) user_id, policy_version, accepted, accepted_at "
        "FROM public.consents ORDER BY user_id, accepted_at DESC, id DESC"
    )
    consents = {
        str(row["user_id"]): row
        for row in source_cursor.fetchall()
    }
    return roles, consents


def _migrate_data(source_cursor: Any, mongo_database: Any) -> dict[str, int]:
    roles, consents = _read_auth_context(source_cursor)
    counts: dict[str, int] = {}

    for table in MIGRATION_ORDER:
        order_column = TABLE_ORDER_COLUMNS.get(table, "id")
        source_cursor.execute(f"SELECT * FROM public.{table} ORDER BY {order_column}")
        count = 0
        while True:
            batch = source_cursor.fetchmany(500)
            if not batch:
                break
            rows = [transform_public_row(table, row) for row in batch]
            count += _upsert_collection(mongo_database, table, rows)
        counts[table] = count

    source_cursor.execute(
        "SELECT id, email, encrypted_password, created_at FROM auth.users "
        "WHERE deleted_at IS NULL ORDER BY id"
    )
    count = 0
    while True:
        batch = source_cursor.fetchmany(500)
        if not batch:
            break
        users = [
            transform_auth_user(
                row,
                roles.get(str(row["id"]), "patient"),
                consents.get(str(row["id"])),
            )
            for row in batch
        ]
        count += _upsert_collection(mongo_database, "users", users)
    counts["users"] = count
    return counts


def _table_counts(source_cursor: Any) -> dict[str, int]:
    counts = {}
    for table in MIGRATION_ORDER:
        source_cursor.execute(f"SELECT count(*) AS count FROM public.{table}")
        counts[table] = int(source_cursor.fetchone()["count"])
    source_cursor.execute("SELECT count(*) AS count FROM auth.users WHERE deleted_at IS NULL")
    counts["users"] = int(source_cursor.fetchone()["count"])
    source_cursor.execute(
        "SELECT count(*) AS count FROM public.lab_reports WHERE storage_path IS NOT NULL"
    )
    counts["lab_report_files"] = int(source_cursor.fetchone()["count"])
    return counts


def run_migration(args: argparse.Namespace) -> None:
    load_dotenv()
    source_uri = os.environ.get("SUPABASE_DB_URI")
    mongo_uri = os.environ.get("MONGODB_URI")
    database_name = os.environ.get("MONGODB_DATABASE")
    if not source_uri or not mongo_uri or not database_name:
        raise MigrationError(
            "SUPABASE_DB_URI, MONGODB_URI, and MONGODB_DATABASE must be set"
        )
    supabase_url = os.environ.get("SUPABASE_URL")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if args.apply and not args.skip_files and (not supabase_url or not service_key):
        raise MigrationError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required to migrate report files; "
            "use --skip-files only if file migration is handled separately"
        )

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise MigrationError("Install the migration requirements from requirements.txt") from exc

    mongo_client = MongoClient(mongo_uri)
    try:
        database = mongo_client[database_name]
        with psycopg.connect(source_uri, row_factory=dict_row) as postgres:
            with postgres.cursor() as cursor:
                counts = _table_counts(cursor)
                print("Source row counts (no personal data shown):")
                for table, count in counts.items():
                    print(f"  {table}: {count}")
                print(f"Target MongoDB database: {database_name}")

                if not args.apply:
                    print("Dry run only. Re-run with --apply to import data.")
                    return

                _ensure_target_indexes(database)
                imported = _migrate_data(cursor, database)
                files = 0
                if not args.skip_files:
                    files = _migrate_report_files(
                        cursor, database, supabase_url, service_key
                    )
                print("Upserted rows:")
                for table, count in imported.items():
                    print(f"  {table}: {count}")
                if not args.skip_files:
                    print(f"  lab_report_files: {files}")
    finally:
        mongo_client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="write the idempotent import (default is a read-only dry run)",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="show source counts without writing (the default)",
    )
    parser.add_argument(
        "--skip-files",
        action="store_true",
        help="do not migrate report files from Supabase Storage",
    )
    args = parser.parse_args()
    run_migration(args)


if __name__ == "__main__":
    main()
