"""python -m vault.gateway   (shim, owner: Anushka; the app lives in soum_app.py)"""
from vault.common.service import run_service
from vault.gateway.soum_app import create_app

if __name__ == "__main__":
    run_service("gw", create_app)
