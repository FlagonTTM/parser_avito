import asyncio
import random

import httpx
from loguru import logger
from playwright.async_api import async_playwright
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
    async def launch_browser(self):
        stealth = Stealth()
        self.playwright_context = stealth.use_async(async_playwright())
        playwright = await self.playwright_context.__aenter__()
        self.playwright = playwright
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
        self.browser = await playwright.chromium.launch(**launch_args)
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
        self.page = await self.context.new_page()
        await self._stealth(self.page)
    async def load_page(self, url: str):
        await self.page.goto(url=url, timeout=60_000,
                             wait_until="domcontentloaded")
        for attempt in range(10):
            await self.check_block(self.page, self.context)
            raw_cookie = await self.page.evaluate("() => document.cookie")
            cookie_dict = self.parse_cookie_string(raw_cookie)
            if cookie_dict.get("ft"):
                logger.info("Cookies получены")
                return cookie_dict
            await asyncio.sleep(5)
        logger.warning("Не удалось получить cookies")
        return {}
    async def extract_cookies(self, url: str) -> dict:
        try:
            await self.launch_browser()
            return await self.load_page(url)
        finally:
            if hasattr(self, "browser"):
                if self.browser:
                    await self.browser.close()
            if hasattr(self, "playwright"):
                await self.playwright.stop()
    async def get_cookies(self, url: str) -> dict:
        return await self.extract_cookies(url)
    async def check_block(self, page, context):
        title = await page.title()
        logger.info(f"Не ошибка, а название страницы: {title}")
        if BAD_IP_TITLE in str(title).lower():
            logger.info("IP заблокирован")
            await context.clear_cookies()
            await self.change_ip()
            await page.reload(timeout=60*1000)
    async def change_ip(self, retries: int = MAX_RETRIES):
        if not self.proxy_split_obj:
            logger.info("Сейчас бы сменили ip, но прокси нет - поэтому ждем")
            await asyncio.sleep(RETRY_DELAY_WITHOUT_PROXY)
            return False
        if not self.proxy_split_obj.change_ip_link:
            rotation_pool = getattr(self.proxy, "rotation_pool", []) if self.proxy else []
            if rotation_pool and len(rotation_pool) > 1:
                logger.info("Переключение на следующий прокси будет выполнено основным парсером")
                return False
            logger.info("Провайдер прокси не поддерживает смену IP по API — делаем паузу")
            await asyncio.sleep(RETRY_DELAY)
            return False
        if self.proxy_split_obj.change_ip_link:
            base_link = str(self.proxy_split_obj.change_ip_link)
            separator = "&" if "?" in base_link else "?"
            change_url = f"{base_link}{separator}format=json"
        else:
            change_url = None

        for attempt in range(1, retries + 1):
            try:
                if not change_url:
                    logger.error("Не задан URL для смены IP")
                    return False
                response = httpx.get(change_url, timeout=20)
                if response.status_code == 200:
                    logger.info(f"IP изменён на {response.json().get('new_ip')}")
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
async def get_cookies(proxy: Proxy = None, headless: bool = True, user_agent: Optional[str] = None) -> tuple:
    logger.info("Пытаюсь обновить cookies")
    client = PlaywrightClient(
        proxy=proxy,
        headless=headless,
        user_agent=user_agent,
    )
    ads_id = str(random.randint(1111111111, 9999999999))
    cookies = await client.get_cookies(f"https://www.avito.ru/{ads_id}")
    return cookies, client.user_agent
