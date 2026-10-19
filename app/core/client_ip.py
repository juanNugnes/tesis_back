from functools import lru_cache
from ipaddress import ip_address, ip_network

from fastapi import HTTPException, Request


@lru_cache(maxsize=16)
def _trusted_networks(cidrs: str):
    return tuple(ip_network(value.strip(), strict=False)
                 for value in cidrs.split(",") if value.strip())


def otp_client_ip(request: Request, trusted_proxy_cidrs: str) -> str:
    # Uvicorn must run with --no-proxy-headers so this is the transport peer.
    try:
        peer = ip_address(request.client.host if request.client else "")
    except ValueError:
        raise HTTPException(status_code=503, detail="Client address unavailable") from None

    trusted = _trusted_networks(trusted_proxy_cidrs)
    if not any(peer in network for network in trusted):
        return peer.compressed

    forwarded = request.headers.getlist("x-forwarded-for")
    if len(forwarded) != 1:
        return peer.compressed
    try:
        hops = [ip_address(value.strip()) for value in forwarded[0].split(",")]
    except ValueError:
        return peer.compressed
    for hop in reversed(hops):
        if not any(hop in network for network in trusted):
            return hop.compressed
    return peer.compressed
