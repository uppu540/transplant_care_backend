import pytest
from app.db import get_supabase, SupabaseClient, set_supabase_client
from app.routers.me import (
    create_caregiver_invite,
    accept_caregiver_invite,
    list_caregiver_patients,
    get_caregiver_patient_view,
    AcceptInviteBody,
)
from app.auth import AuthUser


@pytest.fixture(autouse=True)
def setup_mock_db():
    client = SupabaseClient("http://mock.supabase.co", "mock-key")
    set_supabase_client(client)
    yield client


def test_caregiver_flow_end_to_end():
    sb = get_supabase()
    # 1. Setup Patient
    patient_user_id = "patient-user-1"
    p_res = sb.table("profiles").insert(
        {
            "user_id": patient_user_id,
            "full_name": "Arjun Patel",
            "transplant_date": "2024-03-10",
            "doctor_name": "Sharma",
            "doctor_whatsapp": "+919876500000",
            "role": "patient",
        }
    ).execute()
    patient_id = p_res.data[0]["id"]

    # Add patient medicines and dietary limits
    sb.table("medicines").insert(
        {
            "patient_id": patient_id,
            "name": "Tacrolimus",
            "dose": "1.5mg",
            "schedule_times": ["08:00:00", "20:00:00"],
            "active": True,
        }
    ).execute()

    sb.table("dietary_limits").insert(
        {
            "patient_id": patient_id,
            "potassium_mg": 2500,
            "sodium_mg": 2000,
            "protein_g": 65,
        }
    ).execute()

    # 2. Patient creates invite
    patient_user = AuthUser(user_id=patient_user_id)
    invite = create_caregiver_invite(user=patient_user)
    assert invite["ok"] is True
    code = invite["invite_code"]
    assert len(code) == 8

    # 3. Setup Caregiver User
    caregiver_user_id = "caregiver-user-2"
    sb.table("profiles").insert(
        {
            "user_id": caregiver_user_id,
            "full_name": "Priya Patel",
            "role": "caregiver",
        }
    ).execute()
    caregiver_user = AuthUser(user_id=caregiver_user_id)

    # 4. Caregiver accepts invite
    acc = accept_caregiver_invite(AcceptInviteBody(invite_code=code), user=caregiver_user)
    assert acc["ok"] is True
    assert acc["patient_id"] == patient_id

    # 5. Caregiver lists linked patients
    pts = list_caregiver_patients(user=caregiver_user)
    assert len(pts["patients"]) == 1
    assert pts["patients"][0]["full_name"] == "Arjun Patel"

    # 6. Caregiver views read-only patient dashboard
    view = get_caregiver_patient_view(patient_id, user=caregiver_user)
    assert view["read_only"] is True
    assert view["patient"]["full_name"] == "Arjun Patel"
    assert len(view["medicines"]) == 1
    assert view["budget"]["limits_set"] is True
    assert view["budget"]["limits"]["potassium_mg"] == 2500

    # 7. Unauthorized third party cannot access patient view
    unauthorized_user = AuthUser(user_id="random-stranger")
    with pytest.raises(Exception) as exc:
        get_caregiver_patient_view(patient_id, user=unauthorized_user)
    assert "Not authorized" in str(exc.value)
