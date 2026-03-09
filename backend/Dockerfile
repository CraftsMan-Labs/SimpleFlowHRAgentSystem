FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /workspace/backend

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY SimpleFlowSDKs/python /deps/SimpleFlowSDKs/python
COPY SimpleAgents /deps/SimpleAgents

RUN pip install --no-cache-dir fastapi uvicorn PyJWT \
    && pip install --no-cache-dir -e /deps/SimpleFlowSDKs/python \
    && pip install --no-cache-dir -e /deps/SimpleAgents/crates/simple-agents-py

COPY SimpleFlowTestTempaltes/SimpleFlowHRAgentSystem/backend /workspace/backend

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8091"]
