import asyncio

import pytest
from fastapi import HTTPException

from app.auth import AuthUser
from app.config import Settings
from app.routers.food import EstimateBody, LogBody, estimate, log_food, suggestions


FOOD_ROWS = [
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


def test_food_estimate_heuristic(
    mongo_repository,
    mongo_database,
    profile_factory,
    monkeypatch,
):
    from app.services import food_estimate

    monkeypatch.setattr(
        food_estimate,
        "get_settings",
        lambda: Settings(_env_file=None, anthropic_api_key=""),
    )
    asyncio.run(mongo_database["food_composition"].insert_many(FOOD_ROWS))
    user = AuthUser(user_id="food-test-user")
    profile_factory(user.user_id, full_name="Food Patient")

    estimate_result = asyncio.run(
        estimate(
            EstimateBody(text="rice and dal"),
            user=user,
            repository=mongo_repository,
        )
    )
    assert estimate_result["estimate_source"] == "composition"
    assert estimate_result["potassium_mg"] == 235.0
    assert estimate_result["protein_g"] == 11.7
    assert estimate_result["calories_kcal"] == 246.0


def test_food_log_and_suggestions(
    mongo_repository,
    mongo_database,
    profile_factory,
):
    asyncio.run(mongo_database["food_composition"].insert_many(FOOD_ROWS))
    user = AuthUser(user_id="food-budget-user")
    profile = profile_factory(user.user_id, full_name="Food Budget Patient")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(suggestions(user=user, repository=mongo_repository))
    assert exc.value.status_code == 400
    assert "No doctor-set dietary limits" in exc.value.detail

    asyncio.run(
        mongo_repository.insert_patient_record(
            "dietary_limits",
            profile["id"],
            {
                "potassium_mg": 1500,
                "sodium_mg": 1500,
                "phosphorus_mg": 800,
                "protein_g": 50,
                "calories_kcal": 1800,
            },
        )
    )
    log_result = asyncio.run(
        log_food(
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
            repository=mongo_repository,
        )
    )
    assert log_result["ok"] is True

    suggestion_result = asyncio.run(
        suggestions(user=user, repository=mongo_repository)
    )
    assert suggestion_result["remaining"]["potassium_mg"] == 1350.0
    assert len(suggestion_result["ideas"]) > 0
    assert "informational only" in suggestion_result["disclaimer"].lower()
