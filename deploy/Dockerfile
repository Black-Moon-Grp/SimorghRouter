FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    XRAY_BIN=/usr/local/bin/xray \
    XRAY_ASSETS_DIR=/usr/local/share/xray \
    DATA_DIR=/var/lib/v2ray-router

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Xray-core (Linux x64)
RUN curl -fsSL -o /tmp/xray.zip \
    "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip" \
    && unzip -o /tmp/xray.zip xray -d /usr/local/bin/ \
    && chmod +x /usr/local/bin/xray \
    && rm -f /tmp/xray.zip

# Download GeoIP & Geosite
RUN mkdir -p /usr/local/share/xray /var/lib/v2ray-router \
    && curl -fsSL -o /usr/local/share/xray/geoip.dat \
       "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat" \
    && curl -fsSL -o /usr/local/share/xray/geosite.dat \
       "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat"

WORKDIR /opt/v2ray-router
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000 443 8443 2083

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
