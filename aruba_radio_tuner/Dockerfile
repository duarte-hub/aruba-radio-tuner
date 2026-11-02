FROM ghcr.io/home-assistant/base-python:3.14-alpine3.24

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

CMD ["python3", "-m", "aruba_radio_tuner"]
