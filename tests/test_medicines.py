import asyncio
from datetime import datetime, timezone

from app.auth import AuthUser
from app.routers import medicines
from app.routers.medicines import (
    LogDoseBody,
    adherence,
    check_missed_doses,
    log_dose,
)


def test_log_dose_and_adherence(mongo_repository, profile_factory):
    profile = profile_factory("user-med-test", full_name="Suresh")
    patient_id = profile["id"]
    medicine = asyncio.run(
        mongo_repository.insert_patient_record(
            "medicines",
            patient_id,
            {
                "name": "Prednisolone",
                "dose": "5mg",
                "schedule_times": ["08:00:00"],
                "active": True,
            },
        )
    )
    user = AuthUser(user_id="user-med-test")
    now = datetime.now(timezone.utc)

    taken = asyncio.run(
        log_dose(
            medicine["id"],
            LogDoseBody(status="taken", scheduled_at=now.replace(hour=8)),
            user=user,
            repository=mongo_repository,
        )
    )
    skipped = asyncio.run(
        log_dose(
            medicine["id"],
            LogDoseBody(status="skipped", scheduled_at=now.replace(hour=9)),
            user=user,
            repository=mongo_repository,
        )
    )
    assert taken["ok"] is True
    assert skipped["ok"] is True

    result = asyncio.run(adherence(user=user, repository=mongo_repository))
    assert result["total"] == 2
    assert result["taken"] == 1
    assert result["skipped"] == 1
    assert result["adherence_rate"] == 0.5


def test_missed_dose_detection_and_alerts(
    mongo_repository,
    profile_factory,
    monkeypatch,
):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 22, 18, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(medicines, "datetime", FixedDateTime)
    monkeypatch.setattr(
        medicines,
        "send_sms",
        lambda phone, message: {"ok": True, "dry_run": True},
    )
    profile = profile_factory("user-missed-meds", full_name="Lakshmi")
    patient_id = profile["id"]
    asyncio.run(
        mongo_repository.insert_patient_record(
            "emergency_contacts",
            patient_id,
            {
                "full_name": "Caregiver Venkat",
                "phone": "+919999988888",
                "sort_order": 0,
            },
        )
    )
    asyncio.run(
        mongo_repository.insert_patient_record(
            "medicines",
            patient_id,
            {
                "name": "Cyclosporine",
                "dose": "50mg",
                "schedule_times": ["06:00:00"],
                "active": True,
            },
        )
    )

    result = asyncio.run(
        check_missed_doses(
            grace_minutes=30,
            user=AuthUser(user_id="user-missed-meds"),
            repository=mongo_repository,
        )
    )
    assert result["ok"] is True
    assert len(result["missed_doses"]) == 1
    logs = asyncio.run(
        mongo_repository.find_patient_records("dose_logs", patient_id)
    )
    assert len(logs) == 1
    assert logs[0]["status"] == "missed"
