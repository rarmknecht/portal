"""mDNS advertisement via zeroconf — _portal._tcp.local."""

from __future__ import annotations

import logging
import socket

from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

log = logging.getLogger(__name__)

_SERVICE_TYPE = "_portal._tcp.local."
_zc: AsyncZeroconf | None = None
_info: ServiceInfo | None = None


async def advertise(port: int, hostname: str | None = None) -> None:
    global _zc, _info

    host = hostname or socket.gethostname()
    local_ip = _local_ip()

    _info = ServiceInfo(
        type_=_SERVICE_TYPE,
        name=f"Portal on {host}.{_SERVICE_TYPE}",
        addresses=[socket.inet_aton(local_ip)],
        port=port,
        properties={"version": "1"},
        server=f"{host}.local.",
    )
    _zc = AsyncZeroconf()
    await _zc.async_register_service(_info)
    log.info("mDNS: advertised %s on %s:%d", _info.name, local_ip, port)


async def deregister() -> None:
    global _zc, _info
    if _zc and _info:
        await _zc.async_unregister_service(_info)
        await _zc.async_close()
        log.info("mDNS: deregistered")
    _zc = None
    _info = None


def _local_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"
