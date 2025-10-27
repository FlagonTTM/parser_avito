import asyncio
import json
import random
import re
import signal
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from bs4 import BeautifulSoup
from curl_cffi import requests
from lxml import etree
from loguru import logger
from requests.cookies import RequestsCookieJar
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from common_date import HEADERS
from db_service import PostgreSQLDBHandler
from dto import Proxy, AvitoConfig
from get_cookies import USER_AGENTS, get_cookies
from load_config import load_avito_config

logger.add("logs/app.log", rotation="5 MB", retention="5 days", level="DEBUG")


class AvitoParse:
    """Главный класс, отвечающий за парсинг Avito."""

    BATCH_SIZE = 5
    MAX_CONSECUTIVE_429 = 3

    def __init__(self, config: AvitoConfig, stop_event: threading.Event | None = None):
        self.config = config
        self.stop_event = stop_event or threading.Event()
        self.running = True

        self.proxy_obj = self.get_proxy_obj()
        self.db_handler = self._get_db_handler()

        self.session = self._create_session()
        self.headers = HEADERS.copy()
        self._current_user_agent = self._select_user_agent()
        self._update_headers_user_agent(self._current_user_agent)
        self.cookies: dict | None = None

        self.proxy_pool: list[str] = []
        self.current_proxy: str | None = None
        self.proxy_index = 0
        self.consecutive_429 = 0
        self.consecutive_403 = 0

        self.error_count: dict[str, int] = {}
        self.good_request_count = 0
        self.bad_request_count = 0
        self.selenium_driver = None
        self._selenium_lock = threading.RLock()
        self._last_saved_cookies_snapshot: dict[str, str] | None = None
        self._cookies_supports_user_agent = True

        self._initialize_proxy_pool()

        logger.info(f"Запуск с настройками:\n{config}")

    @staticmethod
    def _create_session() -> requests.Session:
        """Создает и настраивает HTTP-сессию."""
        session = requests.Session()
        session.verify = False  # отключаем проверку сертификата по требованию проекта
        return session

    def _get_db_handler(self):
        """Получает обработчик базы данных в зависимости от конфигурации"""
        try:
            if self.config.database_type.lower() == "postgresql":
                try:
                    return PostgreSQLDBHandler(self.config.database_url)
                except Exception as e:
                    logger.error(f"Ошибка при подключении к PostgreSQL: {e}")
                    logger.info("Подключение к БД отключено")
                    return None
        except Exception as e:
            logger.error(f"Ошибка при чтении конфигурации БД: {e}")
            return None

    def get_proxy_obj(self) -> Proxy | None:
        if not self.config.use_proxy:
            if self.config.use_local_ip:
                logger.info("Работаем с локальным IP")
            else:
                logger.info("Работаем без прокси и без смены IP")
            return None

        logger.info("Работаем с прокси")

        candidate_pool: list[str] = []
        if self.config.proxy_string:
            candidate_pool.append(self._sanitize_proxy_string(self.config.proxy_string))

        change_setting = self.config.proxy_change_url
        change_ip_link: str | None = None

        if isinstance(change_setting, list):
            candidate_pool.extend(
                self._sanitize_proxy_string(proxy) for proxy in change_setting if proxy
            )
        elif isinstance(change_setting, str) and change_setting.strip():
            stripped = change_setting.strip()
            if stripped.lower().startswith(("http://", "https://")):
                change_ip_link = stripped
            else:
                candidate_pool.append(self._sanitize_proxy_string(stripped))

        if getattr(self.config, "proxy_pool", None):
            candidate_pool.extend(
                self._sanitize_proxy_string(proxy)
                for proxy in self.config.proxy_pool
                if proxy
            )

        candidate_pool = [
            proxy for proxy in (candidate_pool or []) if proxy
        ]
        candidate_pool = list(dict.fromkeys(candidate_pool))

        if not candidate_pool and not change_ip_link:
            logger.warning("Включены прокси, но список пуст — переходим на локальный IP")
            return None

        primary_proxy = candidate_pool[0] if candidate_pool else None

        rotation_pool = candidate_pool or ([primary_proxy] if primary_proxy else [])
        proxy_kwargs: dict[str, object] = {"proxy_string": primary_proxy}
        if change_ip_link:
            proxy_kwargs["change_ip_link"] = change_ip_link
        if rotation_pool:
            proxy_kwargs["rotation_pool"] = rotation_pool

        try:
            return Proxy(**proxy_kwargs)  # type: ignore[arg-type]
        except TypeError:
            proxy = Proxy(
                proxy_kwargs["proxy_string"],  # type: ignore[arg-type]
                proxy_kwargs.get("change_ip_link"),
            )
            setattr(proxy, "rotation_pool", rotation_pool)
            return proxy

    @staticmethod
    def _sanitize_proxy_string(proxy: str) -> str:
        """Удаляет пробелы и протокол из строки прокси."""
        cleaned = (proxy or "").strip()
        cleaned = cleaned.replace(" ", "")
        if "://" in cleaned:
            cleaned = cleaned.split("://", 1)[1]
        return cleaned

    @staticmethod
    def _format_proxy(proxy: str | None) -> str | None:
        """Добавляет протокол http://, если его нет."""
        if not proxy:
            return None
        if "://" in proxy:
            return proxy
        return f"http://{proxy}"

    def _initialize_proxy_pool(self) -> None:
        """Готовит список прокси и устанавливает активный."""
        if not self.proxy_obj:
            self.proxy_pool = []
            self.current_proxy = None
            return

        raw_pool = getattr(self.proxy_obj, "rotation_pool", []) or []
        proxy_string = getattr(self.proxy_obj, "proxy_string", None)
        if not raw_pool and proxy_string:
            raw_pool = [self.proxy_obj.proxy_string]

        self.proxy_pool = [
            self._sanitize_proxy_string(proxy) for proxy in raw_pool if proxy
        ]
        self.proxy_pool = list(dict.fromkeys(self.proxy_pool))

        if self.proxy_pool:
            self.proxy_index = 0
            self.current_proxy = self.proxy_pool[self.proxy_index]
            if self.proxy_obj:
                setattr(self.proxy_obj, "rotation_pool", self.proxy_pool)
                self.proxy_obj.proxy_string = self.current_proxy
            self.config.proxy_string = self.current_proxy
            self.config.proxy_pool = self.proxy_pool
            self._update_session_proxy()
        else:
            self.current_proxy = None

    def _update_session_proxy(self) -> None:
        """Применяет текущий прокси к сессии."""
        if self.current_proxy:
            formatted = self._format_proxy(self.current_proxy)
            if formatted:
                self.session.proxies = {"http": formatted, "https": formatted}
        else:
            self.session.proxies = {}

    def _build_proxies(self) -> dict[str, str] | None:
        """Возвращает словарь прокси для запроса."""
        formatted = self._format_proxy(self.current_proxy)
        if not formatted:
            return None
        return {"http": formatted, "https": formatted}

    def _select_user_agent(self) -> str:
        """Выбирает user-agent для HTTP-сессии."""
        if USER_AGENTS:
            choice = random.choice(USER_AGENTS).strip()
            if choice:
                return choice
        return HEADERS.get(
            "user-agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )

    def _update_headers_user_agent(self, user_agent: str) -> None:
        """Синхронизирует связанные заголовки с выбранным user-agent."""
        self.headers["user-agent"] = user_agent
        match = re.search(r"Chrome/(\d+)", user_agent)
        chrome_major = match.group(1) if match else "120"
        if "Android" in user_agent:
            platform = '"Android"'
        elif "Mac OS X" in user_agent or "MacOS" in user_agent:
            platform = '"macOS"'
        elif "Linux" in user_agent:
            platform = '"Linux"'
        else:
            platform = '"Windows"'
        self.headers["sec-ch-ua"] = (
            f'"Not:A Brand";v="99", "Chromium";v="{chrome_major}", "Google Chrome";v="{chrome_major}"'
        )
        self.headers["sec-ch-ua-platform"] = platform
        self.headers["sec-ch-ua-mobile"] = "?1" if "Mobile" in user_agent else "?0"

    def _apply_cookies_to_session(self, cookies: dict | None) -> None:
        """Применяет cookies к HTTP-сессии и обновляет внутреннее состояние."""
        try:
            if cookies:
                jar = RequestsCookieJar()
                for key, value in cookies.items():
                    jar.set(key, value)
                self.session.cookies.update(jar)
                self.cookies = cookies
            else:
                self.session.cookies.clear()
                self.cookies = None
        except Exception as exc:
            logger.warning(f"Не удалось применить cookies к сессии: {exc}")

    def _refresh_cookies_and_user_agent(self) -> bool:
        """Обновляет cookies и user-agent с помощью Playwright."""
        switched = False
        if len(self.proxy_pool) > 1:
            switched = self._switch_proxy()
        elif self.proxy_obj and getattr(self.proxy_obj, "rotation_pool", []):
            rotation_pool = [
                self._sanitize_proxy_string(proxy)
                for proxy in getattr(self.proxy_obj, "rotation_pool", [])
                if proxy
            ]
            if rotation_pool and len(rotation_pool) > 1:
                self.config.proxy_pool = rotation_pool
                self.proxy_pool = rotation_pool
                switched = self._switch_proxy()
        if switched:
            logger.info("Сменили прокси перед обновлением cookies после 403")

        new_user_agent = self._select_user_agent()
        self._current_user_agent = new_user_agent
        self._update_headers_user_agent(new_user_agent)
        cookies = self.get_cookies()
        if cookies:
            self.save_cookies()
            logger.info("Обновлены cookies и user-agent после повторных 403")
            return True
        self._apply_cookies_to_session(None)
        logger.warning("Не удалось обновить cookies после 403")
        return False

    def _switch_proxy(self) -> bool:
        """Переключается на следующий прокси из пула."""
        if len(self.proxy_pool) <= 1:
            logger.warning("Недостаточно прокси для переключения")
            return False

        self.proxy_index = (self.proxy_index + 1) % len(self.proxy_pool)
        self.current_proxy = self.proxy_pool[self.proxy_index]

        if self.proxy_obj:
            self.proxy_obj.proxy_string = self.current_proxy
            setattr(self.proxy_obj, "rotation_pool", self.proxy_pool)

        self.config.proxy_string = self.current_proxy
        self.config.proxy_pool = self.proxy_pool
        self._apply_cookies_to_session(None)
        self._last_saved_cookies_snapshot = None
        self.consecutive_429 = 0

        self._update_session_proxy()
        self._current_user_agent = self._select_user_agent()
        self._update_headers_user_agent(self._current_user_agent)

        logger.info(f"Переключились на прокси #{self.proxy_index + 1}: {self.current_proxy}")
        return True

    def _fetch_cookies(self):
        """Получает cookies, учитывая возможное наличие активного event loop."""
        try:
            return asyncio.run(self._get_cookies_async())
        except RuntimeError as exc:
            if "asyncio.run()" in str(exc):
                loop = asyncio.new_event_loop()
                try:
                    try:
                        previous_loop = asyncio.get_event_loop()
                    except RuntimeError:
                        previous_loop = None
                    asyncio.set_event_loop(loop)
                    return loop.run_until_complete(self._get_cookies_async())
                finally:
                    asyncio.set_event_loop(previous_loop)
                    loop.close()
            raise

    async def _get_cookies_async(self):
        """Асинхронно получает cookies, учитывая возможное отсутствие параметра user_agent."""
        if self._cookies_supports_user_agent:
            try:
                result = await get_cookies(
                    proxy=self.proxy_obj,
                    headless=True,
                    user_agent=self._current_user_agent,
                )
            except TypeError as exc:
                if "unexpected keyword argument 'user_agent'" in str(exc):
                    logger.debug("Функция get_cookies не поддерживает параметр user_agent, повторяем без него")
                    self._cookies_supports_user_agent = False
                else:
                    raise
            else:
                return self._normalize_cookies_result(result)

        result = await get_cookies(proxy=self.proxy_obj, headless=True)
        return self._normalize_cookies_result(result)

    def _normalize_cookies_result(self, result):
        """Приводит результат get_cookies к формату (cookies, user_agent)."""
        if isinstance(result, tuple) and len(result) == 2:
            cookies, user_agent = result
            normalized_agent = str(user_agent) if user_agent is not None else self._current_user_agent
            return cookies or {}, normalized_agent
        if isinstance(result, dict):
            return result, self._current_user_agent
        return {}, self._current_user_agent

    def get_cookies(self, max_retries: int = 3, delay: float = 2.0) -> dict | None:
        for attempt in range(1, max_retries + 1):
            try:
                cookies, user_agent = self._fetch_cookies()
                if cookies:
                    logger.info(f"[get_cookies] Успешно получены cookies с попытки {attempt}")
                    if user_agent:
                        self._current_user_agent = user_agent
                        self._update_headers_user_agent(user_agent)
                    self._apply_cookies_to_session(cookies)
                    return cookies
                else:
                    raise ValueError("Пустой результат cookies")
            except Exception as e:
                logger.warning(f"[get_cookies] Попытка {attempt} не удалась: {e}")
                if attempt < max_retries:
                    time.sleep(delay * attempt)
                else:
                    logger.error(f"[get_cookies] Все {max_retries} попытки не удались")
                    return None

    def load_cookies(self) -> None:
        """Загружает cookies из JSON-файла в requests.Session."""
        try:
            path = Path("cookies.json")
            if not path.exists():
                return
            with path.open("r", encoding="utf-8") as f:
                cookies = json.load(f)
                if isinstance(cookies, dict):
                    self._apply_cookies_to_session(cookies)
                    self._last_saved_cookies_snapshot = dict(self.session.cookies.get_dict())
                    logger.info("Cookies загружены из файла")
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning(f"Не удалось загрузить cookies: {exc}")

    def save_cookies(self) -> None:
        """Сохраняет cookies из requests.Session в JSON-файл."""
        cookies_dict = self.session.cookies.get_dict()
        if cookies_dict == self._last_saved_cookies_snapshot:
            return
        path = Path("cookies.json")
        tmp_path = Path(f"{path}.tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(cookies_dict, f)
        tmp_path.replace(path)
        self._last_saved_cookies_snapshot = dict(cookies_dict)

    def fetch_data(self, url, retries=3, backoff_factor=1):
        attempt = 1
        while attempt <= retries:
            proxy_data = self._build_proxies()
            try:
                response = self.session.get(
                    url=url,
                    headers=self.headers,
                    proxies=proxy_data,
                    cookies=self.cookies,
                    impersonate="chrome",
                    timeout=20,
                    verify=False,
                    http_version=3,
                    allow_redirects=True,
                )
                status_code = response.status_code
                logger.debug(f"Попытка {attempt}: {status_code}")

                if status_code == 403:
                    self.bad_request_count += 1
                    self.consecutive_403 += 1
                    logger.warning(
                        f"Получен 403 Forbidden, попытка {attempt} (подряд: {self.consecutive_403})"
                    )
                    if attempt >= 2:
                        self._refresh_cookies_and_user_agent()
                    sleep_time = max(2, backoff_factor * attempt)
                    time.sleep(sleep_time)
                    attempt += 1
                    continue

                if status_code == 429:
                    self.bad_request_count += 1
                    self.consecutive_429 += 1
                    logger.warning(
                        f"Получен 429 Too Many Requests, попытка {attempt} (подряд: {self.consecutive_429})"
                    )
                    new_cookies = self.get_cookies()
                    if new_cookies:
                        self.save_cookies()
                    if self.consecutive_429 >= 2:
                        if self._switch_proxy():
                            logger.info("Сменили прокси после повторных 429")
                    self.consecutive_403 = 0
                    sleep_time = max(5, backoff_factor * attempt)
                    time.sleep(sleep_time)
                    attempt += 1
                    continue

                if status_code in (302,):
                    self.consecutive_429 = 0
                    self.consecutive_403 = 0
                    new_cookies = self.get_cookies()
                    if new_cookies:
                        self.save_cookies()
                    sleep_time = max(3, backoff_factor * attempt)
                    time.sleep(sleep_time)
                    attempt += 1
                    continue

                if status_code >= 500:
                    raise requests.errors.RequestsError(f"Ошибка сервера: {status_code}")

                self.save_cookies()
                self.good_request_count += 1
                self.consecutive_429 = 0
                self.consecutive_403 = 0
                return response.text

            except requests.errors.RequestsError as exc:
                logger.debug(f"Попытка {attempt} закончилась неуспешно: {exc}")
                if attempt < retries:
                    sleep_time = backoff_factor * attempt
                    logger.debug(f"Повтор через {sleep_time} секунд...")
                    time.sleep(sleep_time)
                else:
                    logger.error(f"Все попытки были неуспешными для URL: {url}")
                    logger.error(f"Последняя ошибка: {str(exc)}")
                    return None
            except Exception as exc:
                logger.error(f"Неожиданная ошибка при запросе {url}: {str(exc)}")
                logger.error(f"Трассировка: {traceback.format_exc()}")
                return None

            attempt += 1

        return None
    def load_urls_from_file(self, file_path: str) -> list[str]:
        """Загружает список URL из текстового файла."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                urls = [line.strip() for line in f if line.strip()]
            logger.info(f"Загружено {len(urls)} URL из файла {file_path}")
            return urls
        except FileNotFoundError:
            logger.error(f"Файл {file_path} не найден")
            return []
        except Exception as e:
            logger.error(f"Ошибка при чтении файла {file_path}: {e}")
            return []

    def _should_stop(self) -> bool:
        """Проверяет, пришел ли сигнал на остановку работы."""
        return not self.running or self.stop_event.is_set()

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Очищает URL от управляющих символов и пробелов."""
        return url.replace('\r', '').replace('\n', '').strip()

    def _collect_urls(self) -> list[str]:
        """Собирает и нормализует список URL из конфигурации."""
        urls_file = getattr(self.config, "urls_file", None)
        if urls_file:
            raw_urls = self.load_urls_from_file(urls_file)
        else:
            raw_urls = getattr(self.config, "urls", []) or []
        cleaned_urls = [self._normalize_url(u) for u in raw_urls if u and u.strip()]
        # сохраняем порядок, удаляя дубликаты
        unique_urls = list(dict.fromkeys(cleaned_urls))
        return unique_urls

    def _increment_error(self, url: str) -> int:
        """Увеличивает счетчик ошибок для URL и возвращает его значение."""
        attempts = self.error_count.get(url, 0) + 1
        self.error_count[url] = attempts
        return attempts

    def _reset_error(self, url: str) -> None:
        """Сбрасывает счетчик ошибок для URL после успешного парсинга."""
        self.error_count.pop(url, None)

    def parse(self) -> None:
        """Основной цикл парсинга URL."""
        self.load_cookies()
        urls = self._collect_urls()
        if not urls:
            logger.error("Не найдено URL для парсинга")
            return

        logger.info(f"Total URLS: {len(urls)}")
        logger.info(f"Начинаем парсинг {len(urls)} URL")

        self.start_scroll_page_thread('https://www.avito.ru/all/vakansii')

        batch: list[dict] = []
        for index, url in enumerate(urls, start=1):
            if self._should_stop():
                logger.info("Получен сигнал остановки, завершаем парсинг")
                break

            result = self.fetch_and_parse(url)
            if result:
                batch.append(result)

            if len(batch) >= self.BATCH_SIZE:
                logger.info(f"Сохраняем пачку из {len(batch)} записей")
                self._save_and_clear_results(batch)
                batch = []

            if index % 5 == 0:
                logger.info(f"Обработано {index}/{len(urls)} URL")

        if batch:
            logger.info(f"Сохраняем оставшиеся {len(batch)} записей")
            self._save_and_clear_results(batch)

        self.close_selenium_driver()
        logger.info(f"Хорошие запросы: {self.good_request_count}шт, плохие: {self.bad_request_count}шт")

    def fetch_and_parse(self, url: str):
        """Парсинг через requests с обработкой ошибок."""
        if self._should_stop():
            return None

        try:
            html_code = self.fetch_data(url=url, retries=self.config.max_count_of_retry)
        except Exception as exc:
            attempts = self._increment_error(url)
            logger.error(f"Ошибка при парсинге через requests URL {url}: {exc}")
            logger.error(f"Трассировка ошибки: {traceback.format_exc()}")
            if attempts >= 3:
                logger.info(f"Ошибка повторяется {attempts} раза для {url}, открываем через Selenium")
                return self.parse_with_selenium(url)
            return None

        if not html_code:
            attempts = self._increment_error(url)
            logger.warning(f"Не удалось получить HTML для URL {url}, попытка {attempts}")
            if attempts >= 3:
                logger.info(f"Повторная ошибка для {url}, открываем через Selenium")
                return self.parse_with_selenium(url)
            return None

        result = self._parse_detailed_job_info(html_code, url)
        if result:
            logger.info(f"Успешно спарсили URL: {url}")
            self._reset_error(url)
            return result

        logger.warning(f"Не удалось распарсить URL: {url}")
        return None

    def _apply_cookies_to_driver(self, cookies: dict | None) -> None:
        """Добавляет куки в Selenium-драйвер."""
        if not cookies or not isinstance(cookies, dict):
            return
        with self._selenium_lock:
            if not self.selenium_driver:
                return
            for key, value in cookies.items():
                try:
                    self.selenium_driver.add_cookie({
                        'name': key,
                        'value': value,
                        'domain': '.avito.ru',
                        'path': '/'
                    })
                except Exception as exc:
                    logger.warning(f"Не удалось установить куки {key}: {exc}")

    def parse_with_selenium(self, url: str, cookies: dict | None = None):
        """Парсинг через Selenium как запасной вариант."""
        if self._should_stop():
            return None

        with self._selenium_lock:
            if not self.selenium_driver:
                self.init_selenium_driver()
            driver = self.selenium_driver
            if not driver:
                return None

            try:
                driver.get(url)
                self._apply_cookies_to_driver(cookies)
                if cookies:
                    driver.get(url)

                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )

                total_height = driver.execute_script("return document.body.scrollHeight")
                scroll_pause_time = 10
                scroll_iterations = 10

                for _ in range(scroll_iterations):
                    if self._should_stop():
                        break
                    driver.execute_script(f"window.scrollBy(0, {total_height * 0.05});")
                    time.sleep(scroll_pause_time)

                for _ in range(scroll_iterations):
                    if self._should_stop():
                        break
                    driver.execute_script(f"window.scrollBy(0, -{total_height * 0.1});")
                    time.sleep(scroll_pause_time)

                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(scroll_pause_time)

                html_content = driver.page_source
                if html_content:
                    return self._parse_detailed_job_info(html_content, url)
                return None
            except Exception as exc:
                logger.error(f"Ошибка при парсинге через Selenium URL {url}: {exc}")
                logger.error(f"Трассировка: {traceback.format_exc()}")
                return None

    def init_selenium_driver(self):
        """Инициализация WebDriver для Selenium"""
        with self._selenium_lock:
            if self.selenium_driver is None:
                chrome_options = Options()
                chrome_options.add_argument("--log-level=3")
                chrome_options.add_argument("--no-sandbox")
                chrome_options.add_argument("--disable-dev-shm-usage")
                chrome_options.add_argument("--disable-blink-features=AutomationControlled")
                chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
                chrome_options.add_experimental_option('useAutomationExtension', False)
                try:
                    self.selenium_driver = webdriver.Chrome(options=chrome_options)
                    self.selenium_driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                    logger.info("Selenium WebDriver инициализирован")
                except Exception as e:
                    logger.error(f"Ошибка инициализации Selenium WebDriver: {e}")
                    self.selenium_driver = None

    def close_selenium_driver(self):
        """Закрытие WebDriver"""
        with self._selenium_lock:
            if self.selenium_driver:
                try:
                    self.selenium_driver.quit()
                except Exception:
                    pass
                finally:
                    self.selenium_driver = None
                logger.info("Selenium WebDriver закрыт")

    def scroll_page_with_selenium(self, url, scroll_pause_time=10, scroll_iterations=10, cookies=None):
        """Прокрутка страницы с контролируемой скоростью и двусторонней прокруткой"""
        with self._selenium_lock:
            if not self.selenium_driver:
                self.init_selenium_driver()
            driver = self.selenium_driver
            if not driver:
                return None
            try:
                driver.get(url)
                self._apply_cookies_to_driver(cookies)
                if cookies:
                    driver.get(url)
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
                total_height = driver.execute_script("return document.body.scrollHeight")
                for _ in range(scroll_iterations):
                    if self._should_stop():
                        break
                    driver.execute_script(f"window.scrollBy(0, {total_height * 0.05});")
                    time.sleep(scroll_pause_time)
                for _ in range(scroll_iterations):
                    if self._should_stop():
                        break
                    driver.execute_script(f"window.scrollBy(0, -{total_height * 0.1});")
                    time.sleep(scroll_pause_time)
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(scroll_pause_time)
                return driver.page_source
            except Exception as e:
                logger.error(f"Ошибка при прокрутке страницы: {e}")
                logger.error(f"Трассировка: {traceback.format_exc()}")
                return None

    def start_scroll_page_thread(self, url, scroll_pause_time=10, scroll_iterations=10):
        """Запуск прокрутки страницы в отдельном потоке"""
        def scroll_worker():
            try:
                logger.info(f"Запуск прокрутки в фоне для URL: {url}")
                result = self.scroll_page_with_selenium(url, scroll_pause_time, scroll_iterations)
                logger.info(f"Прокрутка завершена для URL: {url}")
                return result
            except Exception as e:
                logger.error(f"Ошибка в потоке прокрутки: {e}")
                return None
        # Создаем и запускаем поток
        scroll_thread = threading.Thread(target=scroll_worker, daemon=True)
        scroll_thread.start()
        logger.info(f"Прокрутка запущена в фоне для: {url}")
        return scroll_thread

    def _parse_detailed_job_info(self, html_content: str, url: str):
        """Извлекает данные о вакансии из HTML-страницы."""
        try:
            soup = BeautifulSoup(html_content, 'lxml')
            response = etree.HTML(str(soup))
            # Извлечение информации с использованием XPath
            employer = response.xpath("//div[@data-marker='seller-info/name']/span/text() | //div[@data-marker='seller-info/name']/a/span/text()")
            vacancy_name = response.xpath("//h1[@itemprop='name']/text()")
            description = response.xpath("//div[@data-marker='item-view/item-description']//text()")
            schedule = response.xpath("//div[@data-marker='item-view/item-params']/ul/li[span/text()='Смены']/text()")
            schedule_type = response.xpath("//div[@data-marker='item-view/item-params']/ul/li[span/text()='График']/text()")
            publish_dt = response.xpath("//span[@data-marker='item-view/item-date']/text()")
            address = response.xpath("//div[@itemprop='address']/span/text()")
            salary = response.xpath("//span[@itemprop='price']/text()")
            source_id = response.xpath("//span[@data-marker='item-view/item-id']/text()")
            vacancy_activity = response.xpath("//div[@data-marker='item-view/item-params']/ul/li[span/text()='Сфера деятельности компании']/text()")
            status = response.xpath("//a[@data-marker='item-view/closed-warning']")
            pay_period = response.xpath("//span[starts-with(@class, 'style-price-value-additional')]//text()")
            vacancy_name = vacancy_name[0] if vacancy_name else None
            schedule = schedule[0] if schedule else None
            schedule_type = schedule_type[0] if schedule_type else None
            vacancy_activity = vacancy_activity[0] if vacancy_activity else None
            if not status:
                # описание 
                description = ' '.join(description)
                # дата публикации
                publish_dt_result = ''
                dict_month = {'января': '01', 'февраля': '02', 'марта': '03', 'апреля': '04', 'мая': '05', 'июня': '06', 'июля': '07', 'августа': '08', 'сентября': '09', 
                                'октября': '10', 'ноября': '11', 'декабря': '12'}
                # Обработка ошибки IndexError - просто пропускаем URL при ошибке
                try:
                    publish_dt_value = None
                    if len(publish_dt) >= 2:
                        publish_dt_value = publish_dt[1]
                    elif len(publish_dt) >= 1:
                        publish_dt_value = publish_dt[0]
                    if publish_dt_value and 'вчера в' in publish_dt_value: # если опубликовано вчера 
                        hour = int(publish_dt_value[-5:-3])
                        minute = int(publish_dt_value[-2:])                        
                        publish_dt_result = time.mktime((datetime.now() - timedelta(days = 1)).replace(hour = hour, minute = minute).timetuple())
                    elif publish_dt_value and 'сегодня в' in publish_dt_value: # если опубликовано сегодня 
                        hour = int(publish_dt_value[-5:-3])
                        minute = int(publish_dt_value[-2:])
                        publish_dt_result = time.mktime(datetime.now().replace(hour = hour, minute = minute).timetuple())
                    else: # это дата, которую нужно преобразовать
                        if publish_dt_value:
                            publish_dt_split = publish_dt_value.split(' ')
                            if len(publish_dt_split) >= 3:
                                publish_dt_split[1] = dict_month.get(publish_dt_split[1].lower())
                                publish_dt_formatted = '{} {} {} {} {}'.format(
                                    publish_dt_split[0], 
                                    publish_dt_split[1], 
                                    datetime.now().year, 
                                    int(publish_dt_split[-1][-5:-3]), 
                                    int(publish_dt_split[-1][-2:])
                                )
                                publish_dt_parsed = datetime.strptime(publish_dt_formatted, '%d %m %Y %H %M')
                                publish_dt_result = time.mktime(publish_dt_parsed.timetuple())
                except Exception as e:
                    logger.warning(f"Ошибка при парсинге даты для URL {url}: {e}")
                    publish_dt_result = 0  # Значение по умолчанию
                # ЗП 
                salary_result = []
                if salary: 
                    salary = salary[0].split(' ')
                    for s in salary: 
                        s = s.replace('\xa0', '')
                        s = ''.join([i for i in s if i.isdigit()]) # удаляем все нечисловые значения
                        if s.isdigit():
                            salary_result.append(s.replace('\xa0', ''))
                    if len(salary_result) == 1: # если указана только одна ЗП (мин или макс) - дублируем
                        salary_result.append(salary_result[0])
                # период оплаты
                if pay_period:
                    pay_period_result = pay_period[0].replace('\xa0', ' ')
                # Обработка ошибки IndexError для source_id
                try:
                    source_id_value = source_id[1] if source_id and len(source_id) > 1 else None
                except Exception as e:
                    logger.warning(f"Ошибка при получении source_id для URL {url}: {e}")
                    source_id_value = None
                data = {
                    'external_id': url, 
                    'employer': employer,
                    'vacancy_name': vacancy_name, 
                    'description': description, 
                    'type_schedule': schedule_type, 
                    'publish_dt': publish_dt_result, 
                    'vacancy_source': 'avito', 
                    'location_source': address[0] if address else None, 
                    'location_region': None, 
                    'location_city': None, 
                    'location_coordinates': None, 
                    'salary_min': salary_result[0] if salary_result else None,
                    'salary_max': salary_result[1] if salary_result else None,
                    'salary_type': None,
                    'schedule': schedule,
                    'source_id': source_id_value,
                    'vacancy_activity': [vacancy_activity] if vacancy_activity else [],
                    'employer_id': None,
                    'driver_license_types': None,
                    'pay_period': pay_period_result if pay_period else None,
                }
                return data
            else: 
                return None
        except Exception as e:
            logger.error(f"Ошибка при парсинге URL {url}: {e}")
            return None

    def _save_and_clear_results(self, results):
        """Сохраняет результаты в JSON и БД."""
        if not results:
            logger.warning("Попытка сохранить пустой список результатов")
            return
        # Фильтруем пустые и некорректные результаты
        valid_results = []
        for result in results:
            if result is not None and isinstance(result, dict) and result.get('external_id'):
                valid_results.append(result)
            elif result is not None:
                logger.warning(f"Пропущен некорректный результат: {type(result)}")
        if not valid_results:
            logger.warning("Нет валидных результатов для сохранения")
            return
        try:
            # Сохраняем в JSON
            with open('parsed_results.json', 'w', encoding='utf-8') as f:
                json.dump(valid_results, f, ensure_ascii=False, indent=2, default=str)
            logger.info(f"Сохранены {len(valid_results)} результатов в parsed_results.json")
            # Сохраняем в БД
            if self.db_handler:
                logger.info(f"Пытаюсь сохранить {len(valid_results)} записей в БД")
                self.db_handler.add_record_from_page(valid_results)
                logger.info(f"Сохранены {len(valid_results)} результатов в БД")
            else:
                logger.warning("БД не подключена, сохранение пропущено")
        except Exception as e:
            logger.error(f"Ошибка при сохранении результатов: {e}")
            logger.error(f"Трассировка: {traceback.format_exc()}")

    def change_ip(self, max_attempts: int = 3) -> bool:
        """Пробует сменить IP согласно настройкам конфигурации."""
        if self.proxy_pool and len(self.proxy_pool) > 1:
            logger.info("Переключаюсь на другой прокси из пула")
            return self._switch_proxy()

        change_link = None
        if self.proxy_obj and self.proxy_obj.change_ip_link:
            change_link = self.proxy_obj.change_ip_link
        elif isinstance(self.config.proxy_change_url, str):
            candidate = self.config.proxy_change_url.strip()
            if candidate.lower().startswith(("http://", "https://")):
                change_link = candidate

        if change_link:
            logger.info("Меняю IP через API провайдера прокси")
            for attempt in range(1, max_attempts + 1):
                try:
                    response = requests.get(change_link, timeout=20, verify=False, proxies=None)
                    if response.status_code == 200:
                        logger.info("IP изменен через провайдера прокси")
                        return True
                    logger.warning(f"Не удалось сменить IP (статус {response.status_code}), попытка {attempt}")
                except Exception as err:
                    logger.info(f"При смене IP через прокси возникла ошибка: {err} (попытка {attempt})")
                time.sleep(random.randint(3, 10))
            logger.warning("Не удалось изменить IP через API провайдера")
            return False

        if self.config.use_local_ip:
            logger.info("Пауза для локальной сети")
            # Local IP change simulation - just wait
            time.sleep(random.randint(5, 15))
            logger.info("Пауза закончена")
            return True
        logger.info("Смена IP отключена")
        return False

    # Методы, которые используются в процессе парсинга
    @staticmethod
    def _extract_seller_slug(data):
        match = re.search(r"/brands/([^/?#]+)", str(data))
        if match:
            return match.group(1)
        return None

def signal_handler(sig, frame):
    """Обработчик сигнала Ctrl+C"""
    print('\nПолучен сигнал завершения (Ctrl+C)...')
    # Устанавливаем флаг завершения
    global stop_event
    if 'stop_event' in globals():
        stop_event.set()
    sys.exit(0)
# Добавляем глобальную переменную для управления остановкой
stop_event = threading.Event()
if __name__ == "__main__":
    # Устанавливаем обработчик сигнала для Ctrl+C
    signal.signal(signal.SIGINT, signal_handler)
    try:
        config = load_avito_config("config.toml")
    except Exception as err:
        logger.error(f"Ошибка загрузки конфига: {err}")
        exit(1)
    # Добавляем возможность выбора режима работы
    parser = AvitoParse(config, stop_event)
    # Если указан файл с URL, запускаем парсинг по нему
    if hasattr(config, 'urls_file') and config.urls_file:
        logger.info("Запуск парсинга по URL из файла")
        parser.parse()
    else:
        # Обычный режим работы
        while not stop_event.is_set():
            try:
                parser.parse()
                logger.info(f"Парсинг завершен. Пауза {config.pause_general} сек")
                time.sleep(config.pause_general)
            except Exception as err:
                logger.error(f"Произошла ошибка {err}")
                time.sleep(30)
