# syntax=docker/dockerfile:1.7

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV YOLO_CONFIG_DIR=/tmp/ultralytics

WORKDIR /app

ARG INSTALL_OPENCV_LIBS=1
RUN if [ "$INSTALL_OPENCV_LIBS" = "1" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends \
            libglib2.0-0 \
            libgl1 \
            libxcb1 \
        && rm -rf /var/lib/apt/lists/*; \
    fi

ARG REQUIREMENTS_FILE=requirements.txt
COPY ${REQUIREMENTS_FILE} requirements.txt
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
