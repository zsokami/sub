import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from random import choice
from threading import RLock, Thread
from time import sleep, time
from urllib.parse import parse_qsl, unquote_plus, urlencode, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from requests.structures import CaseInsensitiveDict
from selenium.webdriver.support.expected_conditions import any_of, title_is
from selenium.webdriver.support.ui import WebDriverWait
from undetected_chromedriver import Chrome, ChromeOptions
from urllib3 import Retry

from utils import get_id


class Response:
    def __init__(self, content: bytes, headers: CaseInsensitiveDict[str], status_code: int, reason: str):
        self.content = content
        self.headers = headers
        self.status_code = status_code
        self.reason = reason

    @property
    def text(self):
        if not hasattr(self, '_Response__text'):
            self.__text = self.content.decode()
        return self.__text

    def json(self):
        if not hasattr(self, '_Response__json'):
            self.__json = json.loads(self.text)
        return self.__json

    def bs(self):
        if not hasattr(self, '_Response__bs'):
            self.__bs = BeautifulSoup(self.text, 'html.parser')
        return self.__bs

    def __str__(self):
        return f'{self.status_code} {self.reason} {self.text}'


class Session(requests.Session):
    def __init__(self, host=None, user_agent=None):
        super().__init__()
        self.mount('https://', HTTPAdapter(max_retries=Retry(total=5, backoff_factor=0.1)))
        self.mount('http://', HTTPAdapter(max_retries=Retry(total=5, backoff_factor=0.1)))
        self.headers['User-Agent'] = user_agent or 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36'
        self.base = 'https://' + host if host else ''
        self.host = host

    def close(self):
        super().close()
        if hasattr(self, 'chrome'):
            self.chrome.quit()

    def reset(self):
        self.cookies.clear()
        self.headers.pop('authorization', None)
        if hasattr(self, 'chrome'):
            self.chrome.delete_all_cookies()
            for cookie in self.chrome_default_cookies:
                self.chrome.add_cookie(cookie)

    def head(self, url, **kwargs) -> Response:
        return super().head(url, **kwargs)

    def get(self, url, **kwargs) -> Response:
        return super().get(url, **kwargs)

    def post(self, url, data=None, **kwargs) -> Response:
        return super().post(url, data, **kwargs)

    def request(self, method, url: str, data=None, timeout=5, **kwargs):
        url = urljoin(self.base, url)
        if not hasattr(self, 'chrome'):
            res = super().request(method, url, data=data, timeout=timeout, **kwargs)
            res = Response(res.content, res.headers, res.status_code, res.reason)
            if res.status_code != 403 and (
                'Content-Type' not in res.headers
                or not res.headers['Content-Type'].startswith('text/html')
                or not res.content
                or res.content[0] != 60
                or not res.bs().title
                or res.bs().title.text not in ('Just a moment...', '')
            ):
                return res
        cur_host = urlsplit(url).hostname
        if urlsplit(self.get_chrome().current_url).hostname != cur_host:
            self.chrome.get('https://' + cur_host)
            WebDriverWait(self.chrome, 15).until_not(any_of(title_is('Just a moment...'), title_is('')))
            self.chrome_default_cookies = self.chrome.get_cookies()
        headers = CaseInsensitiveDict()
        if 'authorization' in self.headers:
            headers['authorization'] = self.headers['authorization']
        if data:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
            body = repr(data if isinstance(data, str) else urlencode(data))
        else:
            body = 'null'
        content, header_list, status_code, reason = self.chrome.execute_script(f'''
            const res = await fetch({repr(url)}, {{ method: {repr(method)}, headers: {repr(headers)}, body: {body} }})
            return [new Uint8Array(await res.arrayBuffer()), [...res.headers], res.status, res.statusText]
        ''')
        return Response(bytes(content), CaseInsensitiveDict(header_list), int(status_code), reason)

    def get_chrome(self):
        if not hasattr(self, 'chrome'):
            print(f'{self.host} using Chrome')
            options = ChromeOptions()
            options.add_argument('--disable-web-security')
            options.add_argument('--ignore-certificate-errors')
            options.add_argument('--allow-running-insecure-content')
            options.page_load_strategy = 'eager'
            self.chrome = Chrome(
                options=options,
                driver_executable_path=os.path.join(os.getenv('CHROMEWEBDRIVER'), 'chromedriver')
            )
            self.chrome.set_page_load_timeout(15)
        return self.chrome

    def get_ip_info(self):
        """return (ip, 位置, 运营商)"""
        addr = self.get(f'https://ip125.com/api/{self.get("https://ident.me").text}?lang=zh-CN').json()
        return (
            addr['query'],
            addr['country'] + (',' + addr['city'] if addr['city'] and addr['city'] != addr['country'] else ''),
            addr['isp'] + (',' + addr['org'] if addr['org'] and addr['org'] != addr['isp'] else '')
        )


class V2BoardSession(Session):
    def __set_auth(self, email: str, reg_info: dict):
        if 'data' in reg_info:
            self.email = email
            self.login_info = reg_info
            if 'v2board_session' not in self.cookies:
                self.headers['authorization'] = reg_info['data']['auth_data']

    def register(self, email: str, password=None, email_code=None, invite_code=None) -> dict:
        self.reset()
        res = self.post('api/v1/passport/auth/register', {
            'email': email,
            'password': password or email.split('@')[0],
            **({'email_code': email_code} if email_code else {}),
            **({'invite_code': invite_code} if invite_code else {})
        }).json()
        self.__set_auth(email, res)
        return res

    def login(self, email: str = None, password=None) -> dict:
        if not email or email == getattr(self, 'email', None):
            return self.login_info
        self.reset()
        res = self.post('api/v1/passport/auth/login', {
            'email': email,
            'password': password or email.split('@')[0]
        }).json()
        self.__set_auth(email, res)
        return res

    def send_email_code(self, email) -> dict:
        return self.post('api/v1/passport/comm/sendEmailVerify', {
            'email': email
        }, timeout=60).json()

    def order_save(self, data) -> dict:
        return self.post(
            'api/v1/user/order/save',
            data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        ).json()

    def order_checkout(self, trade_no, method=None) -> dict:
        return self.post('api/v1/user/order/checkout', {
            'trade_no': trade_no,
            **({'method': method} if method else {})
        }).json()

    def get_sub_url(self) -> str:
        self.sub_url = self.get('api/v1/user/getSubscribe').json()['data']['subscribe_url']
        return self.sub_url


class SSPanelSession(Session):
    def __init__(self, host=None, user_agent=None, auth_path=None):
        super().__init__(host, user_agent)
        self.auth_path = auth_path or 'auth'

    def register(self, email: str, password=None, email_code=None, invite_code=None, name_eq_email=None, reg_fmt=None, im_type=False) -> dict:
        self.reset()
        email_code_k, invite_code_k = ('email_code', 'invite_code') if reg_fmt == 'B' else ('emailcode', 'code')
        password = password or email.split('@')[0]
        res = self.post(f'{self.auth_path}/register', {
            'name': email if name_eq_email == 'T' else password,
            'email': email,
            'passwd': password,
            'repasswd': password,
            **({email_code_k: email_code} if email_code else {}),
            **({invite_code_k: invite_code} if invite_code else {}),
            **({'imtype': 1, 'wechat': password} if im_type else {})
        }).json()
        if res['ret']:
            self.email = email
        return res

    def login(self, email: str = None, password=None) -> dict:
        if not email:
            email = self.email
        if 'email' in self.cookies and email == unquote_plus(self.cookies.get('email')):
            return {'ret': 1}
        self.reset()
        res = self.post(f'{self.auth_path}/login', {
            'email': email,
            'passwd': password or email.split('@')[0]
        }).json()
        return res

    def send_email_code(self, email) -> dict:
        return self.post(f'{self.auth_path}/send', {
            'email': email
        }, timeout=60).json()

    def buy(self, data) -> dict:
        return self.post(
            'user/buy',
            data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        ).json()

    def checkin(self) -> dict:
        return self.post('user/checkin').json()

    def get_sub_url(self, params=None) -> str:
        if not params:
            params = 'sub=3'
        elif isinstance(params, dict):
            params = urlencode(params)
        doc = self.get('user').bs()
        sub_url = doc.find(attrs={'data-clipboard-text': True})['data-clipboard-text']
        for k, v in parse_qsl(urlsplit(sub_url).query):
            if k == 'url':
                sub_url = v
                break
        self.sub_url = f'{sub_url.split("?")[0]}?{params}'
        return self.sub_url


re_email_code = re.compile(r'(?<!\d)\d{6}(?!\d)')


class TempEmail:
    def __init__(self):
        self.__lock_account = RLock()
        self.__lock = RLock()
        self.__queues: list[tuple[str, Queue, float]] = []

    def get_email(self) -> str:
        with self.__lock_account:
            if not hasattr(self, '_TempEmail__address'):
                session = Session('api.mail.gw')
                r = session.get('domains')
                if r.status_code != 200:
                    raise Exception(f'获取邮箱域名失败: {r}')
                domain = choice([item['domain'] for item in r.json()['hydra:member']])
                account = {'address': f'{get_id()}@{domain}', 'password': get_id()}
                r = session.post('accounts', json=account)
                if r.status_code != 201:
                    raise Exception(f'创建账户失败: {r}')
                r = session.post('token', json=account)
                if r.status_code != 200:
                    raise Exception(f'获取 token 失败: {r}')
                session.headers['Authorization'] = f'Bearer {r.json()["token"]}'
                self.__session = session
                self.__address = account['address']
        return self.__address

    def get_email_code(self, keyword) -> str | None:
        queue = Queue(1)
        with self.__lock:
            self.__queues.append((keyword, queue, time() + 60))
            if not hasattr(self, '_TempEmail__th'):
                self.__th = Thread(target=self.__run)
                self.__th.start()
        return queue.get()

    def __run(self):
        while True:
            sleep(1)
            messages = []
            session = self.__session
            try:
                r = session.get('messages')
                if r.status_code == 200:
                    items = r.json()['hydra:member']
                    if items:
                        for r in ThreadPoolExecutor(len(items)).map(lambda item: session.get(f'messages/{item["id"]}'), items):
                            if r.status_code == 200:
                                messages.append(r.json()['text'])
            except Exception as e:
                print(f'TempEmail.__run: {e}')
            with self.__lock:
                new_len = 0
                for item in self.__queues:
                    keyword, queue, end_time = item
                    for message in messages:
                        if keyword in message:
                            m = re_email_code.search(message)
                            queue.put(m[0] if m else m)
                            break
                    else:
                        if time() > end_time:
                            queue.put(None)
                        else:
                            self.__queues[new_len] = item
                            new_len += 1
                del self.__queues[new_len:]
                if new_len == 0:
                    delattr(self, '_TempEmail__th')
                    break
