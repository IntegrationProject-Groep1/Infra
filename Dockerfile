FROM python:3.9-slim

# Zorg dat logs direct zichtbaar zijn in Docker/Dozzle
ENV PYTHONUNBUFFERED=1

COPY demo.py .

CMD ["python", "demo.py"]

LABEL org.opencontainers.image.source="https://github.com/IntegrationProject-Groep1/infra"
