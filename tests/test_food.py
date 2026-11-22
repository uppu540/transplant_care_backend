import pytest
from app.db import get_supabase, SupabaseClient, set_supabase_client
from app.routers.food import estimate, log_food, suggestions, EstimateBody, LogBody
from app.auth import AuthUser


@pytest.fixture(autouse=True)
def setup_mock_db():
    client = SupabaseClient("http://mock.supabase.co", "mock-key")
    set_supabase_client(client)
    # Seed food composition
    client.table("food_composition").insert(
        [
            {
                "name_en": "Cooked white rice (100g)",
                "potassium_mg": 35,
                "sodium_mg": 1,
                "phosphorus_mg": 37,
                "protein_g": 2.7,
                "fluid_ml": 70,
                "calories_kcal": 130,
            },
            {
                "name_en": "Dal (cooked, 100g)",
                "potassium_mg": 200,
                "sodium_mg": 5,
                "phosphorus_mg": 100,
                "protein_g": 9.0,
                "fluid_ml": 75,
                "calories_kcal": 116,
            },
            {
                "name_en": "Cucumber (100g)",
                "potassium_mg": 150,
                "sodium_mg": 2,
                "phosphorus_mg": 20,
                "protein_g": 0.7,
                "fluid_ml": 95,
                "calories_kcal": 15,
            },
        ]
    ).execute()
    yield client


def test_food_estimate_heuristic():
    user = AuthUser(user_id="food-test-user")
    sb = get_supabase()
    sb.table("profiles").insert(
        {"user_id": user.user_id, "full_name": "Food Patient"}
    ).execute()

    est = estimate(EstimateBody(text="rice and dal"), user=user)
    assert est["estimate_source"] == "composition"
    assert est["potassium_mg"] == 235.0  # 35 + 200
    assert est["protein_g"] == 11.7     # 2.7 + 9.0
    assert est["calories_kcal"] == 246.0


def test_food_log_and_suggestions():
    user = AuthUser(user_id="food-budget-user")
    sb = get_supabase()
    prof_res = sb.table("profiles").insert(
        {"user_id": user.user_id, "full_name": "Food Budget Patient"}
    ).execute()
    pid = prof_res.data[0]["id"]

    # Without limits, suggestions should reject with 400
    with pytest.raises(Exception) as exc:
        suggestions(user=user)
    assert "No doctor-set dietary limits" in str(exc.value)

    # Set doctor limits
    sb.table("dietary_limits").insert(
        {
            "patient_id": pid,
            "potassium_mg": 1500,
            "sodium_mg": 1500,
            "phosphorus_mg": 800,
            "protein_g": 50,
            "calories_kcal": 1800,
        }
    ).execute()

    # Log meal
    res = log_food(
        LogBody(
            description="Cucumber salad",
            meal_type="lunch",
            potassium_mg=150,
            sodium_mg=2,
            phosphorus_mg=20,
            protein_g=0.7,
            calories_kcal=15,
        ),
        user=user,
    )
    assert res["ok"] is True

    # Now suggestions should work and show remaining budget
    sug_res = suggestions(user=user)
    assert sug_res["remaining"]["potassium_mg"] == 1350.0
    assert len(sug_res["ideas"]) > 0
    assert "informational only" in sug_res["disclaimer"].lower()
