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

    # Swish
    swish_merchant_id: str
    swish_cert_path: str
    swish_key_path: str
    swish_payee_number: str = "0700000000"  # Swish-nummer kunder betalar till

    # Hostek
    hostek_api_url: str = "https://partner.ilait.se/api"
    hostek_api_user: str = ""
    hostek_api_password: str = ""
    hostek_customer_id: str = ""
    hostek_domain_id: str = "127978"

    # Postmark
    postmark_api_key: str

    # Admin
    admin_email: str = "kramnet@broadviewab.se"
    admin_secret: str

    # App
    base_url: str = "https://kramnet.se"
    debug: bool = False


settings = Settings()
