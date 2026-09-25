"""python -m vault.node --id n3   (shim, owner: Anushka; the app lives in soum_app.py)"""
from vault.common.service import run_service
from vault.node.soum_app import create_app

if __name__ == "__main__":
    run_service("n1", create_app, needs_id=True)
