import os
import re
from base64 import b64decode, b64encode
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from itertools import chain
from operator import itemgetter
from random import choice
from time import time

from apis import Session, SSPanelSession, TempEmail, V2BoardSession
from subconverter import gen_clash_config, get, parse_node_groups
from utils import (clear_files, get_id, list_file_paths, read, read_cfg, remove, size2str,
                   str2timestamp, timestamp2str, to_zero, write, write_cfg)

re_non_empty_base64 = re.compile(rb'^(?=[A-Za-z0-9+/]+={0,2}$)(?:.{4})+$')
re_checked_in = re.compile(r'已经?签到')

temp_email = TempEmail()


# 注册/登录/解析/下载


def get_sub(opt: dict, cache: dict[str, list[str]]):
    url = cache['sub_url'][0]
    suffix = ' - ' + opt['name']
    if 'speed_limit' in opt:
        suffix += ' ⚠️限速 ' + opt['speed_limit']
    return get(url, suffix)


def should_turn(opt: dict, cache: dict[str, list[str]]):
    if 'sub_url' not in cache:
        return True

    now = time()
    info, *rest = get_sub(opt, cache)

    return (
        not info
        or opt.get('turn') == 'always'
        or float(info['total']) - (float(info['upload']) + float(info['download'])) < (1 << 27)
        or (opt.get('expire') != 'never' and info.get('expire') and str2timestamp(info.get('expire')) - now < ((now - str2timestamp(cache['time'][0])) / 7 if 'reg_limit' in opt else 1600))
    ), info, *rest


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
        res = session.register(email := f'{get_id()}@gmail.com', **kwargs)
    except Exception as e:
        raise Exception(f'发送注册请求失败({email}): {e}')
    if is_reg_ok(res, s_key, m_key):
        return

    if '邮箱后缀' in res[m_key]:
        try:
            res = session.register(email := f'{get_id()}@qq.com', **kwargs)
        except Exception as e:
            raise Exception(f'发送注册请求失败({email}): {e}')
        if is_reg_ok(res, s_key, m_key):
            return

    if '邮箱验证码' in res[m_key]:
        res = session.send_email_code(email := temp_email.get_email())
        if not res.get(s_key):
            raise Exception(f'发送邮箱验证码失败({email}): {res}')

        email_code = temp_email.get_email_code(opt['name'])
        if not email_code:
            raise Exception(f'获取邮箱验证码失败({email})')

        res = session.register(email, email_code=email_code, **kwargs)
        if is_reg_ok(res, s_key, m_key):
            return

    raise Exception(f'注册失败({email}): {res}{" " + kwargs.get("invite_code") if "邀请" in res[m_key] else ""}')


def try_checkin(session: SSPanelSession, opt: dict, cache: dict[str, list[str]], log: list):
    if opt.get('checkin') == 'T' and cache.get('email'):
        if len(cache['last_checkin']) < len(cache['email']):
            cache['last_checkin'] += ['0'] * (len(cache['email']) - len(cache['last_checkin']))
        last_checkin = to_zero(str2timestamp(cache['last_checkin'][0]))
        now = time()
        if now - last_checkin > 24.5 * 3600:
            try:
                res = session.login(cache['email'][0])
                if not res.get('ret'):
                    raise Exception(f'登录失败: {res}')
                res = session.checkin()
                if not (res.get('ret') or ('msg' in res and re_checked_in.search(res['msg']))):
                    raise Exception(f'签到失败: {res}')
                cache['last_checkin'][0] = timestamp2str(now)
                cache.pop('尝试签到失败', None)
            except Exception as e:
                cache['尝试签到失败'] = [e]
                log.append(f'尝试签到失败({host}): {e}')
    else:
        cache.pop('last_checkin', None)


def do_turn(session: V2BoardSession | SSPanelSession, opt: dict, cache: dict[str, list[str]], log: list) -> bool:
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

    if isinstance(session, V2BoardSession):
        if 'buy' in opt:
            res = session.order_save(opt['buy'])
            if 'data' not in res:
                raise Exception(f'下单失败: {res}')

            res = session.order_checkout(res['data'])
            if not res.get('data'):
                raise Exception(f'结账失败: {res}')

        cache['sub_url'] = [session.get_sub_url()]
    else:
        if 'buy' in opt:
            res = session.buy(opt['buy'])
            if not res.get('ret'):
                raise Exception(f'购买失败: {res}')

        try_checkin(session, opt, cache, log)

        cache['sub_url'] = [session.get_sub_url({k: opt[k] for k in opt.keys() & ('sub', 'clash')})]

    cache['time'] = [timestamp2str(time())]
    log.append(f'更新订阅链接{"(新注册)" if is_new_reg else ""}({session.host}) {cache["sub_url"][0]}')


def try_turn(session: V2BoardSession | SSPanelSession, opt: dict, cache: dict[str, list[str]], log: list):
    cache.pop('更新旧订阅失败', None)
    cache.pop('更新订阅链接失败', None)
    cache.pop('获取订阅失败', None)

    try:
        turn, *sub = should_turn(opt, cache)
    except Exception as e:
        cache['更新旧订阅失败'] = [e]
        log.append(f'更新旧订阅失败({session.host})({cache["sub_url"][0]}): {e}')
        return None

    if turn:
        try:
            do_turn(session, opt, cache, log)
        except Exception as e:
            cache['更新订阅链接失败'] = [e]
            log.append(f'更新订阅链接失败({session.host}): {e}')
            return sub
        try:
            sub = get_sub(opt, cache)
        except Exception as e:
            cache['获取订阅失败'] = [e]
            log.append(f'获取订阅失败({host})({cache["sub_url"][0]}): {e}')

    return sub


def cache_sub_info(info, opt: dict, cache: dict[str, list[str]]):
    if not info:
        raise Exception('no sub info')
    used = float(info["upload"]) + float(info["download"])
    total = float(info["total"])
    rest = ' (剩余 ' + size2str(total - used)
    if opt.get('expire') == 'never' or not info.get('expire'):
        expire = '永不过期'
    else:
        ts = str2timestamp(info['expire'])
        expire = timestamp2str(ts)
        rest += ' ' + str(timedelta(seconds=ts - time()))
    rest += ')'
    cache['sub_info'] = [size2str(used), size2str(total), expire, rest]


def save_sub_base64(base64, host):
    if not re_non_empty_base64.fullmatch(base64):
        raise Exception('no base64' if base64 else 'no content')
    write(f'trials/{host}', base64)


def save_sub_clash(clash, host):
    gen_clash_config(f'trials/{host}.yaml', f'trials_providers/{host}', *parse_node_groups(clash))


def save_sub(info, base64, clash, base64_url, clash_url, host, opt: dict, cache: dict[str, list[str]]):
    cache.pop('保存订阅信息失败', None)
    cache.pop('保存base64订阅失败', None)
    cache.pop('保存clash订阅失败', None)

    try:
        cache_sub_info(info, opt, cache)
    except Exception as e:
        cache['保存订阅信息失败'] = [e]
        log.append(f'保存订阅信息失败({host})({clash_url}): {e}')
    try:
        save_sub_base64(base64, host)
    except Exception as e:
        cache['保存base64订阅失败'] = [e]
        log.append(f'保存base64订阅失败({host})({base64_url}): {e}')
    try:
        save_sub_clash(clash, host)
    except Exception as e:
        cache['保存clash订阅失败'] = [e]
        log.append(f'保存clash订阅失败({host})({clash_url}): {e}')


def get_and_save(session: V2BoardSession | SSPanelSession, opt: dict, cache: dict[str, list[str]], log: list):
    sub = try_turn(session, opt, cache, log)
    if sub:
        save_sub(*sub, session.host, opt, cache)


def get_nodes_v2board(host, opt: dict, cache: dict[str, list[str]]):
    log = []
    session = V2BoardSession(host)
    get_and_save(session, opt, cache, log)
    return log


def get_nodes_sspanel(host, opt: dict, cache: dict[str, list[str]]):
    log = []
    session = SSPanelSession(host)
    try_checkin(session, opt, cache, log)
    get_and_save(session, opt, cache, log)
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

cache = read_cfg('trial.cache', dict_items=True)

for host in [*cache]:
    if host not in opt:
        remove(f'trials/{host}')
        del cache[host]

with ThreadPoolExecutor(32) as executor:
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

nodes, total_node_n = b'', 0
unlimited_speed_nodes, unlimited_speed_node_n = b'', 0
providers_map = {}
unlimited_speed_nodes_providers_map = {}

for host, _opt in opt.items():
    def read_and_merge_into(map):
        for path in list_file_paths(f'trials_providers/{host}'):
            name = os.path.basename(path)
            s = read(path, True)
            if name in map:
                map[name] += s[s.index(b'proxies:\n') + 9:]
            else:
                map[name] = s

    cur_nodes = b64decode(read(f'trials/{host}', True)).splitlines()
    node_n = len(cur_nodes)
    if (d := node_n - (int(cache[host]['node_n'][0]) if 'node_n' in cache[host] else 0)) != 0:
        print(f'{host} 节点数 {"+" if d > 0 else ""}{d} ({node_n})')
    cache[host]['node_n'] = node_n
    nodes += cur_nodes
    read_and_merge_into(providers_map)
    total_node_n += node_n
    if 'speed_limit' not in _opt:
        unlimited_speed_nodes += cur_nodes
        read_and_merge_into(unlimited_speed_nodes_providers_map)
        unlimited_speed_node_n += node_n


print('总节点数', total_node_n)
print('不限速节点数', unlimited_speed_node_n)

write_cfg('trial.cache', cache)

write('trial', b64encode(nodes))
write('trial_unlimited_speed', b64encode(unlimited_speed_nodes))


def gen_trial_yaml(config_path, providers_dir, map):
    clear_files(providers_dir)
    for k, v in map.items():
        write(f'{providers_dir}/{k}', v)
    gen_clash_config(config_path, providers_dir)


gen_trial_yaml('trial.yaml', 'trials_providers', providers_map)
gen_trial_yaml('trial_unlimited_speed.yaml', 'trials_providers/unlimited_speed', unlimited_speed_nodes_providers_map)
