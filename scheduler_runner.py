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


if __name__ == "__main__":
    main()
