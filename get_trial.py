import random
import re
import string
import time
from base64 import b64decode, b64encode
from concurrent.futures import ThreadPoolExecutor
from itertools import chain
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from utils import (new_session, read, read_cfg, remove, remove_illegal, write,
                   write_cfg)

re_non_empty_base64 = re.compile(rb'^(?=[A-Za-z0-9+/]+={0,2}$)(?:.{4})+$')

id = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
email = id + '@gmail.com'

print('id:', id)


# 注册/登录/解析/下载


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
        if not re_non_empty_base64.fullmatch(content):
            raise Exception('not non-empty base64')
        write(path, content)
        return None, path, url, host
    except Exception as e:
        return e, path, url, host


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

with ThreadPoolExecutor(32) as executor:
    now = time.time()
    for err, url, host in chain(executor.map(get_sub_url_v2board, reg_v2board_hosts), executor.map(get_sub_url_sspanel, reg_sspanel_hosts)):
        if err:
            print(err, url)
            sub_url_cache[host]['error(get_sub_url)'] = [remove_illegal(err)]
        else:
            print('new sub url', host, url)
            sub_url_cache[host].pop('error(get_sub_url)', None)
            sub_url_cache[host].update(time=[now], sub_url=[url])

    for err, path, url, host in executor.map(download, *zip(*((f'trials/{host}', item['sub_url'][0], host) for host, item in sub_url_cache.items() if 'sub_url' in item))):
        if err:
            err = f'下载失败: {err}'
            print(err, host, url)
            sub_url_cache[host]['error(download)'] = [remove_illegal(err)]
        else:
            sub_url_cache[host].pop('error(download)', None)

all_nodes = b''
for host, item in sub_url_cache.items():
    nodes = b64decode(read(f'trials/{host}', True))
    item['node_n'] = [nodes.count(b'\n')]
    all_nodes += nodes

write('trial', b64encode(all_nodes))

write_cfg('trial_sub_url_cache', sub_url_cache)
