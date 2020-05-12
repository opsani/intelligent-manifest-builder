FROM python:3-slim

LABEL maintainer="support@opsani.com"
LABEL version="0.1.3"

ENV OPSANI_AUTH_TOKEN='' OPSANI_ACCOUNT_ID='' OPSANI_APPLICATION_ID=''

WORKDIR /work
COPY . .
RUN apt-get update && apt-get install -y curl less && \
   curl -sLo /usr/local/bin/aws-iam-authenticator https://amazon-eks.s3-us-west-2.amazonaws.com/1.15.10/2020-02-22/bin/linux/amd64/aws-iam-authenticator &&\
    chmod +x /usr/local/bin/aws-iam-authenticator &&\
    curl -sSL -o /usr/local/bin/kubectl https://storage.googleapis.com/kubernetes-release/release/$(curl -s https://storage.googleapis.com/kubernetes-release/release/stable.txt)/bin/linux/amd64/kubectl &&\
    chmod +x /usr/local/bin/kubectl &&\
    pip install --no-cache-dir -U . &&\
    apt remove -y --purge build-essential && \
    apt autoremove -y && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["imb"]
