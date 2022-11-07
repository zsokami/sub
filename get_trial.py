import random
import re
import string
import time
from base64 import b64decode, b64encode
from concurrent.futures import ThreadPoolExecutor
from itertools import chain
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from temp_email_code import get_email, get_email_code
from utils import (new_session, read, read_cfg, remove, remove_illegal, write,
                   write_cfg)

re_non_empty_base64 = re.compile(rb'^(?=[A-Za-z0-9+/]+={0,2}$)(?:.{4})+$')
re_checked_in = re.compile(r'已经?签到')

id = ''.join(random.choices(string.ascii_letters + string.digits, k=12))

print('id:', id)

hosts_cfg = read_cfg('trial_hosts.cfg')
sub_url_cache = read_cfg('trial_sub_url_cache', True)

host_ops = {
    host: dict(zip(ops[::2], ops[1::2]))
    for host, _, *ops in chain(hosts_cfg['v2board'], hosts_cfg['sspanel'])
}

for host in [*sub_url_cache]:
    if host not in host_ops:
        remove(f'trials/{host}')
        del sub_url_cache[host]


def filter_expired(host_cfg):
    now = time.time()
    return [host for host, interval, *_ in host_cfg if host not in sub_url_cache or 'sub_url' not in sub_url_cache[host] or now - float(sub_url_cache[host]['time'][0]) > float(interval)]


reg_v2board_hosts = filter_expired(hosts_cfg['v2board'])
reg_sspanel_hosts = filter_expired(hosts_cfg['sspanel'])


# 注册/登录/解析/下载


def get_sub_url_v2board(host):
    base = 'https://' + host
    session = new_session()
    reg_once = host_ops[host].get('reg_once') == 'T'
    try:
        if not reg_once or 'email' not in sub_url_cache[host]:
            email = f'{id}@{host_ops[host].get("email domain") or "gmail.com"}'
            res = session.post(urljoin(base, 'api/v1/passport/auth/register'), data={
                'email': email,
                'password': id,
                **({'invite_code': host_ops[host]['invite_code']} if 'invite_code' in host_ops[host] else {})
            }).json()

            if 'data' not in res:
                if 'message' not in res or '邮箱验证码' not in res['message']:
                    raise Exception(f'注册失败: {res}')

                email = get_email()
                res = session.post(urljoin(base, 'api/v1/passport/comm/sendEmailVerify'), data={
                    'email': email
                }).json()
                if not res.get('data'):
                    raise Exception(f'发送邮箱验证码失败: {res}')

                email_code = get_email_code(host)
                if not email_code:
                    raise Exception('获取邮箱验证码超时')

                res = session.post(urljoin(base, 'api/v1/passport/auth/register'), data={
                    'email': email,
                    'password': email.split('@')[0],
                    **({'invite_code': host_ops[host]['invite_code']} if 'invite_code' in host_ops[host] else {}),
                    'email_code': email_code
                }).json()
                if 'data' not in res:
                    raise Exception(f'注册失败: {res}')

            if reg_once:
                sub_url_cache[host]['email'] = [email]
            else:
                sub_url_cache[host].pop('email', None)
        else:
            email = sub_url_cache[host]['email'][0]
            res = session.post(urljoin(base, 'api/v1/passport/auth/login'), data={
                'email': email,
                'password': email.split('@')[0],
            }).json()

            if 'data' not in res:
                raise Exception(f'登录失败: {res}')

        token = res['data']['token']

        if 'buy' in host_ops[host]:
            if 'v2board_session' not in session.cookies:
                session.headers['authorization'] = res['data']['auth_data']

            res = session.post(
                urljoin(base, 'api/v1/user/order/save'),
                data=host_ops[host]['buy'],
                headers={'Content-Type': 'application/x-www-form-urlencoded'}
            ).json()
            if 'data' not in res:
                raise Exception(f'下单失败: {res}')

            res = session.post(
                urljoin(base, 'api/v1/user/order/checkout'),
                data=f'trade_no={res["data"]}&{host_ops[host]["checkout"]}',
                headers={'Content-Type': 'application/x-www-form-urlencoded'}
            ).json()
            if not res.get('data'):
                raise Exception(f'结账失败: {res}')

        return None, urljoin(base, 'api/v1/client/subscribe?token=' + token), host
    except Exception as e:
        return e, base, host


def get_sub_url_sspanel(host):
    base = 'https://' + host
    session = new_session()
    reg_once = host_ops[host].get('reg_once') == 'T'
    try:
        if not reg_once or 'email' not in sub_url_cache[host]:
            email = f'{id}@{host_ops[host].get("email domain") or "gmail.com"}'
            res = session.post(urljoin(base, 'auth/register'), data={
                'email': email,
                'passwd': id,
                'repasswd': id,
            }).json()
            if res['ret'] == 0:
                raise Exception(f'注册失败: {res}')

            if reg_once:
                sub_url_cache[host]['email'] = [email]
            else:
                sub_url_cache[host].pop('email', None)
        else:
            email = sub_url_cache[host]['email'][0]

        if 'email' not in session.cookies:
            res = session.post(urljoin(base, 'auth/login'), data={
                'email': email,
                'passwd': email.split('@')[0],
            }).json()
            if res['ret'] == 0:
                raise Exception(f'登录失败: {res}')

        if 'buy' in host_ops[host]:
            res = session.post(
                urljoin(base, 'user/buy'),
                data=host_ops[host]['buy'],
                headers={'Content-Type': 'application/x-www-form-urlencoded'}
            ).json()
            if res['ret'] == 0:
                raise Exception(f'购买失败: {res}')

        if 'checkin' in host_ops[host]:
            res = session.post(urljoin(base, 'user/checkin')).json()
            if res['ret'] == 0:
                if re_checked_in.search(res['msg']):
                    raise Warning(f'[警告] 签到失败: {res}')
                else:
                    raise Exception(f'签到失败: {res}')

        return None, (
            BeautifulSoup(session.get(urljoin(base, 'user')).text, 'html.parser')
            .select_one('[data-clipboard-text]')['data-clipboard-text']
            .split('?')[0] + f'?{host_ops[host].get("sub") or "sub=3"}'
        ), host
    except Exception as e:
        return e, base, host


def download(path, url, host):
    try:
        content = new_session().get(url).content
        if not re_non_empty_base64.fullmatch(content):
            raise Exception('下载失败: not non-empty base64')
        write(path, content)
        return None, path, url, host
    except Exception as e:
        return e, path, url, host


with ThreadPoolExecutor(32) as executor:
    now = time.time()
    for err, url, host in chain(executor.map(get_sub_url_v2board, reg_v2board_hosts), executor.map(get_sub_url_sspanel, reg_sspanel_hosts)):
        if err:
            print(err, url)
        if err and not isinstance(err, Warning):
            sub_url_cache[host]['error(get_sub_url)'] = [remove_illegal(err)]
        else:
            sub_url_cache[host].pop('error(get_sub_url)', None)
            if 'sub_url' not in sub_url_cache[host] or url != sub_url_cache[host]['sub_url'][0]:
                print('new sub url', host, url)
            if 'checkin' in host_ops[host]:
                past = float(host_ops[host]['checkin'])
                update_time = (now - past) // 86400 * 86400 + past
            else:
                update_time = now
            sub_url_cache[host].update(time=[update_time], sub_url=[url])

    for err, path, url, host in executor.map(download, *zip(*((f'trials/{host}', item['sub_url'][0], host) for host, item in sub_url_cache.items() if 'sub_url' in item))):
        if err:
            print(err, host, url)
        if err and not isinstance(err, Warning):
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
