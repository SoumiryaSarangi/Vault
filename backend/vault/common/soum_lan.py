"""LAN-mode helpers shared by `vault up --lan` and `vault join` (docs/soum_lan_demo.md). Owner: Soum.

    detect_lan_ip()                → "192.168.1.101"  (this laptop's address on the network that reaches `toward`)
    lan_config_path()              → "vault.lan.yaml" (hub and joiners use the same file)
"""
import os
import socket

LAN_CONFIG = "vault.lan.yaml"


def detect_lan_ip(toward: str = "8.8.8.8") -> str:
    """The local IP the OS would use to reach `toward`. A UDP connect sends no packets, it only picks a route.

    Pass the hub's IP from a joining laptop so the answer is the interface on the hub's network (a laptop
    with Wi-Fi and a hotspot has several). Falls back to 127.0.0.1 when there's no network at all.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((toward, 9))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def lan_config_path() -> str:
    """VAULT_CONFIG if set, else vault.lan.yaml in the working directory."""
    return os.environ.get("VAULT_CONFIG") or LAN_CONFIG
