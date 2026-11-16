"""Create/update one minutely cron-job.org job. Run AFTER deploying the bot."""
import getpass
import json
import os
from pathlib import Path
import sys

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hsk_bot.config import Config  # noqa: E402


def main():
    os.chdir(ROOT)
    config = Config.from_env()
    url = os.environ.get("PUBLIC_URL", "").rstrip("/")
    if not url.startswith("https://"):
        raise SystemExit("Добавьте PUBLIC_URL с адресом Render в .env")
    api_key = getpass.getpass("API key cron-job.org из Settings → API (ввод скрыт): ").strip()
    state_path = ROOT / ".deploy-state.json"
    saved = json.loads(state_path.read_text()) if state_path.exists() else {}
    job_id = saved.get("cron_job_id")
    job = {
        "title": "Private HSK — daily lesson and delivery retries", "url": url + "/cron",
        "enabled": True, "saveResponses": False, "requestMethod": 1, "requestTimeout": 30,
        "schedule": {"timezone": "Etc/UTC", "expiresAt": 0, "hours": [-1], "minutes": [-1],
                     "mdays": [-1], "months": [-1], "wdays": [-1]},
        "extendedData": {"headers": {"Authorization": "Bearer " + config.cron_secret}},
        "notification": {"onFailure": True, "onFailureCount": 3, "onSuccess": True, "onDisable": True},
    }
    endpoint = f"https://api.cron-job.org/jobs/{job_id}" if job_id else "https://api.cron-job.org/jobs"
    method = "PATCH" if job_id else "PUT"
    try:
        response = requests.request(method, endpoint, json={"job": job}, timeout=(5, 20),
                                    headers={"Authorization": "Bearer " + api_key})
        if response.status_code != 200:
            raise SystemExit(f"cron-job.org вернул HTTP {response.status_code}. Ключ не сохранён и не выведен.")
        result = response.json()
    except (requests.RequestException, ValueError):
        raise SystemExit("Не удалось связаться с cron-job.org. Проверьте сеть и повторите.") from None
    if not job_id:
        saved["cron_job_id"] = result["jobId"]
        state_path.write_text(json.dumps(saved) + "\n")
    print(f"Готово. Задание {saved['cron_job_id']}: каждую минуту, POST /cron.")
    print("Повторный запуск обновит это же задание. API key не сохраняется.")


if __name__ == "__main__":
    main()
