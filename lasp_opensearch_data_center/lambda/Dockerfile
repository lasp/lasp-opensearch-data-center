# Base python image with poetry.lock, custom virtual environment, and the libera_cdk package files.
FROM public.ecr.aws/docker/library/python:3.11-slim AS lambda-base
# Turn off interactive shell to suppress configuration errors
ARG DEBIAN_FRONTEND=noninteractive
ENV LIBERA_WORKDIR=/opt/libera
RUN mkdir -p $LIBERA_WORKDIR
WORKDIR $LIBERA_WORKDIR
# Install
# curl so we can install poetry
# gcc because it's often required for python package installations
RUN apt-get update && apt-get install -y curl gcc
# Install aws-lambda-cpp build dependencies
RUN apt-get update && \
    apt-get install -y \
    g++ \
    make \
    cmake \
    unzip \
    wget \
    libcurl4-openssl-dev
# Install Lambda Runtime Interface Emulator, used to run Lambdas locally, if you can manage to mock out the other
# services needed to test them and get the trigger payload right.
RUN wget -P /usr/local/bin \
    https://github.com/aws/aws-lambda-runtime-interface-emulator/releases/latest/download/aws-lambda-rie
RUN chmod +x /usr/local/bin/aws-lambda-rie
# Create virtual environment and permanently activate it for this image
ENV VIRTUAL_ENV=/opt/venv
RUN python -m venv $VIRTUAL_ENV
# This adds not only the venv python executable but also all installed entrypoints to the PATH
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
# Upgrade pip to the latest version because poetry uses pip in the background to install packages
RUN pip install --upgrade pip
# Install poetry
RUN curl -sSL https://install.python-poetry.org | python -
# Add poetry to path
ENV PATH="$PATH:/root/.local/bin"
# Copy contents of directory
COPY lambda_entrypoint.sh .
COPY pyproject.toml .
COPY opensearch_data_center_lambda_runtime opensearch_data_center_lambda_runtime
# We lock all dependencies early so that it can be cached for other builds since dependencies don't change much
RUN poetry lock
# Install dependencies
RUN poetry install --without=dev
# The entrypoint is a script that automatically figures out if its running in AWS or on a dev machine
ENTRYPOINT [ "./lambda_entrypoint.sh" ]


# Target for OS snapshot lambda
FROM lambda-base AS snapshot-lambda
CMD [ "opensearch_data_center_lambda_runtime.snapshot_handler.handler" ]
