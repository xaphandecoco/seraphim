from typing import Optional

from pydantic_settings import BaseSettings


class BootstrapConfig(BaseSettings):
    """Tier 1: Immutable bootstrap config read from file on startup."""
    DATABASE_URL: str
    BOOTSTRAP_CONFIG_PATH: str = "/app/config/bootstrap.json"
    
    class Config:
        env_file = ".env"
        case_sensitive = True


class DynamicSettings:
    """Tier 2: Runtime-mutable settings loaded from database admin_settings table."""
    
    _instance = None
    _settings: dict = {}
    _initialized: bool = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    async def initialize(self, db_session=None):
        """Load all settings from DB into memory on app startup."""
        if db_session is None:
            return
        from sqlalchemy import select
        from app.models import AdminSetting
        result = await db_session.execute(select(AdminSetting))
        self._settings = {
            s.key: s.value.get("value") if isinstance(s.value, dict) and "value" in s.value else s.value
            for s in result.scalars().all()
        }
        self._initialized = True
    
    async def reload(self, db_session):
        """Reload all settings from DB into memory."""
        from sqlalchemy import select
        from app.models import AdminSetting
        result = await db_session.execute(select(AdminSetting))
        self._settings = {
            s.key: s.value.get("value") if isinstance(s.value, dict) and "value" in s.value else s.value
            for s in result.scalars().all()
        }
    
    def get(self, key: str, default=None):
        return self._settings.get(key, default)
    
    def get_str(self, key: str, default: str = "") -> str:
        val = self._settings.get(key, default)
        return str(val) if val is not None else default
    
    def get_int(self, key: str, default: int = 0) -> int:
        val = self._settings.get(key, default)
        if val is None:
            return default
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        val = self._settings.get(key, default)
        if val is None:
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default
    
    def get_bool(self, key: str, default: bool = False) -> bool:
        val = self._settings.get(key, default)
        if isinstance(val, bool):
            return val
        return str(val).lower() in ("true", "1", "yes", "on") if val is not None else default
    
    def get_redis_url(self) -> str:
        return self.get_str("redis_url", "redis://redis:6379/0")
    
    def get_compreface_url(self) -> str:
        return self.get_str("compreface_url", "")
    
    def get_compreface_api_key(self) -> str:
        return self.get_str("compreface_api_key", "")

    def get_compreface_detect_api_key(self) -> str:
        return self.get_str("compreface_detect_api_key", "")

    def get_compreface_recognize_api_key(self) -> str:
        return self.get_str("compreface_recognize_api_key", "")
    
    def get_jwt_secret(self) -> str:
        return self.get_str("jwt_secret", "")
    
    def get_access_token_expire_minutes(self) -> int:
        return self.get_int("access_token_expire_minutes", 15)
    
    def get_refresh_token_expire_days(self) -> int:
        return self.get_int("refresh_token_expire_days", 7)
    
    def get_similarity_threshold_high(self) -> float:
        return self.get_float("similarity_threshold_high", 0.98)
    
    def get_similarity_threshold_medium(self) -> float:
        return self.get_float("similarity_threshold_medium", 0.91)
    
    def get_queue_hard_limit(self) -> int:
        return self.get_int("queue_hard_limit", 500)
    
    def get_queue_resume_limit(self) -> int:
        return self.get_int("queue_resume_limit", 400)
    
    def get_dedup_window_seconds(self) -> int:
        return self.get_int("dedup_window_seconds", 30)
    
    def get_task_expiry_days(self) -> int:
        return self.get_int("task_expiry_days", 31)
    
    def get_face_retention_days(self) -> int:
        return self.get_int("face_retention_days", 60)
    
    def get_allowed_domain(self) -> str:
        return self.get_str("allowed_domain", "lightnc.org")
    
    def get_enable_google_oauth(self) -> bool:
        return self.get_bool("enable_google_oauth", False)
    
    def get_google_client_id(self) -> str:
        return self.get_str("google_client_id", "")
    
    def get_google_client_secret(self) -> str:
        return self.get_str("google_client_secret", "")
    
    def is_safe_mode(self) -> bool:
        return self.get_bool("safe_mode", False)

    def is_setup_complete(self) -> bool:
        return self.get_bool("setup_complete", False)

    def get_active_event_id(self) -> int | None:
        val = self._settings.get("active_event_id")
        if val is None or val == "" or val == 0:
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    def get_sunday_series_id(self) -> int | None:
        val = self._settings.get("sunday_series_id")
        if val is None or val == "" or val == 0:
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    def get_powerhouse_series_id(self) -> int | None:
        val = self._settings.get("powerhouse_series_id")
        if val is None or val == "" or val == 0:
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None


dynamic_settings = DynamicSettings()


# Legacy settings object for backwards compatibility during migration
class LegacySettings(BaseSettings):
    DATABASE_URL: str
    JWT_SECRET: str = ""
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    COMPREFACE_API_KEY: Optional[str] = None
    COMPREFACE_URL: Optional[str] = None
    FRONTEND_URL: str = "http://localhost:5173"
    ENVIRONMENT: str = "development"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    PIN_EXPIRE_HOURS: int = 24
    ALLOWED_DOMAIN: str = "lightnc.org"
    STORAGE_PATH: str = "data"
    WORKER_ID: str = "worker-1"
    POLL_INTERVAL: int = 1
    AUDIT_FREQUENCY: int = 10
    REDIS_URL: str = "redis://redis:6379/0"

    class Config:
        env_file = ".env"
        case_sensitive = True


legacy_settings = LegacySettings()
