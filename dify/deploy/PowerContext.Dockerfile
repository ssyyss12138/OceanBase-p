FROM python:3.12.12-slim
ARG POWERCONTEXT_COMMIT=c4c95e465775b5b87ccac796194e01086bd7cc8f
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
COPY deploy/powercontext-constraints.txt /tmp/constraints.txt
RUN git init -q /tmp/powercontext \
    && git -C /tmp/powercontext remote add origin https://github.com/oceanbase/powercontext.git \
    && git -c http.version=HTTP/1.1 -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=120 \
       -C /tmp/powercontext fetch --depth=1 origin "${POWERCONTEXT_COMMIT}" \
    && git -C /tmp/powercontext checkout -q --detach FETCH_HEAD \
    && test "$(git -C /tmp/powercontext rev-parse HEAD)" = "${POWERCONTEXT_COMMIT}" \
    && SETUPTOOLS_SCM_PRETEND_VERSION="1.5.0+git.$(printf '%.12s' "$POWERCONTEXT_COMMIT")" \
       pip install --no-cache-dir -c /tmp/constraints.txt '/tmp/powercontext[cli,server]' \
    && rm -rf /tmp/powercontext
RUN mkdir /data
EXPOSE 8000
CMD ["powercontext", "server", "run", "--no-env-file", "--host", "0.0.0.0", "--port", "8000"]
