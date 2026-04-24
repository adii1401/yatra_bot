import logging
import sys

def setup_logger(name: str) -> logging.Logger:
    """Creates a production-grade logger."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)

        file_handler = logging.FileHandler("bot_production.log", encoding="utf-8")
        file_handler.setLevel(logging.ERROR) 

        formatter = logging.Formatter(
            '%(asctime)s - [%(levelname)s] - %(name)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        console_handler.setFormatter(formatter)
        file_handler.setFormatter(formatter)

        logger.addHandler(console_handler)
        logger.addHandler(file_handler)

    return logger
