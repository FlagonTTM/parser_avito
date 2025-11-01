import asyncio
import random

import httpx
from loguru import logger
from playwright.async_api import async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright_stealth import Stealth
from typing import Optional, Dict, List

from dto import Proxy, ProxySplit

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

try:
    with open("user_agent_pc.txt", encoding="utf-8") as _ua_file:
        USER_AGENTS = [ua.strip() for ua in _ua_file if ua.strip()]
except FileNotFoundError:
    logger.warning("Файл user_agent_pc.txt не найден, используем стандартный user-agent")
    USER_AGENTS = []
MAX_RETRIES = 3
RETRY_DELAY = 10
RETRY_DELAY_WITHOUT_PROXY = 300
BAD_IP_TITLE = "проблема с ip"
class PlaywrightClient:
    def __init__(
        self,
        proxy: Proxy = None,
        headless: bool = True,
        user_agent: Optional[str] = None,
    ):
        self.proxy = proxy
        self.proxy_split_obj = self.get_proxy_obj()
        self.headless = headless
        if user_agent:
            self.user_agent = str(user_agent)
        elif USER_AGENTS:
            self.user_agent = str(random.choice(USER_AGENTS))
        else:
            self.user_agent = DEFAULT_USER_AGENT
        self.context = self.page = self.browser = None
        self.playwright = None
        self._playwright_cm = None
        self.last_cookie_url: Optional[str] = None
        self._last_humanize = 0.0
    @staticmethod
    def check_protocol(ip_port: str) -> str:
        if "http://" not in ip_port:
            return f"http://{ip_port}"
        return ip_port
    @staticmethod
    def del_protocol(proxy_string: str):
        if "//" in proxy_string:
            return proxy_string.split("//")[1]
        return proxy_string
    def get_proxy_obj(self) -> ProxySplit | None:
        if not self.proxy:
            return None
        try:
            self.proxy.proxy_string = self.del_protocol(proxy_string=self.proxy.proxy_string)
            if "@" in self.proxy.proxy_string:
                ip_port, user_pass = self.proxy.proxy_string.split("@")
                if "." in user_pass:
                    ip_port, user_pass = user_pass, ip_port
                login, password = str(user_pass).split(":")
            else:
                login, password, ip, port = self.proxy.proxy_string.split(":")
                if "." in login:
                    login, password, ip, port = ip, port, login, password
                ip_port = f"{ip}:{port}"
            ip_port = self.check_protocol(ip_port=ip_port)
            return ProxySplit(
                ip_port=ip_port,
                login=login,
                password=password,
                change_ip_link=self.proxy.change_ip_link
            )
        except Exception as err:
            logger.error(err)
            logger.critical("Прокси в таком формате не поддерживаются. "
                            "Используй: ip:port@user:pass или ip:port:user:pass")
    @staticmethod
    def parse_cookie_string(cookie_str: str) -> dict:
        return dict(pair.split("=", 1) for pair in cookie_str.split("; ") if "=" in pair)
    async def _restart_browser(self):
        """Закрывает текущий браузер и запускает новый с обновленными настройками."""
        await self.close()
        await self.ensure_browser()
    async def ensure_browser(self):
        if not self.browser or not getattr(self.browser, "is_connected", lambda: False)():
            await self.launch_browser()
            return
        if self.context is None or getattr(self.context, "is_closed", lambda: True)():
            await self._recreate_context()
            return
        if self.page is None or getattr(self.page, "is_closed", lambda: True)():
            await self._recreate_page()

    async def _recreate_context(self):
        if self.context and not getattr(self.context, "is_closed", lambda: True)():
            try:
                await self.context.close()
            except Exception:
                pass
        context_args = {
            "user_agent": self.user_agent,
            "viewport": {"width": 1920, "height": 1080},
            "screen": {"width": 1920, "height": 1080},
            "device_scale_factor": 1,
            "is_mobile": False,
            "has_touch": False,
        }
        if self.proxy_split_obj:
            context_args["proxy"] = {
                "server": self.proxy_split_obj.ip_port,
                "username": self.proxy_split_obj.login,
                "password": self.proxy_split_obj.password
            }
        self.context = await self.browser.new_context(**context_args)
        await self._recreate_page()

    async def _recreate_page(self):
        if self.context is None:
            return
        if self.page and not getattr(self.page, "is_closed", lambda: True)():
            try:
                await self.page.close()
            except Exception:
                pass
        self.page = await self.context.new_page()
        await self._stealth(self.page)
    async def close(self):
        try:
            if self.page:
                await self.page.close()
        except Exception:
            pass
        finally:
            self.page = None
        try:
            if self.context:
                await self.context.close()
        except Exception:
            pass
        finally:
            self.context = None
        try:
            if self.browser:
                await self.browser.close()
        except Exception:
            pass
        finally:
            self.browser = None
        if self.playwright and self._playwright_cm:
            try:
                await self._playwright_cm.__aexit__(None, None, None)
            except Exception:
                pass
        self.playwright = None
        self._playwright_cm = None
    async def launch_browser(self):
        if self.browser:
            return
        stealth = Stealth()
        self._playwright_cm = stealth.use_async(async_playwright())
        self.playwright = await self._playwright_cm.__aenter__()
        launch_args = {
            "headless": self.headless,
            "chromium_sandbox": False,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--start-maximized",
                "--window-size=1920,1080",
            ]
        }
        self.browser = await self.playwright.chromium.launch(**launch_args)
        await self._recreate_context()
    async def load_page(self, url: str):
        for attempt in range(6):
            await self.ensure_browser()
            try:
                assert self.page
                await self.page.goto(url=url, timeout=60_000, wait_until="domcontentloaded")
            except PlaywrightError as exc:
                logger.debug(f"[Playwright load_page] Ошибка при переходе на {url}: {exc}")
                await self._restart_browser()
                await asyncio.sleep(1.0)
                continue
            except Exception as exc:
                logger.debug(f"[Playwright load_page] Неожиданная ошибка при переходе на {url}: {exc}")
                await asyncio.sleep(1.0)
                continue
            if await self.check_block(url):
                url = self._random_listing_url()
                await asyncio.sleep(1.5)
                continue
            try:
                raw_cookie = await self.page.evaluate("() => document.cookie")
            except PlaywrightError as exc:
                logger.debug(f"[Playwright load_page] Не удалось прочитать cookies: {exc}")
                await self._restart_browser()
                await asyncio.sleep(1.0)
                continue
            cookie_dict = self.parse_cookie_string(raw_cookie)
            if cookie_dict.get("ft"):
                logger.info("Cookies получены")
                return cookie_dict
            await asyncio.sleep(2)
        logger.warning("Не удалось получить cookies")
        return {}
    async def extract_cookies(self, url: str) -> dict:
        await self.ensure_browser()
        cookies = await self.load_page(url)
        self.last_cookie_url = url
        return cookies
    async def get_cookies(self, url: str) -> dict:
        return await self.extract_cookies(url)
    async def check_block(self, url: str) -> bool:
        if not self.page:
            return False
        try:
            title = await self.page.title()
        except PlaywrightError as exc:
            logger.debug(f"[Playwright check_block] Не удалось получить title: {exc}")
            await self._restart_browser()
            return True
        logger.info(f"Не ошибка, а название страницы: {title}")
        if BAD_IP_TITLE in str(title).lower():
            logger.info("IP заблокирован")
            if self.context:
                try:
                    await self.context.clear_cookies()
                except Exception:
                    pass
            await self.change_ip()
            try:
                await self.page.goto(self._random_listing_url(), timeout=60_000, wait_until="domcontentloaded")
            except PlaywrightError as exc:
                logger.debug(f"[Playwright check_block] Ошибка при переходе после смены IP: {exc}")
                await self._restart_browser()
            return True
        return False
    async def change_ip(self, retries: int = MAX_RETRIES):
        if not self.proxy_split_obj:
            logger.info("Сейчас бы сменили ip, но прокси нет - поэтому ждем")
            await asyncio.sleep(RETRY_DELAY_WITHOUT_PROXY)
            return False
        rotation_pool = getattr(self.proxy, "rotation_pool", []) if self.proxy else []
        rotated_locally = await self._rotate_local_proxy(rotation_pool)
        if rotated_locally:
            logger.info("Переключились на следующий прокси из пула")
            return True
        change_url = self._build_change_url()
        if not change_url:
            logger.info("Провайдер прокси не поддерживает смену IP по API — делаем паузу")
            await asyncio.sleep(RETRY_DELAY)
            return False

        for attempt in range(1, retries + 1):
            try:
                response = httpx.get(change_url, timeout=15, verify=False)
                if response.status_code == 200:
                    try:
                        payload = response.json()
                        new_ip = payload.get("new_ip")
                    except Exception:
                        new_ip = None
                    logger.info(f"IP изменён на {new_ip or 'неизвестный'}")
                    await asyncio.sleep(2)
                    return True
                else:
                    logger.warning(f"[{attempt}/{retries}] Ошибка смены IP: {response.status_code}")
                    if response.status_code in (401, 403, 407):
                        logger.error("Провайдер вернул код авторизации/доступа при смене IP, прекращаем попытки")
                        return False
            except httpx.RequestError as e:
                logger.error(f"[{attempt}/{retries}] Ошибка смены IP: {e}")
            if attempt < retries:
                logger.info(f"Повторная попытка сменить IP через {RETRY_DELAY} секунд...")
                await asyncio.sleep(RETRY_DELAY)
            else:
                logger.error("Превышено количество попыток смены IP")
                return False
        return False
    async def _rotate_local_proxy(self, rotation_pool: List[str]) -> bool:
        if not rotation_pool or len(rotation_pool) <= 1:
            return False
        sanitized_pool = [self.del_protocol(proxy) for proxy in rotation_pool if proxy]
        if len(sanitized_pool) <= 1:
            return False
        current = self.del_protocol(self.proxy.proxy_string) if self.proxy and self.proxy.proxy_string else None
        try:
            current_index = sanitized_pool.index(current) if current else -1
        except ValueError:
            current_index = -1
        next_index = (current_index + 1) % len(sanitized_pool)
        if current_index == next_index and current_index != -1:
            return False
        next_proxy = sanitized_pool[next_index]
        logger.info(f"Переключаюсь на следующий прокси из пула: {next_proxy}")
        if self.proxy:
            self.proxy.proxy_string = next_proxy
            setattr(self.proxy, "rotation_pool", sanitized_pool)
        self.proxy_split_obj = self.get_proxy_obj()
        await self._restart_browser()
        return True
    def _build_change_url(self) -> Optional[str]:
        if not self.proxy_split_obj or not self.proxy_split_obj.change_ip_link:
            return None
        base_link = str(self.proxy_split_obj.change_ip_link).strip()
        if not base_link:
            return None
        separator = "&" if "?" in base_link else "?"
        return f"{base_link}{separator}format=json"
    def _random_listing_url(self) -> str:
        ads_id = random.randint(1111111111, 9999999999)
        return f"https://www.avito.ru/{ads_id}"
    def is_compatible(self, proxy: Proxy | None, user_agent: Optional[str]) -> bool:
        normalized_proxy = self.del_protocol(proxy.proxy_string) if proxy else None
        current_proxy = self.del_protocol(self.proxy.proxy_string) if self.proxy and self.proxy.proxy_string else None
        ua = str(user_agent) if user_agent else self.user_agent
        return normalized_proxy == current_proxy and ua == self.user_agent
    async def humanize_session(self, extra_routes: Optional[List[str]] = None):
        await self.ensure_browser()
        routes = extra_routes or []
        if not routes:
            routes = [
                "https://www.avito.ru/",
                random.choice([
                    "https://www.avito.ru/moskva/nedvizhimost",
                    "https://www.avito.ru/rossiya/transport",
                    "https://www.avito.ru/moskva/rabota",
                    self._random_listing_url()
                ])
            ]
        for url in routes:
            try:
                await self.page.goto(url, timeout=60_000, wait_until="domcontentloaded")
                await asyncio.sleep(random.uniform(1.0, 2.0))
                await self._human_clicks()
            except Exception as exc:
                logger.debug(f"[Playwright humanize] Не удалось обработать {url}: {exc}")
        self._last_humanize = asyncio.get_event_loop().time()
    async def _human_clicks(self):
        if not self.page:
            return
        try:
            scroll_steps = random.randint(2, 5)
            for _ in range(scroll_steps):
                await self.page.mouse.wheel(0, random.randint(400, 900))
                await asyncio.sleep(random.uniform(0.3, 0.7))
            candidates = await self.page.query_selector_all("a[href^='https://www.avito.ru/']")
            random.shuffle(candidates)
            for candidate in candidates[:3]:
                href = await candidate.get_attribute("href")
                if not href:
                    continue
                try:
                    await candidate.hover()
                    await asyncio.sleep(random.uniform(0.2, 0.5))
                    await candidate.click(button="left", delay=random.randint(40, 120))
                    await self.page.wait_for_load_state("domcontentloaded", timeout=30_000)
                    await asyncio.sleep(random.uniform(1.0, 2.0))
                    await self.page.go_back()
                    await self.page.wait_for_load_state("domcontentloaded", timeout=30_000)
                    break
                except Exception:
                    continue
        except Exception:
            pass
    @staticmethod
    async def _stealth(page):
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
            Object.defineProperty(navigator, 'vendor', { get: () => 'Google Inc.' });
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        """)
    @staticmethod
    async def block_images(route, request):
        if request.resource_type == "image":
            await route.abort()
        else:
            await route.continue_()
class PlaywrightManager:
    def __init__(self):
        self.client: Optional[PlaywrightClient] = None
        self.lock = asyncio.Lock()
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.last_cookie_refresh = 0.0
        self.last_humanize = 0.0
        self.cookie_refresh_interval = 240.0
        self.humanize_interval = 300.0

    async def _ensure_client(self, proxy: Proxy | None, user_agent: Optional[str]):
        current_loop = asyncio.get_running_loop()
        if self.loop and self.loop is not current_loop:
            if self.client:
                await self.client.close()
            self.client = None
        self.loop = current_loop
        if self.client and not self.client.is_compatible(proxy, user_agent):
            await self.client.close()
            self.client = None
        if not self.client:
            self.client = PlaywrightClient(proxy=proxy, user_agent=user_agent)
            await self.client.ensure_browser()
        else:
            await self.client.ensure_browser()

    async def get_cookies(self, proxy: Proxy | None, user_agent: Optional[str]) -> tuple[dict, str]:
        async with self.lock:
            await self._ensure_client(proxy, user_agent)
            assert self.client
            target_url = self.client._random_listing_url()
            cookies = await self.client.get_cookies(target_url)
            self.last_cookie_refresh = asyncio.get_event_loop().time()
            return cookies, self.client.user_agent

    async def humanized_browse(self, proxy: Proxy | None, user_agent: Optional[str], routes: Optional[List[str]] = None):
        async with self.lock:
            await self._ensure_client(proxy, user_agent)
            assert self.client
            await self.client.humanize_session(routes)
            self.last_humanize = asyncio.get_event_loop().time()

    async def periodic_refresh(self, proxy: Proxy | None, user_agent: Optional[str]):
        now = asyncio.get_event_loop().time()
        if now - self.last_cookie_refresh > self.cookie_refresh_interval:
            await self.get_cookies(proxy, user_agent)
        if now - self.last_humanize > self.humanize_interval:
            await self.humanized_browse(proxy, user_agent)

    async def shutdown(self):
        async with self.lock:
            if self.client:
                await self.client.close()
                self.client = None
            self.loop = None


_manager = PlaywrightManager()


async def get_cookies(proxy: Proxy = None, headless: bool = True, user_agent: Optional[str] = None) -> tuple:
    logger.info("Пытаюсь обновить cookies")
    return await _manager.get_cookies(proxy, user_agent)


async def humanized_browse(proxy: Proxy = None, user_agent: Optional[str] = None, routes: Optional[List[str]] = None):
    await _manager.humanized_browse(proxy, user_agent, routes)


async def ensure_playwright_alive(proxy: Proxy = None, user_agent: Optional[str] = None):
    await _manager.periodic_refresh(proxy, user_agent)


async def shutdown_playwright():
    await _manager.shutdown()
