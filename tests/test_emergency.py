import pytest
from app.db import get_supabase, SupabaseClient, set_supabase_client
from app.routers.emergency import _build_doctor_text, sos_trigger, sos_cancel, SosBody, DoctorPayload
from app.auth import AuthUser


@pytest.fixture(autouse=True)
def setup_mock_db():
    client = SupabaseClient("http://mock.supabase.co", "mock-key")
    set_supabase_client(client)
    yield client


def test_doctor_whatsapp_payload_building():
    sb = get_supabase()
    user_id = "user-doctor-test"
    prof_res = sb.table("profiles").insert(
        {
            "user_id": user_id,
            "full_name": "Ravi Kumar",
            "transplant_date": "2025-06-15",
            "doctor_name": "Reddy",
            "doctor_whatsapp": "+919876543210",
        }
    ).execute()
    profile = prof_res.data[0]

    # Add active medicines
    sb.table("medicines").insert(
        {
            "patient_id": profile["id"],
            "name": "Tacrolimus",
            "dose": "2mg",
            "active": True,
        }
    ).execute()

    # Add recent lab
    sb.table("lab_values").insert(
        {
            "patient_id": profile["id"],
            "analyte": "creatinine",
            "value": 1.1,
            "unit": "mg/dL",
            "measured_at": "2026-11-20",
        }
    ).execute()

    wa_url, text = _build_doctor_text(profile, "Mild ankle swelling")
    assert "https://wa.me/919876543210?text=" in wa_url
    assert "Dr. Reddy" in text
    assert "Ravi Kumar" in text
    assert "Mild ankle swelling" in text
    assert "creatinine=1.1mg/dL" in text
    assert "Tacrolimus 2mg" in text
    assert "TransplantCare" in text


def test_sos_trigger_no_contacts_raises():
    sb = get_supabase()
    user_id = "user-no-contacts"
    sb.table("profiles").insert(
        {"user_id": user_id, "full_name": "No Contact Patient"}
    ).execute()

    user = AuthUser(user_id=user_id)
    with pytest.raises(Exception) as exc:
        sos_trigger(SosBody(lat=17.385, lng=78.486), user=user)
    assert "Add at least one emergency contact" in str(exc.value)


def test_sos_trigger_with_contacts_and_cancel():
    sb = get_supabase()
    user_id = "user-sos-patient"
    prof_res = sb.table("profiles").insert(
        {"user_id": user_id, "full_name": "Ananya Sharma"}
    ).execute()
    pid = prof_res.data[0]["id"]

    # Add 2 contacts
    sb.table("emergency_contacts").insert(
        [
            {
                "patient_id": pid,
                "full_name": "Caregiver Mom",
                "phone": "+919811111111",
                "relation": "Mother",
                "sort_order": 0,
            },
            {
                "patient_id": pid,
                "full_name": "Brother",
                "phone": "+919822222222",
                "relation": "Brother",
                "sort_order": 1,
            },
        ]
    ).execute()

    user = AuthUser(user_id=user_id)
    result = sos_trigger(
        SosBody(lat=17.44, lng=78.38, symptoms="Dizziness and chest pain"),
        user=user,
    )

    assert result["dial_uri"] == "tel:+919811111111"
    assert result["first_contact"]["full_name"] == "Caregiver Mom"
    assert len(result["sms_results"]) == 2
    assert len(result["whatsapp_urls"]) == 2
    assert "maps.google.com" in result["whatsapp_urls"][0]["wa_url"]

    # Cancel SOS
    cancel_res = sos_cancel(user=user)
    assert cancel_res["ok"] is True

    # Verify latest event status is cancelled
    events = (
        sb.table("sos_events")
        .select("*")
        .eq("patient_id", pid)
        .order("created_at", desc=True)
        .execute()
        .data
    )
    assert len(events) >= 1
    assert events[0]["status"] == "cancelled"
