import logging
import os
from time import sleep

import coloredlogs
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
coloredlogs.install(level=log_level, logger=logging.getLogger())

from e_kakushin_client import EKakushinClient


def main():
    with EKakushinClient(headless=False) as client:
        client.login()
        try:
            client.click_secom_service()
            client.search_and_process_eq_data()
            # sleep(10)
        except Exception:
            client.logout()
            raise
        client.logout()


if __name__ == "__main__":
    main()
