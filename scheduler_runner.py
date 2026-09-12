"""Dispara el procesamiento periódico de facturas programadas en HSC."""

import os

import requests


def main():
    app_url = os.getenv("HSC_APP_URL", "https://hsc-app-3.onrender.com").rstrip("/")
    scheduler_key = os.environ["HSC_SCHEDULER_KEY"]
    response = requests.post(
        f"{app_url}/api/invoice-schedules/run",
        headers={"X-HSC-Scheduler-Key": scheduler_key},
        timeout=120,
    )
    print(f"HSC scheduler: HTTP {response.status_code}")
    print(response.text[:2000])
    response.raise_for_status()
    result = response.json()
    if not result.get("ok") or not result.get("drive_backup", True) or any(
        item.get("status") == "error" for item in result.get("processed", [])
    ):
        raise RuntimeError("La revisión mensual terminó con pendientes. Consulta el centro de avisos de HSC.")


if __name__ == "__main__":
    main()
