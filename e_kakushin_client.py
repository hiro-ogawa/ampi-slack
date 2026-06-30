import csv
import logging
import os
from datetime import datetime

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

_START_URL = "https://www.e-kakushin.com/login/"
# _STATE_FILE = os.path.join("tmp", "session_state.json")

_company_id = os.getenv("COMPANY_ID")
_ids = os.getenv("IDS", "").split(",")
_passwords = os.getenv("PASSWORDS", "").split(",")

eq_title = "甲信越地域　震度６弱　関東地域　震度５弱　東海地域　震度５弱"

logger = logging.getLogger(__name__)


def _save_html(page: Page, label: str) -> str:
    os.makedirs("tmp", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join("tmp", f"{ts}_{label}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page.content())
    return path


def _credentials(index: int = 0) -> tuple[str, str, str]:
    if not _company_id:
        raise ValueError("COMPANY_ID is not set")
    if not _ids or not _passwords:
        raise ValueError("IDS or PASSWORDS is not set")
    if len(_ids) != len(_passwords):
        raise ValueError("IDS and PASSWORDS counts do not match")
    if index < 0 or index >= len(_ids):
        raise IndexError(f"Credential index {index} is out of range")
    return _company_id, _ids[index].strip(), _passwords[index].strip()


class EKakushinClient:
    def __init__(self, index: int = 0, headless: bool = True) -> None:
        self._company, self._user_id, self._password = _credentials(index)
        self._headless = headless
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self.logger = logging.getLogger(__name__)

    def _save_html_debug(self, label: str) -> None:
        if self.logger.isEnabledFor(logging.DEBUG):
            path = _save_html(self._page, label)
            self.logger.debug(path)

    def __enter__(self) -> "EKakushinClient":
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self._headless)
        return self

    def __exit__(self, *args) -> None:
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def _is_logged_in(self) -> bool:
        """セッションが有効かどうかをトップページ遷移で確認する。"""
        try:
            self._page.goto(_START_URL, wait_until="domcontentloaded")
            self._page.get_by_role("heading", name="ご利用可能サービス").wait_for(
                timeout=5_000
            )
            return True
        except PlaywrightTimeoutError:
            return False

    def login(self) -> None:
        """ログイン。保存済みセッションがあれば再利用する。"""
        # state_file = _STATE_FILE if os.path.exists(_STATE_FILE) else None
        # self._context = self._browser.new_context(storage_state=state_file)
        self._context = self._browser.new_context()
        self._page = self._context.new_page()
        self._page.set_default_timeout(10_000)
        self._page.on("dialog", lambda dialog: dialog.accept())

        # if state_file and self._is_logged_in():
        #     self.logger.info(_save_html(self._page, "already_logged_in"))
        #     return

        try:
            self._page.goto(_START_URL, wait_until="domcontentloaded")
            self._page.get_by_role("button", name="ログインページ").click()
            self._page.get_by_role("textbox", name="企業コードを入力してください").fill(
                self._company
            )
            self._page.get_by_role("textbox", name="ユーザーIDを入力してください").fill(
                self._user_id
            )
            self._page.get_by_role("textbox", name="パスワードを入力してください").fill(
                self._password
            )
            self._page.get_by_role("button", name="ログインする", exact=True).click()
            self._page.get_by_role("heading", name="ご利用可能サービス").wait_for(
                timeout=5_000
            )
        except PlaywrightTimeoutError:
            self._save_html_debug("login_timeout")
            raise

        os.makedirs("tmp", exist_ok=True)
        # self._context.storage_state(path=_STATE_FILE)
        self._save_html_debug("after_login")

    def click_secom_service(self) -> None:
        """セコム安否確認サービスをクリック"""
        page = self._page
        try:
            # まず「ご利用可能サービス」ボタンをクリックしてメニューを開く
            menu_button = page.locator("#account-srvclist")
            menu_button.wait_for(state="visible", timeout=5_000)
            menu_button.click()

            # セコム安否確認サービスのリンクをクリック
            # PC版のリンク(service-list0)を優先し、見つからなければスマートフォン版(service-listsp0)を試す
            try:
                link = page.locator("#service-list0")
                link.wait_for(state="visible", timeout=5_000)
            except PlaywrightTimeoutError:
                link = page.locator("#service-listsp0")
                link.wait_for(state="visible", timeout=5_000)
            link.click()

            # サービス遷移直後に多重ログイン警告が出る場合があるため、
            # 切断ボタンがあればここで先に処理しておく。
            page.wait_for_load_state("domcontentloaded", timeout=10_000)
            self._disconnect_if_present()
            self._save_html_debug("after_click_secom_service")
        except PlaywrightTimeoutError:
            self._save_html_debug("secom_service_not_found")
            raise

    def _disconnect_if_present(self) -> None:
        """管理者アクセス一覧に切断リンクがあれば押す。"""
        page = self._page

        # 画面によってaタグ/ボタンのどちらで出る場合もあるため両方見る。
        disconnect_button = page.locator(
            "a:has-text('切断'), button:has-text('切断')"
        ).first

        if disconnect_button.count() == 0:
            return

        disconnect_button.wait_for(state="visible", timeout=5_000)
        disconnect_button.click()
        page.wait_for_load_state("networkidle", timeout=10_000)
        page.wait_for_timeout(1_000)
        self._save_html_debug("after_disconnect_click")

    def search_and_process_eq_data(self) -> None:
        """eq_titleで検索して安否状況集計ボタンを押し、対象者数データを取得してCSVに保存"""
        page = self._page
        try:
            # 管理者アクセス一覧で接続情報が残っている場合は切断する
            self._disconnect_if_present()

            # eq_titleを含む災害名リンクを検索
            row = page.locator(f"a:has-text('{eq_title}')").first
            row.wait_for(state="visible", timeout=5_000)
            self._save_html_debug("after_find_eq_title")

            # その行から「安否状況集計」リンク/ボタンを探してクリック
            parent_row = row.locator("xpath=ancestor::tr")
            status_button = parent_row.locator(
                "a:has-text('安否状況集計'), button:has-text('安否状況集計')"
            ).first
            status_button.wait_for(state="visible", timeout=5_000)
            status_button.click()
            page.wait_for_load_state("domcontentloaded", timeout=10_000)
            self._save_html_debug("after_click_status_button")

            # 「対象者数」カード内の人数リンクをクリック
            target_count_card = page.locator("div.mdl-common-block.small").filter(
                has=page.locator("div.status-summary-heading", has_text="対象者数")
            )
            target_count_button = target_count_card.locator("div.status-value a").first
            target_count_button.wait_for(state="visible", timeout=5_000)
            target_count_button.click()
            self._save_html_debug("after_click_target_count")

            # テーブルデータを取得
            page.wait_for_load_state("networkidle", timeout=10_000)
            page.wait_for_timeout(1_000)  # 追加の読み込み待機

            # テーブルのすべての行を取得
            rows = page.locator("table tbody tr")
            row_count = rows.count()

            if row_count == 0:
                # テーブルが見つからない場合は別の構造を試す
                rows = page.locator("tr")
                row_count = rows.count()

            # データを抽出
            table_data = []
            for i in range(row_count):
                cells = rows.nth(i).locator("td, th")
                cell_count = cells.count()
                row_data = []
                for j in range(cell_count):
                    cell_text = cells.nth(j).text_content().strip()
                    row_data.append(cell_text)
                if row_data:
                    table_data.append(row_data)

            # CSVファイルに保存
            os.makedirs("tmp", exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_path = os.path.join("tmp", f"{ts}_secom_data.csv")

            if table_data:
                with open(csv_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerows(table_data)
                self.logger.info("CSV saved: %s", csv_path)
            else:
                self.logger.warning("No table data found")

            self._save_html_debug("after_extract_data")

        except PlaywrightTimeoutError:
            self._save_html_debug("search_and_process_timeout")
            raise

    def logout(self) -> None:
        """ログアウト。成功後にセッション保存ファイルを削除する。"""
        page = self._page
        try:
            # 画面レイアウト差分に対応: 表示中のメニューボタン/ログアウト要素を優先する
            menu_button = page.locator(
                "#mgr-menu-change-pc:visible, "
                "#mgr-menu-change-sp:visible, "
                "#account-mgr:visible, "
                "#account-mgr1:visible, "
                "#account-mgr2:visible"
            ).first

            logout_locator = page.locator(
                "#account-logout:visible, "
                "a#logout-btn1:visible, "
                "button#logout-btn1:visible, "
                "a:has-text('ログアウト'):visible, "
                "button:has-text('ログアウト'):visible"
            ).first

            # すでにログアウト要素が見えていなければ、先にアカウントメニューを開く
            if logout_locator.count() == 0:
                menu_button.wait_for(state="visible", timeout=10_000)
                menu_button.scroll_into_view_if_needed()
                try:
                    menu_button.click(timeout=5_000)
                except PlaywrightTimeoutError:
                    menu_button.click(force=True, timeout=5_000)

                logout_locator.wait_for(state="visible", timeout=5_000)

            self._save_html_debug("after_menu_open")
            logout_locator.click()
            self._save_html_debug("after_logout_click")
            page.locator("text=ログアウトしました").first.wait_for(timeout=10_000)
            self._save_html_debug("logout_success")
        except PlaywrightTimeoutError:
            self._save_html_debug("logout_timeout")
            raise

        # if os.path.exists(_STATE_FILE):
        #     os.remove(_STATE_FILE)
