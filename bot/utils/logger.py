import logging
import sys
import os

def setup_logger(name: str) -> logging.Logger:
    """
    Production logger — stdout only (Render has no persistent disk).
    File logging is disabled because container restarts wipe the filesystem.
    Use Sentry for persistent error tracking instead.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter(
            '%(asctime)s - [%(levelname)s] - %(name)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        logger.addHandler(handler)

    return logger