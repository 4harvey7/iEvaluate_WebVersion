FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Unprivileged runtime user (no shell login, no home write needed).
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

# Code is owned by root and only readable by the app user: a compromised
# process can't rewrite the application.
COPY app ./app

USER 10001:10001

# Platforms like Railway/Render inject $PORT; default to 8000 for local docker run.
ENV PORT=8000
# Proxies whose X-Forwarded-For / X-Forwarded-Proto uvicorn trusts. Only used
# for the request scheme/host now: rate limiting reads the client IP itself
# (app/security.py client_ip), and only when TRUST_PROXY_HEADERS=true.
#   local docker run: FORWARDED_ALLOW_IPS=127.0.0.1, TRUST_PROXY_HEADERS=false
#   Render (behind its proxy + Cloudflare): FORWARDED_ALLOW_IPS=*, TRUST_PROXY_HEADERS=true
ENV FORWARDED_ALLOW_IPS=127.0.0.1 \
    TRUST_PROXY_HEADERS=false
EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS}\""]
