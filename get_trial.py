import random
import re
import string
from base64 import b64decode, b64encode
from concurrent.futures import ThreadPoolExecutor
from itertools import chain
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


def urls(text):
    return [f'{p or "https:"}//{u}' for p, u in re.findall(r'^\s*([a-z]+:)?[\\/]*([A-Za-z\d]\S+)', text, re.MULTILINE)]


v2board_bases = urls('''
feiniaoyun.top
www.ckcloud.xyz
user.hdapi.work
shan-cloud.xyz
yifei999.com
fastestcloud.xyz
''')
sspanel_bases = urls('''
jsmao.xyz
iacgbt.com
fyy.pw
www.wolaile.icu
paopaocloud.com
''')

id = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
email = id + '@gmail.com'


def new_session():
    session = requests.Session()
    # session.trust_env = False  # 禁用系统代理
    # session.proxies['http'] = '127.0.0.1:7890'
    # session.proxies['https'] = '127.0.0.1:7890'
    return session


def get_sub_url_v2board(base):
    try:
        res = new_session().post(urljoin(base, 'api/v1/passport/auth/register'), json={
            'email': email,
            'password': id,
        }).json()
        return None, urljoin(base, 'api/v1/client/subscribe?token=' + res['data']['token'])
    except Exception as e:
        return e, base


def get_sub_url_sspanel(base):
    session = new_session()
    try:
        res = session.post(urljoin(base, 'auth/register'), json={
            'email': email,
            'passwd': id,
            'repasswd': id,
        }).json()
        if res['ret'] == 0:  # 注册失败
            raise Exception(res)
        if 'email' not in session.cookies:
            res = session.post(urljoin(base, 'auth/login'), json={
                'email': email,
                'passwd': id,
            }).json()
            if res['ret'] == 0:  # 登录失败
                raise Exception(res) 
        return None, (
            BeautifulSoup(session.get(urljoin(base, 'user')).text, 'html.parser')
            .select_one('[data-clipboard-text]')['data-clipboard-text']
            .split('?')[0] + '?sub=3'
        )
    except Exception as e:
        return e, base


def get_nodes_de(sub_url):
    return b64decode(new_session().get(sub_url).content)


executor = ThreadPoolExecutor(len(v2board_bases) + len(sspanel_bases))

sub_urls = []
for err, url in chain(executor.map(get_sub_url_v2board, v2board_bases), executor.map(get_sub_url_sspanel, sspanel_bases)):
    if err:
        print(err, url)
    else:
        sub_urls.append(url)

print(*sub_urls, sep='\n')

nodes_en = b64encode(b''.join(executor.map(get_nodes_de, sub_urls)))

with open('trial', 'wb') as f:
    f.write(nodes_en)
