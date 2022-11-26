import json
import os
import re
from base64 import b64decode, b64encode, urlsafe_b64decode, urlsafe_b64encode
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from itertools import chain
from math import log
from random import choices, randint
from string import ascii_lowercase
from threading import RLock
from typing import TypeVar
from urllib.parse import (parse_qs, parse_qsl, quote, unquote_plus, urlencode,
                          urlsplit, urlunsplit)

re_cfg_item_or_k = re.compile(r'^\s*((?:(?: {2,})?[^#;\s](?: ?\S)*)+)', re.MULTILINE)
re_cfg_item_v_sep = re.compile(r' {2,}')
re_cfg_k = re.compile(r'\[(.+?)\]')
re_cfg_illegal = re.compile(r'[\r\n ]+')
re_sort_key = re.compile(r'(\D+)(\d+)')


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


def read_cfg(path=None, text=None, dict_items=False):
    cfg = defaultdict((lambda: defaultdict(list)) if dict_items else list)
    g = cfg['default']
    for m in re_cfg_item_or_k.finditer(text or read(path)):
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
                line = '  '.join(map(_remove_illegal, item)) if isinstance(item, list) else _remove_illegal(item)
                if line:
                    yield line
        elif isinstance(items, dict):
            for k, v in _sort_items(items.items()):
                line = '  '.join(chain([_remove_illegal(k)], map(_remove_illegal, v)
                                 if isinstance(v, list) else [_remove_illegal(v)]))
                if line:
                    yield line
        elif items is not None and item != '':
            yield _remove_illegal(items)

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


def _remove_illegal(v):
    return re_cfg_illegal.sub(' ', str(v).strip())


def _sort_items(items):
    return sorted(items, key=lambda kv: [(s, int(n)) for s, n in re_sort_key.findall(f'a{kv[0]}0')])


################

lock_id = RLock()
id = None


def get_id():
    """随机 id 仅生成一次"""
    global id
    with lock_id:
        if not id:
            id = f'{"".join(choices(ascii_lowercase, k=randint(7, 12)))}{randint(0, 999)}'
            print('id:', id)
    return id


def str2timestamp(s: str):
    if not s:
        return 0
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return float(s)


def timestamp2str(t: float):
    return str(datetime.fromtimestamp(t, timezone(timedelta(hours=8))))


def to_zero(t: float):
    return (t - 16 * 3600) // (24 * 3600) * (24 * 3600) + 16 * 3600


StrOrBytes = TypeVar('StrOrBytes', str, bytes)


def get_name(url: StrOrBytes) -> str:
    if isinstance(url, bytes):
        url = url.decode()
    split = urlsplit(url)
    match split.scheme:
        case 'vmess':
            return json.loads(b64decode(url[8:]).decode())['ps']
        case 'ssr':
            for k, v in parse_qsl(urlsplit('ssr://' + _decode_ssr(url[6:])).query):
                if k == 'remarks':
                    return _decode_ssr(v)
        case _:
            return unquote_plus(split.fragment)
    return ''


def rename(url: StrOrBytes, name: str) -> StrOrBytes:
    is_bytes = isinstance(url, bytes)
    if is_bytes:
        url = url.decode()
    split = urlsplit(url)
    match split.scheme:
        case 'vmess':
            j = json.loads(b64decode(url[8:]).decode())
            j['ps'] = name
            url = url[:8] + b64encode(json.dumps(j, ensure_ascii=False, separators=(',', ':')).encode()).decode()
        case 'ssr':
            split = urlsplit(url[:6] + _decode_ssr(url[6:]))
            q = parse_qs(split.query)
            q['remarks'] = [_encode_ssr(name)]
            split = list(split)
            split[3] = urlencode(q, doseq=True, quote_via=quote)
            url = urlunsplit(split)
            url = url[:6] + _encode_ssr(url[6:])
        case _:
            split = list(split)
            split[-1] = quote(name)
            url = urlunsplit(split)
    return url.encode() if is_bytes else url


def _decode_ssr(en: str):
    return urlsafe_b64decode(en + '=' * (3 - (len(en) - 1) % 4)).decode()


def _encode_ssr(de: str):
    return urlsafe_b64encode(de.encode()).decode().rstrip('=')


def size2str(size):
    size = float(size)
    n = int(size and log(size, 1024))
    return f'{size / 1024 ** n:.4g}{"BKMGTPE"[n]}'
