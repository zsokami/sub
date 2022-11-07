import os
import random
import re
import string
from collections import defaultdict
from itertools import chain
from threading import RLock

import requests

re_cfg_item_or_k = re.compile(r'^\s*((?:(?: {2,})?[^#\s](?: ?\S)*)+)', re.MULTILINE)
re_cfg_item_v_sep = re.compile(r' {2,}')
re_cfg_k = re.compile(r'\[(.+?)\]')
re_cfg_illegal = re.compile(r'[\r\n ]+')


lock_id = RLock()
id = None


# 文件读写删


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


# 自定义配置文件读写


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


# 创建会话


def new_session():
    session = requests.Session()
    # session.trust_env = False  # 禁用系统代理
    # session.proxies['http'] = '127.0.0.1:7890'
    # session.proxies['https'] = '127.0.0.1:7890'
    session.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36'
    return session


# 随机 id 仅生成一次


def get_id():
    global id
    with lock_id:
        if not id:
            id = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
            print('id:', id)
    return id
