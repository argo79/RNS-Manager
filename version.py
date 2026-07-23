#!/usr/bin/env python3
# version.py - Versione unificata per tutto il progetto

APP_VERSION = "1.1.1"
APP_NAME = "RNS Manager"
APP_AUTHOR = "argo79"
APP_EMAIL = "arg0netds@gmail.com"
APP_URL = "https://github.com/argo79/RNS-Manager"
APP_LICENSE = "MIT"
APP_DESCRIPTION = "RNS Identity Manager & Aspect Monitor"

# Indirizzi per donazioni
DONATION_ADDRESSES = {
    "XRP": "rBKbetm51vuQQfg4Yo8fvweRya7gedcr9J",
    "XMR": "87jacZEtYvXcgnvEp7wu45gLwRBYpvwMr3N9dqhNipPWV69XwQX658tS73VEdghLopG1wA4STEdMPcGF8Tc3e18eJyQ4kMA",
    "ETH": "0xd2d85288df96B4162814Ca7492039620371b9D81"
}

def get_version():
    return APP_VERSION

def get_full_name():
    return f"{APP_NAME} v{APP_VERSION}"

if __name__ == "__main__":
    print(APP_VERSION)