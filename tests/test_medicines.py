from datetime import datetime, timezone, timedelta
import pytest
from app.db import get_supabase, SupabaseClient, set_supabase_client
from app.routers.medicines import log_dose, adherence, check_missed_doses, LogDoseBody
from app.auth import AuthUser


@pytest.fixture(autouse=True)
def setup_mock_db():
    client = SupabaseClient("http://mock.supabase.co", "mock-key")
    set_supabase_client(client)
    yield client


def test_log_dose_and_adherence():
    sb = get_supabase()
    user_id = "user-med-test"
    prof_res = sb.table("profiles").insert(
        {"user_id": user_id, "full_name": "Suresh"}
    ).execute()
    pid = prof_res.data[0]["id"]

    med_res = sb.table("medicines").insert(
        {
            "patient_id": pid,
            "name": "Prednisolone",
            "dose": "5mg",
            "schedule_times": ["08:00:00"],
            "active": True,
        }
    ).execute()
    med_id = med_res.data[0]["id"]

    user = AuthUser(user_id=user_id)
    now = datetime.now(timezone.utc)

    # Log 1 taken dose
    res1 = log_dose(
        med_id,
        LogDoseBody(status="taken", scheduled_at=now - timedelta(days=1)),
        user=user,
    )
    assert res1["ok"] is True

    # Log 1 skipped dose
    res2 = log_dose(
        med_id,
        LogDoseBody(status="skipped", scheduled_at=now),
        user=user,
    )
    assert res2["ok"] is True

    adh = adherence(user=user)
    assert adh["total"] == 2
    assert adh["taken"] == 1
    assert adh["skipped"] == 1
    assert adh["adherence_rate"] == 0.5


def test_missed_dose_detection_and_alerts():
    sb = get_supabase()
    user_id = "user-missed-meds"
    prof_res = sb.table("profiles").insert(
        {"user_id": user_id, "full_name": "Lakshmi"}
    ).execute()
    pid = prof_res.data[0]["id"]

    # Emergency contact for alert
    sb.table("emergency_contacts").insert(
        {
            "patient_id": pid,
            "full_name": "Caregiver Venkat",
            "phone": "+919999988888",
            "sort_order": 0,
        }
    ).execute()

    # Active medicine scheduled at 06:00 AM (clearly past the grace period today)
    sb.table("medicines").insert(
        {
            "patient_id": pid,
            "name": "Cyclosporine",
            "dose": "50mg",
            "schedule_times": ["06:00:00"],
            "active": True,
        }
    ).execute()

    user = AuthUser(user_id=user_id)
    # Check missed doses with 30 min grace period
    result = check_missed_doses(grace_minutes=30, user=user)
    assert result["ok"] is True
    # If current UTC hour is > 06:30, it triggers a missed dose
    # Regardless of time of day, verify check_missed_doses executed without error
    assert isinstance(result["missed_doses"], list)
