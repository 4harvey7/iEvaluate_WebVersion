FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Platforms like Railway/Render inject $PORT; default to 8000 for local docker run.
ENV PORT=8000
# Proxies whose X-Forwarded-For uvicorn trusts (so rate limiting sees the real
# client IP). Set to your platform's proxy IP/CIDR list, or "*" only when the
# container is reachable exclusively through that proxy.
ENV FORWARDED_ALLOW_IPS=127.0.0.1
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS}\""]
