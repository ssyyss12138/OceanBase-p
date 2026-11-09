FROM python:3.12.12-slim
ARG POWERCONTEXT_COMMIT=c4c95e465775b5b87ccac796194e01086bd7cc8f
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
COPY deploy/powercontext-constraints.txt /tmp/constraints.txt
RUN pip install --no-cache-dir -c /tmp/constraints.txt "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@${POWERCONTEXT_COMMIT}"
RUN mkdir /data
EXPOSE 8000
CMD ["powercontext", "server", "run", "--no-env-file", "--host", "0.0.0.0", "--port", "8000"]
