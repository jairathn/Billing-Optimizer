FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 REMITWATCH_DB=/data/remitwatch.sqlite REMITWATCH_DATA_DIR=/data/dropbox
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY remitwatch ./remitwatch
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir -p /data
VOLUME ["/data"]
EXPOSE 8000
# the commit this image is built from (docker-compose.yml passes it), which /api/health shows. Last, so that a new
# commit rebuilds nothing above it
ARG REMITWATCH_COMMIT=
ENV REMITWATCH_COMMIT=$REMITWATCH_COMMIT
ENTRYPOINT ["/entrypoint.sh"]
CMD ["serve"]
