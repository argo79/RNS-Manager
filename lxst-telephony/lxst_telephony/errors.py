"""Errors raised by lxst_telephony."""


class TelephonyError(Exception):
    pass


class NoIdentityError(TelephonyError):
    pass


class CallBusyError(TelephonyError):
    pass


class CallNotFoundError(TelephonyError):
    pass
