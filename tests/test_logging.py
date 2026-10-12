import logging

from utils.logging_setup import RedactFilter

TOKEN = "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def test_token_is_redacted_in_messages_args_and_tracebacks(capsys):
    logger = logging.getLogger("redact-test")
    logger.handlers.clear()
    handler = logging.StreamHandler()
    handler.addFilter(RedactFilter([TOKEN]))
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    logger.error("url https://api.telegram.org/bot%s/getMe failed", TOKEN)
    try:
        raise RuntimeError(f"boom {TOKEN}")
    except RuntimeError:
        logger.exception("oops")
    err = capsys.readouterr().err
    assert TOKEN not in err and "***" in err
