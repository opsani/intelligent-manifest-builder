#-------------------------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See https://go.microsoft.com/fwlink/?linkid=2090316 for license information.
#-------------------------------------------------------------------------------------------------------------

# You can use any Debian/Ubuntu based image as a base
FROM debian:10

# Avoid warnings by switching to noninteractive
ENV DEBIAN_FRONTEND=noninteractive

# This Dockerfile adds a non-root user with sudo access. Use the "remoteUser"
# property in devcontainer.json to use it. On Linux, the container user's GID/UIDs
# will be updated to match your local UID/GID (when using the dockerFile property).
# See https://aka.ms/vscode-remote/containers/non-root-user for details.
ARG USERNAME=vscode
ARG USER_UID=1000
ARG USER_GID=$USER_UID

# Configure apt and install packages
RUN apt-get update \
    && apt-get -y install --no-install-recommends apt-utils dialog 2>&1 \
    #
    # Verify git, process tools installed
    && apt-get -y install git openssh-client iproute2 procps \
    #
    # Install Docker CE CLI
    && apt-get install -y apt-transport-https ca-certificates curl gnupg-agent software-properties-common lsb-release \
    && curl -fsSL https://download.docker.com/linux/$(lsb_release -is | tr '[:upper:]' '[:lower:]')/gpg | (OUT=$(apt-key add - 2>&1) || echo $OUT) \
    && add-apt-repository "deb [arch=amd64] https://download.docker.com/linux/$(lsb_release -is | tr '[:upper:]' '[:lower:]') $(lsb_release -cs) stable" \
    && apt-get update \
    && apt-get install -y docker-ce-cli
    #
    # Install kubectl
RUN curl -sSL -o /usr/local/bin/kubectl https://storage.googleapis.com/kubernetes-release/release/$(curl -s https://storage.googleapis.com/kubernetes-release/release/stable.txt)/bin/linux/amd64/kubectl \
    && chmod +x /usr/local/bin/kubectl \
    #
    # Install Helm
    && curl -s https://raw.githubusercontent.com/helm/helm/master/scripts/get-helm-3 | bash - \
    #
    # Copy localhost's ~/.kube/config file into the container and swap out localhost
    # for host.docker.internal whenever a new shell starts to keep them in sync.
    && echo '\n\
        if [ "$SYNC_LOCALHOST_KUBECONFIG" == "true" ]; then\n\
            mkdir -p $HOME/.kube\n\
            cp -r $HOME/.kube-localhost/* $HOME/.kube\n\
            sed -i -e "s/localhost/host.docker.internal/g" $HOME/.kube/config\n\
        \n\
            if [ -d "$HOME/.minikube-localhost" ]; then\n\
                mkdir -p $HOME/.minikube\n\
                cp -r $HOME/.minikube-localhost/ca.crt $HOME/.minikube\n\
                sed -i -r "s|(\s*certificate-authority:\s).*|\\1$HOME\/.minikube\/ca.crt|g" $HOME/.kube/config\n\
                cp -r $HOME/.minikube-localhost/client.crt $HOME/.minikube\n\
                sed -i -r "s|(\s*client-certificate:\s).*|\\1$HOME\/.minikube\/client.crt|g" $HOME/.kube/config\n\
                cp -r $HOME/.minikube-localhost/client.key $HOME/.minikube\n\
                sed -i -r "s|(\s*client-key:\s).*|\\1$HOME\/.minikube\/client.key|g" $HOME/.kube/config\n\
            fi\n\
        fi' \
        >> $HOME/.bashrc \
    #
    # Create a non-root user to use if preferred - see https://aka.ms/vscode-remote/containers/non-root-user.
    && groupadd --gid $USER_GID $USERNAME \
    && useradd -s /bin/bash --uid $USER_UID --gid $USER_GID -m $USERNAME \
    # [Optional] Add sudo support for the non-root user
    && apt-get install -y sudo \
    && echo $USERNAME ALL=\(root\) NOPASSWD:ALL > /etc/sudoers.d/$USERNAME\
    && chmod 0440 /etc/sudoers.d/$USERNAME \
    # Clean up
    && apt-get autoremove -y \
    && apt-get clean -y \
    && rm -rf /var/lib/apt/lists/*

#     # Add python
# RUN apt-get update && curl -LO https://www.python.org/ftp/python/3.8.2/Python-3.8.2.tgz \
#     && apt-get purge python3-dev python3 -y && apt-get autoremove -y \
#     && apt-get install build-essential checkinstall libreadline-gplv2-dev libncursesw5-dev libssl-dev \
#         libsqlite3-dev tk-dev libgdbm-dev libc6-dev libbz2-dev libffi-dev zlib1g-dev -y \
#     && tar xfz Python-3.8.2.tgz && cd Python-3.8.2 && ./configure --enable-optimizations && make install \
#     && cd .. && rm -rf Python-3.* && update-alternatives --install /usr/bin/python python /usr/local/bin/python3 1 \
#     && update-alternatives --install /usr/bin/python3 python3 /usr/local/bin/python3 1 \
#     && python -m ensurepip --default-pip \
#     && python3 -m pip install -U pip \
#     && python3 -m pip install -U setuptools wheel pylint autopep8 \
#     #
#     # Clean up
#     && apt-get autoremove -y \
#     && apt-get clean -y \
#     && rm -rf /var/lib/apt/lists/*
RUN apt-get update && apt-get install -y python3-dev python3-pip \
    && python3 -m pip install -U pip setuptools wheel pylint autopep8 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3 1 \
    # Clean up
    && apt-get autoremove -y \
    && apt-get clean -y \
    && rm -rf /var/lib/apt/lists/*

# Cloud management: AWS, GCP, DO
RUN python3 -m pip install awscli \
    && curl -sLo /usr/local/bin/aws-iam-authenticator https://amazon-eks.s3-us-west-2.amazonaws.com/1.15.10/2020-02-22/bin/linux/amd64/aws-iam-authenticator \
    && chmod +x /usr/local/bin/aws-iam-authenticator \
    && curl -sLo - "https://github.com/weaveworks/eksctl/releases/latest/download/eksctl_$(uname -s)_amd64.tar.gz" | tar xz -C /usr/local/bin/ \
    && chmod +x /usr/local/bin/eksctl
    # Digital Ocean
RUN curl -sLo - https://github.com/digitalocean/doctl/releases/download/v1.38.0/doctl-1.38.0-linux-386.tar.gz | tar xz -C /usr/local/bin/ 
    # Login with DO: doctl auth init

    # Google
# RUN echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] http://packages.cloud.google.com/apt cloud-sdk main" | tee -a /etc/apt/sources.list.d/google-cloud-sdk.list \
#     && curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | apt-key --keyring /usr/share/keyrings/cloud.google.gpg  add - \
#     && apt-get update -y && apt-get install google-cloud-sdk -y \
#     # Login manually: gcloud init 
#     # Clean up
#     && apt-get autoremove -y \
#     && apt-get clean -y \
#     && rm -rf /var/lib/apt/lists/*

# Switch back to dialog for any ad-hoc use of apt-get
ENV DEBIAN_FRONTEND=dialog
