"""python -m vault.metadata   (shim, owner: Anushka; the app lives in jaiveer_app.py)"""
from vault.common.service import run_service
from vault.metadata.jaiveer_app import create_app

if __name__ == "__main__":
    run_service("meta", create_app)
