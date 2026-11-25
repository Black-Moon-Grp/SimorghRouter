import logging
from typing import Optional

import grpc
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

logger = logging.getLogger(__name__)


def _build_xray_protobuf_classes():
    """
    Dynamically registers Xray-core's exact gRPC protobuf descriptors in Python's
    descriptor_pool so we have 100% wire-compatible StatsService & HandlerService
    messages without requiring external .proto compilation.
    """
    pool = descriptor_pool.Default()
    files = []

    # 1. xray/common/serial/typed_message.proto
    f_typed = descriptor_pb2.FileDescriptorProto()
    f_typed.name = "xray/common/serial/typed_message.proto"
    f_typed.package = "xray.common.serial"
    f_typed.syntax = "proto3"
    m_tm = f_typed.message_type.add()
    m_tm.name = "TypedMessage"
    for idx, (fname, ftype) in enumerate(
        [("type", descriptor_pb2.FieldDescriptorProto.TYPE_STRING),
         ("value", descriptor_pb2.FieldDescriptorProto.TYPE_BYTES)],
        start=1,
    ):
        fld = m_tm.field.add()
        fld.name = fname
        fld.number = idx
        fld.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
        fld.type = ftype
    files.append(f_typed)

    # 2. xray/common/protocol/user.proto
    f_user = descriptor_pb2.FileDescriptorProto()
    f_user.name = "xray/common/protocol/user.proto"
    f_user.package = "xray.common.protocol"
    f_user.syntax = "proto3"
    f_user.dependency.append("xray/common/serial/typed_message.proto")
    m_user = f_user.message_type.add()
    m_user.name = "User"
    fld1 = m_user.field.add()
    fld1.name = "level"
    fld1.number = 1
    fld1.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    fld1.type = descriptor_pb2.FieldDescriptorProto.TYPE_UINT32
    fld2 = m_user.field.add()
    fld2.name = "email"
    fld2.number = 2
    fld2.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    fld2.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    fld3 = m_user.field.add()
    fld3.name = "account"
    fld3.number = 3
    fld3.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    fld3.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    fld3.type_name = ".xray.common.serial.TypedMessage"
    files.append(f_user)

    # 3. xray/proxy/vless/account.proto
    f_vless = descriptor_pb2.FileDescriptorProto()
    f_vless.name = "xray/proxy/vless/account.proto"
    f_vless.package = "xray.proxy.vless"
    f_vless.syntax = "proto3"
    m_vless = f_vless.message_type.add()
    m_vless.name = "Account"
    for idx, fname in enumerate(["id", "flow", "encryption"], start=1):
        fld = m_vless.field.add()
        fld.name = fname
        fld.number = idx
        fld.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
        fld.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    files.append(f_vless)

    # 4. xray/proxy/trojan/account.proto
    f_trojan = descriptor_pb2.FileDescriptorProto()
    f_trojan.name = "xray/proxy/trojan/account.proto"
    f_trojan.package = "xray.proxy.trojan"
    f_trojan.syntax = "proto3"
    m_trojan = f_trojan.message_type.add()
    m_trojan.name = "Account"
    fld = m_trojan.field.add()
    fld.name = "password"
    fld.number = 1
    fld.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    fld.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    files.append(f_trojan)

    # 5. xray/app/proxyman/command/command.proto
    f_pm = descriptor_pb2.FileDescriptorProto()
    f_pm.name = "xray/app/proxyman/command/command.proto"
    f_pm.package = "xray.app.proxyman.command"
    f_pm.syntax = "proto3"
    f_pm.dependency.append("xray/common/serial/typed_message.proto")
    f_pm.dependency.append("xray/common/protocol/user.proto")

    m_add = f_pm.message_type.add()
    m_add.name = "AddUserOperation"
    fa = m_add.field.add()
    fa.name = "user"
    fa.number = 1
    fa.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    fa.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    fa.type_name = ".xray.common.protocol.User"

    m_rem = f_pm.message_type.add()
    m_rem.name = "RemoveUserOperation"
    fr = m_rem.field.add()
    fr.name = "email"
    fr.number = 1
    fr.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    fr.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING

    m_alt_req = f_pm.message_type.add()
    m_alt_req.name = "AlterInboundRequest"
    f1 = m_alt_req.field.add()
    f1.name = "tag"
    f1.number = 1
    f1.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    f1.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    f2 = m_alt_req.field.add()
    f2.name = "operation"
    f2.number = 2
    f2.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    f2.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    f2.type_name = ".xray.common.serial.TypedMessage"

    m_alt_resp = f_pm.message_type.add()
    m_alt_resp.name = "AlterInboundResponse"
    files.append(f_pm)

    # 6. xray/app/stats/command/command.proto
    f_st = descriptor_pb2.FileDescriptorProto()
    f_st.name = "xray/app/stats/command/command.proto"
    f_st.package = "xray.app.stats.command"
    f_st.syntax = "proto3"

    m_stat = f_st.message_type.add()
    m_stat.name = "Stat"
    s1 = m_stat.field.add()
    s1.name = "name"
    s1.number = 1
    s1.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    s1.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    s2 = m_stat.field.add()
    s2.name = "value"
    s2.number = 2
    s2.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    s2.type = descriptor_pb2.FieldDescriptorProto.TYPE_INT64

    m_gs_req = f_st.message_type.add()
    m_gs_req.name = "GetStatsRequest"
    gs1 = m_gs_req.field.add()
    gs1.name = "name"
    gs1.number = 1
    gs1.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    gs1.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    gs2 = m_gs_req.field.add()
    gs2.name = "reset"
    gs2.number = 2
    gs2.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    gs2.type = descriptor_pb2.FieldDescriptorProto.TYPE_BOOL

    m_gs_resp = f_st.message_type.add()
    m_gs_resp.name = "GetStatsResponse"
    gsr1 = m_gs_resp.field.add()
    gsr1.name = "stat"
    gsr1.number = 1
    gsr1.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    gsr1.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    gsr1.type_name = ".xray.app.stats.command.Stat"

    m_qs_req = f_st.message_type.add()
    m_qs_req.name = "QueryStatsRequest"
    qs1 = m_qs_req.field.add()
    qs1.name = "pattern"
    qs1.number = 1
    qs1.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    qs1.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    qs2 = m_qs_req.field.add()
    qs2.name = "reset"
    qs2.number = 2
    qs2.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    qs2.type = descriptor_pb2.FieldDescriptorProto.TYPE_BOOL

    m_qs_resp = f_st.message_type.add()
    m_qs_resp.name = "QueryStatsResponse"
    qsr1 = m_qs_resp.field.add()
    qsr1.name = "stat"
    qsr1.number = 1
    qsr1.label = descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
    qsr1.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    qsr1.type_name = ".xray.app.stats.command.Stat"
    files.append(f_st)

    for fd in files:
        try:
            pool.FindFileByName(fd.name)
        except KeyError:
            pool.AddSerializedFile(fd.SerializeToString())

    names = [
        "xray.common.serial.TypedMessage",
        "xray.common.protocol.User",
        "xray.proxy.vless.Account",
        "xray.proxy.trojan.Account",
        "xray.app.proxyman.command.AddUserOperation",
        "xray.app.proxyman.command.RemoveUserOperation",
        "xray.app.proxyman.command.AlterInboundRequest",
        "xray.app.proxyman.command.AlterInboundResponse",
        "xray.app.stats.command.GetStatsRequest",
        "xray.app.stats.command.GetStatsResponse",
        "xray.app.stats.command.QueryStatsRequest",
        "xray.app.stats.command.QueryStatsResponse",
    ]
    return {
        n.split(".")[-1] if not n.endswith("Account") else (
            "VlessAccount" if "vless" in n else "TrojanAccount"
        ): message_factory.GetMessageClass(pool.FindMessageTypeByName(n))
        for n in names
    }


_PB = _build_xray_protobuf_classes()


class XrayGrpcClient:
    """Async gRPC client for Xray-core HandlerService & StatsService (localhost only)."""

    def __init__(self, grpc_addr: str = "127.0.0.1:10085"):
        self.grpc_addr = grpc_addr

    def _channel(self) -> grpc.aio.Channel:
        return grpc.aio.insecure_channel(self.grpc_addr)

    async def add_vless_user(self, inbound_tag: str, email: str, uuid: str, level: int = 0) -> None:
        account = _PB["VlessAccount"](id=uuid, flow="", encryption="none")
        user = _PB["User"](
            level=level,
            email=email,
            account=_PB["TypedMessage"](
                type="xray.proxy.vless.Account",
                value=account.SerializeToString(),
            ),
        )
        op = _PB["AddUserOperation"](user=user)
        req = _PB["AlterInboundRequest"](
            tag=inbound_tag,
            operation=_PB["TypedMessage"](
                type="xray.app.proxyman.command.AddUserOperation",
                value=op.SerializeToString(),
            ),
        )
        async with self._channel() as ch:
            call = ch.unary_unary(
                "/xray.app.proxyman.command.HandlerService/AlterInbound",
                request_serializer=lambda m: m.SerializeToString(),
                response_deserializer=_PB["AlterInboundResponse"].FromString,
            )
            await call(req, timeout=5.0)

    async def add_trojan_user(self, inbound_tag: str, email: str, password: str, level: int = 0) -> None:
        account = _PB["TrojanAccount"](password=password)
        user = _PB["User"](
            level=level,
            email=email,
            account=_PB["TypedMessage"](
                type="xray.proxy.trojan.Account",
                value=account.SerializeToString(),
            ),
        )
        op = _PB["AddUserOperation"](user=user)
        req = _PB["AlterInboundRequest"](
            tag=inbound_tag,
            operation=_PB["TypedMessage"](
                type="xray.app.proxyman.command.AddUserOperation",
                value=op.SerializeToString(),
            ),
        )
        async with self._channel() as ch:
            call = ch.unary_unary(
                "/xray.app.proxyman.command.HandlerService/AlterInbound",
                request_serializer=lambda m: m.SerializeToString(),
                response_deserializer=_PB["AlterInboundResponse"].FromString,
            )
            await call(req, timeout=5.0)

    async def remove_user(self, inbound_tag: str, email: str) -> bool:
        op = _PB["RemoveUserOperation"](email=email)
        req = _PB["AlterInboundRequest"](
            tag=inbound_tag,
            operation=_PB["TypedMessage"](
                type="xray.app.proxyman.command.RemoveUserOperation",
                value=op.SerializeToString(),
            ),
        )
        try:
            async with self._channel() as ch:
                call = ch.unary_unary(
                    "/xray.app.proxyman.command.HandlerService/AlterInbound",
                    request_serializer=lambda m: m.SerializeToString(),
                    response_deserializer=_PB["AlterInboundResponse"].FromString,
                )
                await call(req, timeout=5.0)
            return True
        except grpc.aio.AioRpcError as exc:
            # Xray returns NOT_FOUND / Unknown when user isn't currently in that inbound
            if "not found" in (exc.details() or "").lower():
                return False
            raise

    async def query_user_stats(self, reset: bool = True) -> dict[str, tuple[int, int]]:
        """
        Queries all user traffic counters from StatsService and returns:
        { email: (uplink_bytes, downlink_bytes) }
        """
        req = _PB["QueryStatsRequest"](pattern="user>>>", reset=reset)
        async with self._channel() as ch:
            call = ch.unary_unary(
                "/xray.app.stats.command.StatsService/QueryStats",
                request_serializer=lambda m: m.SerializeToString(),
                response_deserializer=_PB["QueryStatsResponse"].FromString,
            )
            resp = await call(req, timeout=5.0)

        usage: dict[str, list[int]] = {}
        for stat in resp.stat:
            # Format: user>>>email>>>traffic>>>uplink (or downlink)
            parts = stat.name.split(">>>")
            if len(parts) == 4 and parts[0] == "user" and parts[2] == "traffic":
                email = parts[1]
                direction = parts[3]
                if email not in usage:
                    usage[email] = [0, 0]
                if direction == "uplink":
                    usage[email][0] += int(stat.value)
                elif direction == "downlink":
                    usage[email][1] += int(stat.value)
        return {k: (v[0], v[1]) for k, v in usage.items()}

    async def sync_client_add(self, email: str, uuid: str, trojan_password: str) -> None:
        """Adds user to all active inbound tags (VLESS TCP, VLESS WS, Trojan)."""
        for tag in ("in-vless-tcp", "in-vless-ws"):
            try:
                await self.add_vless_user(tag, email, uuid)
            except grpc.aio.AioRpcError as exc:
                if "already exists" not in (exc.details() or "").lower():
                    logger.warning("gRPC add_vless_user(%s, %s) failed: %s", tag, email, exc.details())
        try:
            await self.add_trojan_user("in-trojan", email, trojan_password)
        except grpc.aio.AioRpcError as exc:
            if "already exists" not in (exc.details() or "").lower():
                logger.warning("gRPC add_trojan_user(%s) failed: %s", email, exc.details())

    async def sync_client_remove(self, email: str) -> None:
        """Removes user from all inbound tags."""
        for tag in ("in-vless-tcp", "in-vless-ws", "in-trojan"):
            try:
                await self.remove_user(tag, email)
            except grpc.aio.AioRpcError as exc:
                logger.warning("gRPC remove_user(%s, %s) failed: %s", tag, email, exc.details())
