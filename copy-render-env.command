#!/bin/zsh
set -e
cd -- "${0:A:h}"
.venv/bin/python - <<'PY'
import json
import subprocess
from dotenv import dotenv_values

values = dotenv_values('.env', interpolate=False)
keys = ('BOT_TOKEN', 'OWNER_TELEGRAM_ID', 'DATABASE_URL', 'WEBHOOK_SECRET', 'CRON_SECRET', 'DEFAULT_TIMEZONE')
missing = [key for key in keys if not values.get(key)]
if missing:
    raise SystemExit('Не заполнены настройки: ' + ', '.join(missing))
if not values['DATABASE_URL'].startswith(('postgresql://', 'postgres://')):
    raise SystemExit('Сначала подключите Neon через connect-neon.command.')
lines = [key + '=' + json.dumps(values[key], ensure_ascii=False) for key in keys]
lines.append('PYTHON_VERSION=3.12.12')
subprocess.run(['pbcopy'], input='\n'.join(lines) + '\n', text=True, check=True)
print('Настройки скопированы в буфер обмена. Значения не выведены на экран.')
print('В Render: ваш сервис → Environment → Add from .env → вставить → сохранить и развернуть.')
print('В буфере находятся секреты. Вставляйте их только в Environment вашего сервиса Render.')
PY
read -r "?Нажмите Enter, чтобы закрыть окно…"
