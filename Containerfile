# PyPI Actions Runtime 1.0.2 has Linux x86-64 wheels only.
FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7 AS base

ARG TARGETARCH
RUN test "$TARGETARCH" = amd64 \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl openssh-client procps \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip download --no-deps --only-binary=:all: --dest /tmp/runtime-wheel actions-runtime==1.0.2 \
    && echo '9aef9ec55c17d90ec11a32d8d4da694def92502ba46a73621c35267f230873d3  /tmp/runtime-wheel/actions_runtime-1.0.2-cp312-cp312-manylinux_2_17_x86_64.manylinux_2_5_x86_64.manylinux1_x86_64.manylinux2014_x86_64.whl' | sha256sum -c - \
    && python -m pip install --no-cache-dir /tmp/runtime-wheel/*.whl actions-core==1.0.1 mcp==2.0.0 \
    && rm -rf /tmp/runtime-wheel \
    && curl -fL --max-time 120 --retry 2 https://github.com/devsy-org/devsy/releases/download/v1.19.0/devsy-linux-amd64 -o /usr/local/bin/devsy \
    && echo '2f43f28ab5b399b379091aeb09628ec6b70dc82212a64d30de8e2a4a18a49ef5  /usr/local/bin/devsy' | sha256sum -c - \
    && chmod 0755 /usr/local/bin/devsy \
    && groupadd --gid 1000 codex-actions \
    && useradd --uid 1000 --gid 1000 --create-home codex-actions \
    && mkdir -p /var/lib/codex-action-server/actions /var/lib/codex-action-server/receipts /var/lib/codex-action-server/runtime /var/lib/codex-action-server/launcher /var/lib/codex-action-server/robots /run/operator-devsy/contexts/default \
    && ln -s /var/lib/codex-action-server/launcher /home/codex-actions/.actions \
    && chown -R 1000:1000 /var/lib/codex-action-server /run/operator-devsy \
    && chmod 0700 /var/lib/codex-action-server /var/lib/codex-action-server/actions /var/lib/codex-action-server/receipts /var/lib/codex-action-server/runtime /var/lib/codex-action-server/launcher /var/lib/codex-action-server/robots

ENV CODEX_ACTION_TARGETS=/run/codex-action-server/targets.json \
    CODEX_ACTION_RECEIPTS=/var/lib/codex-action-server/receipts \
    CODEX_ACTION_DATA=/var/lib/codex-action-server/runtime \
    ACTIONS_HOME=/var/lib/codex-action-server/actions \
    CODEX_ACTION_PREPARED_CACHE=/opt/codex-action-server-cache \
    ROBOTS_HOME=/var/lib/codex-action-server/robots \
    DEVSY_HOME=/run/operator-devsy \
    DEVSY_DISABLE_TELEMETRY=true \
    CODEX_ACTION_PORT=8088 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /opt/codex-action-server
COPY package.yaml ./
COPY src/ ./src/
COPY scripts/run-container.sh scripts/preflight.py scripts/container_health.py ./scripts/
COPY scripts/install_runtime_transport_patch.py ./scripts/
RUN python3 scripts/install_runtime_transport_patch.py
COPY scripts/install_runtime_annotation_patch.py ./scripts/
RUN python3 scripts/install_runtime_annotation_patch.py

USER 1000:1000
FROM base AS prepared
# Warm the exact package environment at build time; no daemon is started.
RUN action-server import --dir /opt/codex-action-server --datadir /tmp/cas-image-import \
    && rm -rf /tmp/cas-image-import
RUN rm -rf "$ACTIONS_HOME/pkgs" "$ACTIONS_HOME/uvcache" "$ACTIONS_HOME/temp"

FROM base
COPY --from=prepared --chown=1000:1000 /var/lib/codex-action-server/actions/ /opt/codex-action-server-cache/
COPY --from=prepared --chown=1000:1000 /var/lib/codex-action-server/launcher/ /var/lib/codex-action-server/launcher/
ENV SEMA4AI_OPTIMIZE_FOR_CONTAINER=1

HEALTHCHECK --interval=30s --timeout=20s --start-period=120s --retries=3 \
    CMD ["python3", "/opt/codex-action-server/scripts/container_health.py"]
ENTRYPOINT ["bash", "/opt/codex-action-server/scripts/run-container.sh"]
