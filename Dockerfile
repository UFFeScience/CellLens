FROM apache/airflow:2.7.2-python3.10

USER root
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY requirements.txt /requirements.txt

RUN uv pip install --system -r /requirements.txt
USER airflow