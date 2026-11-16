#!/bin/zsh
set -e
cd -- "${0:A:h}"
print "Настройка личного Telegram-бота HSK"
print "Токен будет запрошен скрыто и сохранён только в локальном .env."
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python configure.py
print "Настройка завершена. Файл .env не отправляйте в чат и не загружайте на GitHub."
read -r "?Нажмите Enter, чтобы закрыть окно…"
