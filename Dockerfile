FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    curl wget gnupg ca-certificates \
    fonts-liberation libatk-bridge2.0-0 libatk1.0-0 \
    libcups2 libdbus-1-3 libdrm2 libgbm1 libgtk-3-0 \
    libnspr4 libnss3 libx11-xcb1 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libxss1 \
    libasound2 libpango-1.0-0 libpangocairo-1.0-0 \
    tor \
    --no-install-recommends && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium && playwright install-deps chromium

COPY server.py .
COPY ui.html .
COPY live.html .

EXPOSE ${PORT:-8000}

CMD ["sh", "-c", "tor --SocksPort 9050 --RunAsDaemon 1 && sleep 3 && uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]