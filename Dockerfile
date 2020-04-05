FROM python:3.8-slim

LABEL maintainer="support@opsani.com"
LABEL version="0.1.0"

ENV CO_TOKEN='' \
    CO_DOMAIN='' \
    CO_APP='' \
    PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100 \
    POETRY_VERSION=1.0.2

WORKDIR /app
COPY . /app/
RUN chmod +x ./imb/imb.py

RUN apt-get update \
  && apt-get install -y curl apt-transport-https gnupg2 \
  && curl -s https://packages.cloud.google.com/apt/doc/apt-key.gpg | apt-key add - \
  && echo "deb https://apt.kubernetes.io/ kubernetes-xenial main" | tee -a /etc/apt/sources.list.d/kubernetes.list \
  && apt-get update \
  && apt-get install -y kubectl \
  && curl -sLo /usr/local/bin/aws-iam-authenticator https://amazon-eks.s3-us-west-2.amazonaws.com/1.15.10/2020-02-22/bin/linux/amd64/aws-iam-authenticator \
  && chmod +x /usr/local/bin/aws-iam-authenticator

# Install dependencies with Poetry
RUN pip install "poetry==$POETRY_VERSION"
COPY poetry.lock pyproject.toml /app/
RUN poetry config virtualenvs.create false \
  && poetry install --no-interaction --no-ansi

ENTRYPOINT [ "poetry", "run", "./run_imb_noinstall.py" ]
