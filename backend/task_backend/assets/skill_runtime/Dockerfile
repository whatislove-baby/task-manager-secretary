ARG PYTHON_BASE=python@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
FROM ${PYTHON_BASE}
COPY requirements.txt /opt/task-manager-requirements.txt
RUN python -m pip install --no-cache-dir --disable-pip-version-check -r /opt/task-manager-requirements.txt
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
USER 65534:65534
WORKDIR /tmp
ENTRYPOINT ["python", "-I", "-B", "/work/runner.py"]
