"""Logging: one line per event, logs/<pid>.log, format `ts pid level message` (TECH_STACK §5).
Owner: Anushka (shared).
"""
import logging
import sys
from pathlib import Path


def setup_logging(pid: str, logs_dir: str = "./logs", level: int = logging.INFO) -> logging.Logger:
    Path(logs_dir).mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(f"%(asctime)s {pid} %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    fh = logging.FileHandler(Path(logs_dir) / f"{pid}.log", encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)
    # uvicorn access logs are noisy at 2 heartbeats/s × 6 nodes
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return logging.getLogger(pid)
