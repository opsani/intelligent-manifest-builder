# Invoking imb.py directly prevents importing of modules with 'imb.' package name prefix
# This entrypoint serves as a workaround
from imb.imb_main import imb

if __name__ == "__main__":
    imb()