import random
import string
from base64 import b64decode, b64encode
from concurrent.futures import ThreadPoolExecutor
from itertools import chain
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

v2board_bases = [
    'https://feiniaoyun.top',
    'https://www.ckcloud.xyz',
    'https://user.hdapi.work',
    'https://shan-cloud.xyz',
    'https://yifei999.com',
]
sspanel_bases = [
    'https://jsmao.xyz',
    # 'https://paopaocloud.com',
]

id = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
email = id + '@gmail.com'

session = requests.session()
# session.trust_env = False  # 禁用系统代理
# session.proxies['http'] = '127.0.0.1:7890'
# session.proxies['https'] = '127.0.0.1:7890'


def get_sub_url_v2board(base):
    try:
        res = session.post(urljoin(base, 'api/v1/passport/auth/register'), json={
            'email': email,
            'password': id,
        }).json()
        return urljoin(base, 'api/v1/client/subscribe?token=' + res['data']['token'])
    except:
        return None


def get_sub_url_sspanel(base):
    try:
        if (
            session.post(urljoin(base, 'auth/register'), json={
                'email': email,
                'passwd': id,
                'repasswd': id,
            }).json()['ret'] == 0  # 注册失败
        ) or (
            'email' not in session.cookies and
            session.post(urljoin(base, 'auth/login'), json={
                'email': email,
                'passwd': id,
            }).json()['ret'] == 0  # 登录失败
        ):
            raise
        return (
            BeautifulSoup(session.get(urljoin(base, 'user')).text, 'html.parser')
            .select_one('[data-clipboard-text]')['data-clipboard-text']
            .split('?')[0] + '?sub=3'
        )

    except:
        return None


def get_nodes_de(sub_url):
    return b64decode(session.get(sub_url).content)


executor = ThreadPoolExecutor(len(v2board_bases) + len(sspanel_bases))

sub_urls = [
    url for url in chain(
        executor.map(get_sub_url_v2board, v2board_bases),
        executor.map(get_sub_url_sspanel, sspanel_bases)
    ) if url
]

print(*sub_urls, sep='\n')

nodes_en = b64encode(b''.join(executor.map(get_nodes_de, sub_urls)))

with open('trial', 'wb') as f:
    f.write(nodes_en)
