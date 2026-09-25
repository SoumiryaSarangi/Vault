"""Seed ~200 synthetic clinic files (xray-0042.png, lab-report-0113.pdf, 20 KB–3 MB random bytes)
through the gateway (ARCHITECTURE §6). Owner: Urooz. Task U2.
    async def seed(rpc, bucket: str = "clinic", count: int = 200, seed: int = 42) -> int
No real patient data, ever.
"""
