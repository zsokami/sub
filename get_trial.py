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
re_cfg_illegal = re.compile(r'[\r\n ]+')


def read(path, b=False):
    if os.path.isfile(path):
        with open(path, 'rb' if b else 'r') as f:
            return f.read()
    return b'' if b else ''


def write(path, first, *rest):
    os.makedirs(os.path.normpath(os.path.dirname(path)), exist_ok=True)
    with (open(path, 'w', newline='') if isinstance(first, str) else open(path, 'wb')) as f:
        f.write(first)
        f.writelines(rest)


def remove(path):
    if os.path.exists(path):
        os.remove(path)


def read_cfg(path, dict_items=False):
    cfg = defaultdict(dict if dict_items else list)
    g = cfg['default']
    for m in re_cfg_item_or_k.finditer(read(path)):
        vs = re_cfg_item_v_sep.split(m[1])
        m = re_cfg_k.fullmatch(vs[0])
        if m:
            g = cfg[m[1]]
        elif dict_items:
            g[vs[0]] = vs[1:]
        else:
            g.append(vs)
    return cfg


def write_cfg(path, cfg):
    def lines(items):
        if isinstance(items, list):
            for item in items:
                line = '  '.join(map(str, item)) if isinstance(item, list) else str(item)
                if line:
                    yield line
        elif isinstance(items, dict):
            for k, v in items.items():
                line = '  '.join(chain([str(k)], map(str, v) if isinstance(v, list) else [str(v)]))
                if line:
                    yield line
        elif items is not None and item != '':
            yield str(items)

    gs = []
    if isinstance(cfg, dict):
        default = cfg.get('default')
        if default:
            gs.append('\n'.join(lines(default)))
        for k, items in cfg.items():
            if k == 'default':
                continue
            gs.append('\n'.join(chain([f'[{k}]'], lines(items))))
    else:
        gs.append('\n'.join(lines(cfg)))
    write(path, '\n\n'.join(gs), '\n')


def remove_illegal(v):
    return re_cfg_illegal.sub(' ', str(v).strip())


hosts_cfg = read_cfg('trial_hosts.cfg')
sub_url_cache = read_cfg('trial_sub_url_cache', True)

host_set = set(host for host, _ in chain(hosts_cfg['v2board'], hosts_cfg['sspanel']))

for host in [*sub_url_cache]:
    if host not in host_set:
        remove(f'trials/{host}')
        del sub_url_cache[host]


def filter_expired(host_and_intervals):
    now = time.time()
    return [host for host, interval in host_and_intervals if host not in sub_url_cache or now - float(sub_url_cache[host]['time'][0]) > float(interval)]


reg_v2board_hosts = filter_expired(hosts_cfg['v2board'])
reg_sspanel_hosts = filter_expired(hosts_cfg['sspanel'])

id = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
email = id + '@gmail.com'

print('id:', id)


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


def download(path, url, host):
    try:
        content = new_session().get(url).content
        if not content:
            raise Exception('not content')
        write(path, content)
        return None, path, url, host
    except Exception as e:
        return e, path, url, host


executor = ThreadPoolExecutor(max(len(hosts_cfg['v2board']) + len(hosts_cfg['sspanel']), 1))

now = time.time()
for err, url, host in chain(executor.map(get_sub_url_v2board, reg_v2board_hosts), executor.map(get_sub_url_sspanel, reg_sspanel_hosts)):
    if err:
        print(err, url)
        sub_url_cache[host]['error(get_sub_url)'] = remove_illegal(err)
    else:
        print('new sub url', host, url)
        sub_url_cache[host].pop('error(get_sub_url)', None)
        sub_url_cache[host].update(time=now, sub_url=url)

for err, path, url, host in executor.map(download, *zip((f'trials/{host}', item['sub_url'], host) for host, item in sub_url_cache.items() if 'sub_url' in item)):
    if err:
        err = f'下载失败: {err}'
        print(err, host, url)
        sub_url_cache[host]['error(download)'] = remove_illegal(err)
    else:
        sub_url_cache[host].pop('error(download)', None)

nodes_de = []
for host, item in sub_url_cache.items():
    content = b64decode(read(f'trials/{host}', True))
    if content:
        item['node_n'] = content.count(b'\n')
        nodes_de.append(content)

write('trial', b64encode(b''.join(nodes_de)))

write_cfg('trial_sub_url_cache', sub_url_cache)
