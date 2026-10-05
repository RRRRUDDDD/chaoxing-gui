from requests import RequestException


class LoginError(Exception):
    pass


class InputFormatError(Exception):
    pass


class MaxRetryExceeded(Exception):
    pass


class CaptchaNotPassed(RequestException):
    """A read-only request ended on the verification page and it was not passed."""

    def __init__(self, msg="验证码未通过，请在浏览器中手动完成验证后重试"):
        super().__init__(msg)


class FontDecodeError(Exception):
    pass
