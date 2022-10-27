import os
import random
import re
import string
import time
from base64 import b64decode, b64encode
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from itertools import chain
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

re_cfg_item_or_k = re.compile(r'^\s*((?:(?: {2,})?[^#\s](?: ?\S)*)+)', re.MULTILINE)
re_cfg_item_v_sep = re.compile(r' {2,}')
re_cfg_k = re.compile(r'\[(.+?)\]')


def read(path, b=False):
    path = os.path.normpath(path)
    if os.path.isfile(path):
        with open(path, 'rb' if b else 'r') as f:
            return f.read()
    return b'' if b else ''


def write(path, first, *rest):
    path = os.path.normpath(path)
    os.makedirs(os.path.normpath(os.path.dirname(path)), exist_ok=True)
    with (open(path, 'w', newline='') if isinstance(first, str) else open(path, 'wb')) as f:
        f.write(first)
        f.writelines(rest)


def read_cfg(path):
    cfg = defaultdict(list)
    g = cfg['default']
    for m in re_cfg_item_or_k.finditer(read(path)):
        vs = re_cfg_item_v_sep.split(m[1])
        m = re_cfg_k.fullmatch(vs[0])
        if m:
            g = cfg[m[1]]
        else:
            g.append(vs)
    return cfg


def write_cfg(path, cfg):
    gs = []
    default = cfg.get('default')
    if default:
        gs.append('\n'.join('  '.join(map(str, item)) for item in default))
    for k, items in cfg.items():
        if k == 'default':
            continue
        gs.append('\n'.join(chain([f'[{k}]'], ('  '.join(map(str, item)) for item in items))))
    write(path, '\n\n'.join(gs), '\n')


hosts_cfg = read_cfg('trial_hosts.cfg')
last_update_time = read_cfg('trial_last_update_time')


def filter_expired(host_and_intervals):
    now = time.time()
    return [host for host, interval in host_and_intervals if host not in last_update_time or now - float(last_update_time[host][0]) > float(interval)]


v2board_hosts = filter_expired(hosts_cfg['v2board'])
sspanel_hosts = filter_expired(hosts_cfg['sspanel'])

id = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
email = id + '@gmail.com'


def new_session():
    session = requests.Session()
    # session.trust_env = False  # 禁用系统代理
    # session.proxies['http'] = '127.0.0.1:7890'
    # session.proxies['https'] = '127.0.0.1:7890'
    return session


def get_sub_url_v2board(host):
    base = 'https://' + host
    try:
        res = new_session().post(urljoin(base, 'api/v1/passport/auth/register'), json={
            'email': email,
            'password': id,
        }).json()
        try:
            token = res['data']['token']
        except KeyError:
            raise Exception(f'注册失败: {res}')
        return None, urljoin(base, 'api/v1/client/subscribe?token=' + token), host
    except Exception as e:
        return e, base, host


def get_sub_url_sspanel(host):
    base = 'https://' + host
    session = new_session()
    try:
        res = session.post(urljoin(base, 'auth/register'), json={
            'email': email,
            'passwd': id,
            'repasswd': id,
        }).json()
        if res['ret'] == 0:
            raise Exception(f'注册失败: {res}')
        if 'email' not in session.cookies:
            res = session.post(urljoin(base, 'auth/login'), json={
                'email': email,
                'passwd': id,
            }).json()
            if res['ret'] == 0:
                raise Exception(f'登录失败: {res}')
        return None, (
            BeautifulSoup(session.get(urljoin(base, 'user')).text, 'html.parser')
            .select_one('[data-clipboard-text]')['data-clipboard-text']
            .split('?')[0] + '?sub=3'
        ), host
    except Exception as e:
        return e, base, host


def download(path, url):
    try:
        content = new_session().get(url).content
        if not content:
            raise Exception('not content')
        write(path, content)
        return None, path, url
    except Exception as e:
        return e, path, url


executor = ThreadPoolExecutor(len(v2board_hosts) + len(sspanel_hosts))

path_and_sub_urls = []
now = time.time()
for err, url, host in chain(executor.map(get_sub_url_v2board, v2board_hosts), executor.map(get_sub_url_sspanel, sspanel_hosts)):
    if err:
        print(err, url)
    else:
        path_and_sub_urls.append((f'trials/{host}', url))
        last_update_time[host] = [[now]]

ok_path_and_sub_urls = []
for err, path, url in executor.map(download, *zip(*path_and_sub_urls)):
    if err:
        print(f'下载失败: {err}', path, url)
    else:
        ok_path_and_sub_urls.append((path, url))

for path_and_sub_url in ok_path_and_sub_urls:
    print(*path_and_sub_url)

nodes_de = []
for host, interval in chain(hosts_cfg['v2board'], hosts_cfg['sspanel']):
    nodes_de.append(b64decode(read(f'trials/{host}', True)))

write('trial', b64encode(b''.join(nodes_de)))
write_cfg('trial_last_update_time', last_update_time)
