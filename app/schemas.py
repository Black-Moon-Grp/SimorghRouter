from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator


# --- Auth ---
class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6, max_length=128)


# --- Outbounds ---
class BulkImportRequest(BaseModel):
    links_text: Optional[str] = None
    subscription_url: Optional[str] = None


class BulkImportResponse(BaseModel):
    imported: int
    skipped_duplicates: int
    errors: list[str]


class OutboundResponse(BaseModel):
    id: int
    name: str
    protocol: str
    address: str
    port: int
    is_alive: bool
    is_active: bool
    enabled: bool
    latency_ms: Optional[float] = None
    speed_mbps: Optional[float] = None
    last_error: Optional[str] = None
    last_tested_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SpeedTestTriggerRequest(BaseModel):
    run_speed_dl: bool = False


# --- Clients ---
class ClientCreate(BaseModel):
    username: str = Field(..., min_length=2, max_length=48, pattern=r"^[a-zA-Z0-9_.-]+$")
    quota_gb: float = Field(default=0.0, ge=0.0)
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=3650)
    expires_at: Optional[datetime] = None
    enabled: bool = True


class ClientUpdate(BaseModel):
    quota_gb: Optional[float] = Field(default=None, ge=0.0)
    expires_at: Optional[datetime] = None
    clear_expiration: bool = False
    enabled: Optional[bool] = None
    reset_traffic: bool = False


class ClientResponse(BaseModel):
    id: int
    username: str
    email: str
    uuid: str
    trojan_password: str
    enabled: bool
    revoked_reason: Optional[str] = None
    quota_bytes: int
    upload_bytes: int
    download_bytes: int
    total_used_bytes: int
    expires_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ClientLinksResponse(BaseModel):
    username: str
    public_host: str
    vless_tcp: str
    vless_ws: str
    trojan: str


# --- Routing ---
class RoutingRuleCreate(BaseModel):
    rule_type: Literal["domain", "ip"]
    value: str = Field(..., min_length=1, max_length=255)
    action: Literal["direct", "proxy", "block"]
    description: Optional[str] = Field(default=None, max_length=255)
    enabled: bool = True

    @field_validator("value")
    @classmethod
    def clean_value(cls, v: str) -> str:
        v = v.strip()
        if not v or any(c in v for c in ("\n", "\r", ";", "{", "}")):
            raise ValueError("Invalid routing rule value")
        return v


class RoutingRuleResponse(BaseModel):
    id: int
    rule_type: str
    value: str
    action: str
    description: Optional[str] = None
    enabled: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Geo ---
class GeoUpdateRequest(BaseModel):
    geoip_url: Optional[str] = None
    geosite_url: Optional[str] = None
    weekly_auto_update: Optional[bool] = None


class GeoStatusResponse(BaseModel):
    geoip_exists: bool
    geoip_size_mb: float
    geoip_updated_at: Optional[datetime] = None
    geosite_exists: bool
    geosite_size_mb: float
    geosite_updated_at: Optional[datetime] = None
    geoip_url: str
    geosite_url: str
    weekly_auto_update: bool


# --- System Settings ---
class SystemSettingsUpdate(BaseModel):
    speedtest_interval_min: Optional[int] = Field(default=None, ge=1, le=1440)
    run_speed_dl_on_schedule: Optional[bool] = None
    public_host: Optional[str] = Field(default=None, max_length=255)
    vless_tcp_port: Optional[int] = Field(default=None, ge=1, le=65535)
    vless_ws_port: Optional[int] = Field(default=None, ge=1, le=65535)
    vless_ws_path: Optional[str] = Field(default=None, max_length=128)
    trojan_port: Optional[int] = Field(default=None, ge=1, le=65535)


class SystemOverviewResponse(BaseModel):
    xray_main_running: bool
    xray_outbound_running: bool
    active_outbound: Optional[OutboundResponse] = None
    total_outbounds: int
    alive_outbounds: int
    total_clients: int
    active_clients: int
    total_traffic_bytes: int
    speedtest_interval_min: int
    run_speed_dl_on_schedule: bool
    public_host: str
    vless_tcp_port: int
    vless_ws_port: int
    vless_ws_path: str
    trojan_port: int
    last_speedtest_at: Optional[str] = None
    last_watchdog_at: Optional[str] = None
    is_testing_now: bool
