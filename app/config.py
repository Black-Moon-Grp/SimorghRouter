from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "SimorghRouter"
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Security
    JWT_SECRET: str = "change-this-to-a-long-random-secret-in-production"
    JWT_EXPIRE_HOURS: int = 24
    DEFAULT_ADMIN_USER: str = "admin"
    DEFAULT_ADMIN_PASS: str = "admin123"

    # Public address & inbound ports
    PUBLIC_HOST: str = ""
    VLESS_TCP_PORT: int = 443
    VLESS_WS_PORT: int = 8443
    VLESS_WS_PATH: str = "/ws"
    TROJAN_PORT: int = 2083

    # Xray paths & ports
    XRAY_BIN: str = "/usr/local/bin/xray"
    XRAY_ASSETS_DIR: str = "/usr/local/share/xray"
    DATA_DIR: str = "/var/lib/v2ray-router"
    XRAY_GRPC_ADDR: str = "127.0.0.1:10085"
    OUTBOUND_BRIDGE_PORT: int = 10808
    OUTBOUND_PORT_A: int = 10809
    OUTBOUND_PORT_B: int = 10810

    # Speed test & scheduler
    DEFAULT_SPEEDTEST_INTERVAL_MIN: int = 30
    WATCHDOG_INTERVAL_SEC: int = 60
    STATS_POLL_INTERVAL_SEC: int = 12
    SPEEDTEST_WORKERS: int = 10
    SPEEDTEST_TIMEOUT_SEC: int = 8
    TEST_URL: str = "https://www.gstatic.com/generate_204"
    SPEED_DL_URL: str = "https://speed.cloudflare.com/__down?bytes=2000000"

    # GeoIP / Geosite
    GEOIP_URL: str = "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat"
    GEOSITE_URL: str = "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat"

    @property
    def data_path(self) -> Path:
        return Path(self.DATA_DIR)

    @property
    def db_url(self) -> str:
        db_file = self.data_path / "v2ray_router.db"
        return f"sqlite+aiosqlite:///{db_file.as_posix()}"

    @property
    def xray_main_config_path(self) -> Path:
        return self.data_path / "xray_main.json"

    @property
    def xray_outbound_config_path(self) -> Path:
        return self.data_path / "xray_outbound.json"

    @property
    def assets_path(self) -> Path:
        return Path(self.XRAY_ASSETS_DIR)


settings = Settings()
