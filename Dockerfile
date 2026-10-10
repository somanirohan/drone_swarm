FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 HOST=0.0.0.0 PORT=8787
WORKDIR /app
COPY requirements/requirements-deploy.txt requirements/requirements-deploy.txt
RUN pip install -r requirements/requirements-deploy.txt
COPY swarm_ops ./swarm_ops
COPY web ./web
COPY assets/operations ./assets/operations
COPY examples ./examples
RUN useradd --create-home --uid 10001 swarm && mkdir -p /app/.cache/operations && chown -R swarm:swarm /app
USER swarm
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8787'), timeout=4).status==200 else 1)"
CMD ["python", "-m", "swarm_ops.cli"]
