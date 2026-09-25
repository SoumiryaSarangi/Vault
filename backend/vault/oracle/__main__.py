"""python -m vault.oracle   (shim, owner: Anushka; the app lives in urooz_app.py)"""
from vault.common.service import run_service
from vault.oracle.urooz_app import create_app

if __name__ == "__main__":
    run_service("oracle", create_app)
