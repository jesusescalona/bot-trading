FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY entry_and_manage.py .
COPY config_binance.json .

CMD ["python", "entry_and_manage.py"]
