FROM python:alpine

LABEL maintainer="support@opsani.com"
LABEL version="0.1.0"

ENV CO_TOKEN='' CO_DOMAIN='' CO_APP=''

WORKDIR /work
# Make imb project dir so setup.py won't error during dependency install
RUN mkdir imb

# Install Dependencies
COPY ./setup.py .
RUN apk add --no-cache curl && curl -sLo /usr/local/bin/aws-iam-authenticator https://amazon-eks.s3-us-west-2.amazonaws.com/1.15.10/2020-02-22/bin/linux/amd64/aws-iam-authenticator \
    && chmod +x /usr/local/bin/aws-iam-authenticator \
    && curl -sSL -o /usr/local/bin/kubectl https://storage.googleapis.com/kubernetes-release/release/$(curl -s https://storage.googleapis.com/kubernetes-release/release/stable.txt)/bin/linux/amd64/kubectl \
    && chmod +x /usr/local/bin/kubectl \
    # Only install setup.py's install_requires
    && pip install --no-cache-dir -e .

# Downloading gcloud package
RUN curl https://dl.google.com/dl/cloudsdk/release/google-cloud-sdk.tar.gz > /tmp/google-cloud-sdk.tar.gz \
    && mkdir -p /usr/local/gcloud \
    && tar -C /usr/local/gcloud -xvf /tmp/google-cloud-sdk.tar.gz \
    && /usr/local/gcloud/google-cloud-sdk/install.sh

# Adding the package path to local
ENV PATH $PATH:/usr/local/gcloud/google-cloud-sdk/bin
# Install IMB last for faster code iteration
COPY . .
RUN pip install --no-cache-dir -U .
ENTRYPOINT ["imb"]
