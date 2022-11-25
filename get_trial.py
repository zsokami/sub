import re
from base64 import b64decode, b64encode
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from itertools import chain
from operator import itemgetter
from random import choice
from time import time

from apis import Session, SSPanelSession, TempEmail, V2BoardSession
from utils import (get_id, get_name, read, read_cfg, remove, rename, size2str,
                   str2timestamp, timestamp2str, to_zero, write, write_cfg)

re_non_empty_base64 = re.compile(rb'^(?=[A-Za-z0-9+/]+={0,2}$)(?:.{4})+$')
re_checked_in = re.compile(r'已经?签到')
re_exclude = re.compile(r'剩余流量|套餐到期|过期时间|重置')

subconverters = [row[0] for row in read_cfg('subconverters.cfg')['default']]


# 注册/登录/解析/下载


def should_turn(sub_info: dict, now, opt: dict, cache: dict[str, list[str]]):
    return (
        not sub_info
        or opt.get('turn') == 'always'
        or float(sub_info['total']) - (used := float(sub_info['upload']) + float(sub_info['download'])) < (used / 3 if 'reg_limit' in opt else (1 << 27))
        or (opt.get('expire') != 'never' and sub_info.get('expire') and str2timestamp(sub_info.get('expire')) - now < (now - str2timestamp(cache['time'][0]) if 'reg_limit' in opt else 3600))
    )


def check_and_write_content(host, content):
    if not re_non_empty_base64.fullmatch(content):
        raise Exception('no base64' if content else 'no content')
    nodes = b''
    node_set = set()
    for node in b64decode(content).splitlines():
        if not (node in node_set or re_exclude.search(get_name(node))):
            node_set.add(node)
            nodes += node + b'\n'
    write(f'trials/{host}', b64encode(nodes))


def cache_sub_info(sub_info, opt: dict, cache: dict[str, list[str]]):
    if not sub_info:
        raise Exception('no sub_info')
    used = float(sub_info["upload"]) + float(sub_info["download"])
    total = float(sub_info["total"])
    rest = ' (剩余 ' + size2str(total - used)
    if opt.get('expire') == 'never' or not sub_info.get('expire'):
        expire = '永不过期'
    else:
        ts = str2timestamp(sub_info['expire'])
        expire = timestamp2str(ts)
        rest += ' ' + str(timedelta(seconds=ts - time()))
    rest += ')'
    cache['sub_info'] = [size2str(used), size2str(total), expire, rest]


def is_reg_ok(res: dict, s_key, m_key):
    if res.get(s_key):
        return True
    if m_key in res:
        return False
    raise Exception(f'注册失败: {res}')


def register(session: V2BoardSession | SSPanelSession, opt: dict):
    s_key, m_key = ('data', 'message') if isinstance(session, V2BoardSession) else ('ret', 'msg')
    kwargs = {k: opt[k] for k in opt.keys() & ('name_eq_email', 'reg_fmt')}
    invite_code = opt.get('invite_code')
    if isinstance(invite_code, str):
        kwargs['invite_code'] = choice(invite_code.split())
    try:
        res = session.register(get_id() + '@gmail.com', **kwargs)
    except Exception as e:
        raise Exception(f'发送注册请求失败: {e}')
    if is_reg_ok(res, s_key, m_key):
        return

    if '邮箱后缀' in res[m_key]:
        try:
            res = session.register(get_id() + '@qq.com', **kwargs)
        except Exception as e:
            raise Exception(f'发送注册请求失败: {e}')
        if is_reg_ok(res, s_key, m_key):
            return

    if '邮箱验证码' in res[m_key]:
        res = session.send_email_code(temp_email.get_email())
        if not res.get(s_key):
            raise Exception(f'发送邮箱验证码失败: {res}')

        email_code = temp_email.get_email_code(opt['name'])
        if not email_code:
            raise Exception('获取邮箱验证码失败')

        res = session.register(temp_email.get_email(), email_code=email_code, **kwargs)
        if is_reg_ok(res, s_key, m_key):
            return

    raise Exception(f'注册失败: {res}{" " + kwargs.get("invite_code") if "邀请" in res[m_key] else ""}')


def try_checkin(session: SSPanelSession, opt: dict, cache: dict[str, list[str]]):
    if opt.get('checkin') == 'T' and cache.get('email'):
        if len(cache['last_checkin']) < len(cache['email']):
            cache['last_checkin'] += ['0'] * (len(cache['email']) - len(cache['last_checkin']))
        last_checkin = to_zero(str2timestamp(cache['last_checkin'][0]))
        now = time()
        if now - last_checkin > 24.5 * 3600:
            res = session.login(cache['email'][0])
            if not res.get('ret'):
                raise Exception(f'登录失败: {res}')
            res = session.checkin()
            if not (res.get('ret') or ('msg' in res and re_checked_in.search(res['msg']))):
                raise Exception(f'签到失败: {res}')
            cache['last_checkin'][0] = timestamp2str(now)
    else:
        cache.pop('last_checkin', None)


def do_turn(session: V2BoardSession | SSPanelSession, opt: dict, cache: dict[str, list[str]]) -> bool:
    is_new_reg = False
    reg_limit = opt.get('reg_limit')
    if not reg_limit:
        register(session, opt)
        is_new_reg = True
        cache['email'] = [session.email]
        if opt.get('checkin') == 'T':
            cache['last_checkin'] = ['0']
    else:
        if len(cache['email']) < int(reg_limit):
            register(session, opt)
            is_new_reg = True
            cache['email'].append(session.email)
            if opt.get('checkin') == 'T':
                cache['last_checkin'] += ['0'] * (len(cache['email']) - len(cache['last_checkin']))
        elif len(cache['email']) > int(reg_limit):
            del cache['email'][:-int(reg_limit)]
            if opt.get('checkin') == 'T':
                del cache['last_checkin'][:-int(reg_limit)]

        cache['email'] = cache['email'][-1:] + cache['email'][:-1]
        if opt.get('checkin') == 'T':
            cache['last_checkin'] = cache['last_checkin'][-1:] + cache['last_checkin'][:-1]

    try:
        res = session.login(cache['email'][0])
    except Exception as e:
        raise Exception(f'发送登录请求失败: {e}')
    if not res.get('data' if isinstance(session, V2BoardSession) else 'ret'):
        raise Exception(f'登录失败: {res}')
    return is_new_reg


def get_nodes_v2board(host, opt: dict, cache: dict[str, list[str]]):
    log = []
    session = V2BoardSession(host)
    turn = True

    try:
        if 'sub_url' in cache:
            now = time()
            try:
                sub_info = session.get_sub_info(cache['sub_url'][0])
            except Exception as e:
                raise Exception(f'更新订阅信息失败: {e}')
            turn = should_turn(sub_info, now, opt, cache)

        if turn:
            is_new_reg = do_turn(session, opt, cache)
            if 'buy' in opt:
                res = session.order_save(opt['buy'])
                if 'data' not in res:
                    raise Exception(f'下单失败: {res}')

                res = session.order_checkout(res['data'])
                if not res.get('data'):
                    raise Exception(f'结账失败: {res}')

            cache['sub_url'] = [session.get_sub_url()]
            cache['time'] = [timestamp2str(time())]
            log.append(f'更新订阅链接{"(新注册)" if is_new_reg else ""} {cache["sub_url"][0]}')

        cache.pop('更新订阅链接失败', None)
    except Exception as e:
        cache['更新订阅链接失败'] = [e]
        log.append(f'更新订阅链接失败 {host} {e}')

    if 'sub_url' in cache:
        try:
            check_and_write_content(host, session.get_sub_content(cache['sub_url'][0]))
            cache.pop('更新订阅失败', None)
        except Exception as e:
            cache['更新订阅失败'] = [e]
            log.append(f'更新订阅失败 {host} {cache["sub_url"][0]} {e}')

        try:
            cache_sub_info(session.get_sub_info(cache['sub_url'][0]) if turn else sub_info, opt, cache)
            cache.pop('更新订阅信息失败', None)
        except Exception as e:
            cache['更新订阅信息失败'] = [e]
            log.append(f'更新订阅信息失败 {host} {cache["sub_url"][0]} {e}')

    return log


def cvt_to_b64_sub_url_if_clash(sub_url: str):
    if 'clash' in sub_url[sub_url.index('?') + 1:]:
        return f'https://{choice(subconverters)}/sub?target=mixed&emoji=false&url={sub_url}'
    return sub_url


def get_nodes_sspanel(host, opt: dict, cache: dict[str, list[str]]):
    log = []
    session = SSPanelSession(host)
    turn = True

    try:
        try_checkin(session, opt, cache)
        cache.pop('尝试签到失败', None)
    except Exception as e:
        cache['尝试签到失败'] = [e]
        log.append(f'尝试签到失败 {host} {e}')

    try:
        if 'sub_url' in cache:
            now = time()
            sub_url = cvt_to_b64_sub_url_if_clash(cache['sub_url'][0])
            try:
                sub_info, sub_content = session.get_sub(sub_url)
            except Exception as e:
                raise Exception(f'更新订阅失败: {e}')
            turn = should_turn(sub_info, now, opt, cache)

        if turn:
            is_new_reg = do_turn(session, opt, cache)
            if 'buy' in opt:
                res = session.buy(opt['buy'])
                if not res.get('ret'):
                    raise Exception(f'购买失败: {res}')

            try:
                try_checkin(session, opt, cache)
                cache.pop('尝试签到失败', None)
            except Exception as e:
                cache['尝试签到失败'] = [e]
                log.append(f'尝试签到失败 {host} {e}')

            cache['sub_url'] = [session.get_sub_url({k: opt[k] for k in opt.keys() & ('sub', 'clash')})]
            cache['time'] = [timestamp2str(time())]
            log.append(f'更新订阅链接{"(新注册)" if is_new_reg else ""} {cache["sub_url"][0]}')

        cache.pop('更新订阅链接失败', None)
    except Exception as e:
        cache['更新订阅链接失败'] = [e]
        log.append(f'更新订阅链接失败 {host} {e}')

    if 'sub_url' in cache:
        try:
            if turn:
                sub_url = cvt_to_b64_sub_url_if_clash(cache['sub_url'][0])
                sub_info, sub_content = session.get_sub(sub_url)
            check_and_write_content(host, sub_content)
            cache_sub_info(sub_info, opt, cache)
            cache.pop('更新订阅失败', None)
        except Exception as e:
            cache['更新订阅失败'] = [e]
            log.append(f'更新订阅失败 {host} {sub_url} {e}')

    return log


def get_ip_info():
    try:
        return ['  '.join(Session().get_ip_info())]
    except Exception as e:
        return [f'获取 ip 信息失败 {e}']


cfg = read_cfg('trial.cfg')

opt = {
    host: dict(zip(opt[::2], opt[1::2]))
    for host, *opt in chain(cfg['v2board'], cfg['sspanel'])
}

cache = read_cfg('trial.cache', True)

for host in [*cache]:
    if host not in opt:
        remove(f'trials/{host}')
        del cache[host]

with ThreadPoolExecutor(32) as executor, TempEmail() as temp_email:
    f_ip_info = executor.submit(get_ip_info)
    hosts = [host for host, *_ in cfg['v2board']]
    getter = itemgetter(*hosts)
    m_v2board = executor.map(get_nodes_v2board, hosts, getter(opt), getter(cache))
    hosts = [host for host, *_ in cfg['sspanel']]
    getter = itemgetter(*hosts)
    m_sspanel = executor.map(get_nodes_sspanel, hosts, getter(opt), getter(cache))

    for log in chain((f.result() for f in [f_ip_info]), m_v2board, m_sspanel):
        for line in log:
            print(line)

nodes = b''
total_node_n = 0
for host, _opt in opt.items():
    suffix = _opt["name"]
    cur_nodes = b64decode(read(f'trials/{host}', True)).splitlines()
    node_n = len(cur_nodes)
    for node in cur_nodes:
        nodes += rename(node, f'{get_name(node)} - {suffix}') + b'\n'
    if (d := node_n - (int(cache[host]['node_n'][0]) if 'node_n' in cache[host] else 0)) != 0:
        print(f'{host} 节点数 {"+" if d > 0 else ""}{d} ({node_n})')
    cache[host]['node_n'] = node_n
    total_node_n += node_n

print('总节点数', total_node_n)
write('trial', b64encode(nodes))
write_cfg('trial.cache', cache)
