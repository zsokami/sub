import re
import time
from queue import Queue
from threading import RLock, Thread
from urllib.parse import unquote_plus, urljoin

import requests
from bs4 import BeautifulSoup
from pymailtm import Account, MailTm


class Session(requests.Session):
    def __init__(self, host=None):
        super().__init__()
        # self.trust_env = False  # 禁用系统代理
        # self.proxies['http'] = '127.0.0.1:7890'
        # self.proxies['https'] = '127.0.0.1:7890'
        self.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36'
        self.base = 'https://' + host if host else ''
        self.host = host

    def request(self, method, url, **kwargs):
        return super().request(method, urljoin(self.base, url), **kwargs)

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
        res = self.post('api/v1/passport/auth/register', data={
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
        res = self.post('api/v1/passport/auth/login', data={
            'email': email,
            'password': password or email.split('@')[0]
        }).json()
        self.__set_auth(email, res)
        return res

    def send_email_code(self, email) -> dict:
        return self.post('api/v1/passport/comm/sendEmailVerify', data={
            'email': email
        }).json()

    def order_save(self, data) -> dict:
        return self.post(
            'api/v1/user/order/save',
            data=data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        ).json()

    def order_checkout(self, trade_no, method=None) -> dict:
        return self.post('api/v1/user/order/checkout', data={
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
        res = self.post('auth/register', data={
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
        res = self.post('auth/login', data={
            'email': email,
            'passwd': password or email.split('@')[0]
        }).json()
        return res

    def send_email_code(self, email) -> dict:
        return self.post('auth/send', data={
            'email': email
        }).json()

    def buy(self, data) -> dict:
        return self.post(
            'user/buy',
            data=data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        ).json()

    def checkin(self) -> dict:
        return self.post('user/checkin').json()

    def get_sub_url(self, sub=None) -> str:
        doc = BeautifulSoup(self.get('user').text, 'html.parser')
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
                print('temp email:', account.address)
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
            self.__queues.append((keyword, queue, time.time() + 60))
            if not self.__th:
                self.__th = Thread(target=self.__run)
                self.__th.start()
        return queue.get()

    def __run(self):
        while True:
            time.sleep(1)
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
                        if self.__del or time.time() > end_time:
                            queue.put(None)
                        else:
                            self.__queues[new_len] = item
                            new_len += 1
                del self.__queues[new_len:]
                if new_len == 0:
                    self.__th = None
                    break
