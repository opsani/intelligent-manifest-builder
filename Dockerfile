FROM python:alpine

LABEL maintainer="support@opsani.com"
LABEL version="0.1.0"

ENV CO_TOKEN='' CO_DOMAIN='' CO_APP=''

WORKDIR /work
COPY . .
RUN apk add --no-cache curl && curl -sLo /usr/local/bin/aws-iam-authenticator https://amazon-eks.s3-us-west-2.amazonaws.com/1.15.10/2020-02-22/bin/linux/amd64/aws-iam-authenticator \
    && chmod +x /usr/local/bin/aws-iam-authenticator \
    && curl -sSL -o /usr/local/bin/kubectl https://storage.googleapis.com/kubernetes-release/release/$(curl -s https://storage.googleapis.com/kubernetes-release/release/stable.txt)/bin/linux/amd64/kubectl \
    && chmod +x /usr/local/bin/kubectl \
    && pip install --no-cache-dir -U .
ENTRYPOINT ["imb"]
