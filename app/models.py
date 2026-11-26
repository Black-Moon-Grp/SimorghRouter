from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OutboundConfig(Base):
    __tablename__ = "outbound_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False)  # vless, vmess, trojan, shadowsocks
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_link: Mapped[str] = mapped_column(Text, nullable=False)
    xray_outbound_json: Mapped[str] = mapped_column(Text, nullable=False)
    subscription_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    is_alive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    speed_mbps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    last_tested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    logs: Mapped[list["LatencyLog"]] = relationship(
        "LatencyLog", back_populates="outbound", cascade="all, delete-orphan"
    )


class LatencyLog(Base):
    __tablename__ = "latency_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    outbound_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("outbound_configs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    speed_mbps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_alive: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    tested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    outbound: Mapped["OutboundConfig"] = relationship("OutboundConfig", back_populates="logs")


class ClientAccount(Base):
    __tablename__ = "client_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    uuid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    trojan_password: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    revoked_reason: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    quota_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)  # 0 = unlimited
    upload_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    download_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    @property
    def total_used_bytes(self) -> int:
        return (self.upload_bytes or 0) + (self.download_bytes or 0)


class ClientActivityLog(Base):
    __tablename__ = "client_activity_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("client_accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_ip: Mapped[str] = mapped_column(String(64), nullable=False)
    destination: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    network: Mapped[str] = mapped_column(String(16), nullable=False)  # tcp, udp
    inbound_tag: Mapped[str] = mapped_column(String(32), nullable=False)
    outbound_tag: Mapped[str] = mapped_column(String(32), nullable=False)  # proxy, direct, block
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class RoutingRule(Base):
    __tablename__ = "routing_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_type: Mapped[str] = mapped_column(String(16), nullable=False)  # domain, ip
    value: Mapped[str] = mapped_column(String(255), nullable=False)  # e.g. geosite:google, domain:example.com, 1.2.3.0/24, geoip:cn
    action: Mapped[str] = mapped_column(String(16), nullable=False)  # direct, proxy, block
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
