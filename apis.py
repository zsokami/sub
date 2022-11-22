import json
import os
import re
from queue import Queue
from threading import RLock, Thread
from time import sleep, time
from urllib.parse import unquote_plus, urlencode, urljoin

import requests
from bs4 import BeautifulSoup
from pymailtm import Account, MailTm
from requests.structures import CaseInsensitiveDict
from selenium.webdriver.support.expected_conditions import any_of, title_is
from selenium.webdriver.support.ui import WebDriverWait
from undetected_chromedriver import Chrome, ChromeOptions


class Response:
    def __init__(self, content: bytes, headers: CaseInsensitiveDict[str], status_code: int, reason: str):
        self.content = content
        self.headers = headers
        self.status_code = status_code
        self.reason = reason

    @property
    def text(self):
        if not hasattr(self, '__text'):
            self.__text = self.content.decode()
        return self.__text

    def json(self):
        if not hasattr(self, '__json'):
            self.__json = json.loads(self.text)
        return self.__json

    def bs(self):
        if not hasattr(self, '__bs'):
            self.__bs = BeautifulSoup(self.text, 'html.parser')
        return self.__bs


class Session(requests.Session):
    def __init__(self, host=None):
        super().__init__()
        self.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36'
        self.base = 'https://' + host if host else ''
        self.host = host

    def close(self):
        super().close()
        if hasattr(self, 'chrome'):
            self.chrome.quit()

    def head(self, url, **kwargs) -> Response:
        return super().head(url, **kwargs)

    def get(self, url, **kwargs) -> Response:
        return super().get(url, **kwargs)

    def post(self, url, data=None, **kwargs) -> Response:
        return super().post(url, data, **kwargs)

    def request(self, method, url: str, data=None, **kwargs):
        url = urljoin(self.base, url)
        if not hasattr(self, 'chrome'):
            res = super().request(method, url, data=data, **kwargs)
            res = Response(res.content, res.headers, res.status_code, res.reason)
            if (
                not res.headers['Content-Type'].startswith('text/html')
                or not res.content
                or res.content[0] != 60
                or not res.bs().title
                or res.bs().title.text not in ('Just a moment...', '')
            ):
                return res
            self.get_chrome().get(self.base)
            WebDriverWait(self.chrome, 15).until_not(any_of(title_is('Just a moment...'), title_is('')))
        headers = self.headers.copy()
        del headers['User-Agent']
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
            options = ChromeOptions()
            options.add_argument('--disable-web-security')
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
        self.cookies.clear()
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
        self.cookies.clear()
        res = self.post('api/v1/passport/auth/login', {
            'email': email,
            'password': password or email.split('@')[0]
        }).json()
        self.__set_auth(email, res)
        return res

    def send_email_code(self, email) -> dict:
        return self.post('api/v1/passport/comm/sendEmailVerify', {
            'email': email
        }).json()

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

    def get_sub_info(self, url=None) -> dict | None:
        if not url:
            url = getattr(self, 'sub_url', None) or self.get_sub_url()
        info = self.head(url + '&flag=clash').headers.get('Subscription-Userinfo')
        if info:
            info = dict(kv.split('=') for kv in info.split('; '))
        return info

    def get_sub_content(self, url=None) -> bytes:
        if not url:
            url = getattr(self, 'sub_url', None) or self.get_sub_url()
        return self.get(url).content


class SSPanelSession(Session):
    def register(self, email: str, password=None, email_code=None, invite_code=None) -> dict:
        self.cookies.clear()
        password = password or email.split('@')[0]
        res = self.post('auth/register', {
            'name': password,
            'email': email,
            'passwd': password,
            'repasswd': password,
            **({'email_code': email_code} if email_code else {}),
            **({'invite_code': invite_code} if invite_code else {})
        }).json()
        if res['ret']:
            self.email = email
        return res

    def login(self, email: str = None, password=None) -> dict:
        if not email:
            email = self.email
        if 'email' in self.cookies and email == unquote_plus(self.cookies.get('email')):
            return {'ret': 1}
        self.cookies.clear()
        res = self.post('auth/login', {
            'email': email,
            'passwd': password or email.split('@')[0]
        }).json()
        return res

    def send_email_code(self, email) -> dict:
        return self.post('auth/send', {
            'email': email
        }).json()

    def buy(self, data) -> dict:
        return self.post(
            'user/buy',
            data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        ).json()

    def checkin(self) -> dict:
        return self.post('user/checkin').json()

    def get_sub_url(self, sub=None) -> str:
        doc = self.get('user').bs()
        sub_url = doc.find(attrs={'data-clipboard-text': True})['data-clipboard-text']
        self.sub_url = f'{sub_url[:sub_url.index("?") + 1]}sub={sub or "3"}'
        return self.sub_url

    def get_sub(self, url=None, sub=None) -> tuple[dict, bytes]:
        if not url:
            url = getattr(self, 'sub_url', None) or self.get_sub_url(sub)
        if sub:
            url = f'{url[:url.index("?") + 1]}sub={sub}'
        res = self.get(url)
        info = dict(kv.split('=') for kv in res.headers['Subscription-Userinfo'].split('; '))
        return info, res.content


re_email_code = re.compile(r'(?<!\d)\d{6}(?!\d)')


class TempEmail:
    def __init__(self):
        self.__lock_account = RLock()
        self.__lock = RLock()
        self.__account: Account = None
        self.__queues: list[tuple[str, Queue]] = []
        self.__th: Thread = None
        self.__del = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if not self.del_email():
            print('删除邮箱失败')

    def get_email(self) -> str:
        with self.__lock_account:
            if not (account := self.__account):
                account = self.__account = MailTm().get_account()
                print('temp email:', account.address, account.password)
                self.__del = False
        return account.address

    def del_email(self) -> bool:
        self.__del = True
        while True:
            self.__lock.acquire()
            th = self.__th
            if not th:
                break
            self.__lock.release()
            th.join()
        with self.__lock_account:
            if self.__account:
                succeed = self.__account.delete_account()
                if succeed:
                    self.__account = None
                return succeed
        self.__lock.release()
        return True

    def get_email_code(self, keyword) -> str | None:
        queue = Queue(1)
        with self.__lock:
            self.__queues.append((keyword, queue, time() + 60))
            if not self.__th:
                self.__th = Thread(target=self.__run)
                self.__th.start()
        return queue.get()

    def __run(self):
        while True:
            sleep(1)
            messages = self.__account.get_messages()
            with self.__lock:
                new_len = 0
                for item in self.__queues:
                    keyword, queue, end_time = item
                    for message in messages:
                        if keyword in message.text:
                            m = re_email_code.search(message.text)
                            queue.put(m[0] if m else m)
                            break
                    else:
                        if self.__del or time() > end_time:
                            queue.put(None)
                        else:
                            self.__queues[new_len] = item
                            new_len += 1
                del self.__queues[new_len:]
                if new_len == 0:
                    self.__th = None
                    break
