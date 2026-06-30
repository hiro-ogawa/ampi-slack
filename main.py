from time import sleep

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
