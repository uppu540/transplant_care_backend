from functools import lru_cache

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class Settings(BaseSettings):
        model_config = SettingsConfigDict(env_file=".env", extra="ignore")

        supabase_url: str = ""
        supabase_anon_key: str = ""
        supabase_service_role_key: str = ""
        supabase_jwt_secret: str = ""
        supabase_secret_key: str = ""

        mongodb_uri: str = ""
        mongodb_database: str = ""
        jwt_secret: str = ""
        jwt_issuer: str = "transplantcare"
        access_token_minutes: int = 15
        refresh_token_days: int = 30
        app_env: str = "production"

        anthropic_api_key: str = ""
        claude_model: str = "claude-sonnet-4-20250514"

        msg91_auth_key: str = ""
        msg91_sender_id: str = "TRCARE"
        msg91_template_id: str = ""

        twilio_account_sid: str = ""
        twilio_auth_token: str = ""
        twilio_from_number: str = ""

        app_public_url: str = "http://localhost:8000"
        cors_origins: str = "*"
except Exception:
    from pydantic import BaseSettings  # type: ignore

    class Settings(BaseSettings):  # type: ignore
        supabase_url: str = ""
        supabase_anon_key: str = ""
        supabase_service_role_key: str = ""
        supabase_jwt_secret: str = ""

        mongodb_uri: str = ""
        mongodb_database: str = ""
        jwt_secret: str = ""
        jwt_issuer: str = "transplantcare"
        access_token_minutes: int = 15
        refresh_token_days: int = 30
        app_env: str = "production"

        anthropic_api_key: str = ""
        claude_model: str = "claude-sonnet-4-20250514"

        msg91_auth_key: str = ""
        msg91_sender_id: str = "TRCARE"
        msg91_template_id: str = ""

        twilio_account_sid: str = ""
        twilio_auth_token: str = ""
        twilio_from_number: str = ""

        app_public_url: str = "http://localhost:8000"
        cors_origins: str = "*"

        class Config:
            env_file = ".env"
            extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
