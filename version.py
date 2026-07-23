#!/usr/bin/env python3
# version.py - Versione unificata per tutto il progetto

APP_VERSION = "1.1.1"
APP_NAME = "RNS Manager"
APP_AUTHOR = "argo79"
APP_EMAIL = "arg0netds@gmail.com"
APP_URL = "https://github.com/argo79/RNS-Manager"
APP_LICENSE = "MIT"
APP_DESCRIPTION = "RNS Identity Manager & Aspect Monitor"

def get_version():
    return APP_VERSION

def get_full_name():
    return f"{APP_NAME} v{APP_VERSION}"

if __name__ == "__main__":
    print(APP_VERSION)