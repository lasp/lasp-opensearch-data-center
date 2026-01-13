#!/bin/sh
# This script is used inside our Lambda containers to determine whether to run the Runtime Interface Emulator (RIE) for
# testing
if [ -z "${AWS_LAMBDA_RUNTIME_API}" ]; then
  exec /usr/local/bin/aws-lambda-rie python -m awslambdaric $@
else
  exec python -m awslambdaric $@
fi
