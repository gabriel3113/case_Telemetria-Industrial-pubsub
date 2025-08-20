from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    project_id: str = Field(..., alias="PROJECT_ID")
    topic_id: str = Field("telemetry-events", alias="TOPIC_ID")
    allow_anon: bool = Field(False, alias="ALLOW_ANON")
    ordering_key_field: str | None = Field(None, alias="ORDERING_KEY_FIELD")
    # 👇 novos
    credentials_path: str | None = Field(None, alias="CREDENTIALS_PATH")
    credentials_json_b64: str | None = Field(None, alias="CREDENTIALS_JSON_B64")

    class Config:
        env_file = ".env"
        extra = "allow"

settings = Settings()
