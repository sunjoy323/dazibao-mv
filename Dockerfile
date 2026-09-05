FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DAZIBAO_DATA=/data

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-noto-cjk \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md README.zh-CN.md LICENSE ./
COPY src ./src
COPY examples ./examples

RUN pip install --upgrade pip \
    && pip install '.[web,align]'

EXPOSE 8765
VOLUME ["/data"]

CMD ["dazibao-mv", "serve", "--host", "0.0.0.0", "--port", "8765"]
