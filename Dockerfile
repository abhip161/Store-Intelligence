# syntax=docker/dockerfile:1.7

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV YOLO_CONFIG_DIR=/tmp/ultralytics

WORKDIR /app

FROM base AS api
COPY requirements-api.txt requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt
COPY app/ app/
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS dashboard
COPY requirements-dashboard.txt requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt
COPY dashboard/ dashboard/
EXPOSE 8501
CMD ["streamlit", "run", "dashboard/streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]

FROM base AS pipeline
ARG INSTALL_OPENCV_LIBS=1
RUN if [ "$INSTALL_OPENCV_LIBS" = "1" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends \
            libglib2.0-0 \
            libgl1 \
            libxcb1 \
        && rm -rf /var/lib/apt/lists/*; \
    fi
COPY requirements-pipeline.txt requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt
COPY pipeline/ pipeline/
COPY app/ app/
COPY scripts/ scripts/
CMD ["sleep", "infinity"]

FROM base AS runtime
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
COPY requirements*.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r "$REQUIREMENTS_FILE"
COPY app/ app/
COPY dashboard/ dashboard/
COPY pipeline/ pipeline/
COPY scripts/ scripts/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
