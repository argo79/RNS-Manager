#!/usr/bin/env python3
# version.py - Versione unificata per RNS Messenger

APP_VERSION = "1.0.0"
APP_NAME = "RNS Messenger"
APP_AUTHOR = "argo79"
APP_EMAIL = "arg0netds@gmail.com"
APP_URL = "https://github.com/argo79/RNS-Manager"
APP_LICENSE = "MIT"
APP_DESCRIPTION = "LXMF Web Messenger per Reticulum"


def get_version():
    return APP_VERSION


def get_full_name():
    return f"{APP_NAME} v{APP_VERSION}"


if __name__ == "__main__":
    print(APP_VERSION)
