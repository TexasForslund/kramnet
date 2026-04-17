from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database
    database_url: str

    # Security
    secret_key: str
    access_token_expire_minutes: int = 60 * 24  # 24 hours

    # Klarna
    klarna_api_user: str = ""
    klarna_api_password: str = ""
    klarna_api_url: str = "https://api.klarna.com"

    # Hostek
    hostek_api_url: str = "https://partner.ilait.se/api"
    hostek_api_user: str = ""
    hostek_api_password: str = ""
    hostek_customer_id: str = ""
    hostek_domain_id: str = "127978"

    # SMTP
    smtp_host: str = "bulkmail.ilait.se"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "noreply@kramnetkund.se"
    smtp_from_name: str = "Kramnet"

    # Admin
    admin_email: str = "kramnet@broadviewab.se"
    admin_secret: str
    allowed_admin_ips: List[str] = []

    # App
    base_url: str = "https://kramnet.se"
    debug: bool = False


settings = Settings()
