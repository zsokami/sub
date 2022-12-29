import os
from copy import deepcopy
from random import choice
from time import time
from urllib.parse import quote, urljoin

from ruamel.yaml import YAML, CommentedMap

from apis import Session
from utils import clear_files, list_file_paths, read, read_cfg, write

GITHUB_REPOSITORY = os.getenv('GITHUB_REPOSITORY')

subconverters = [row[0] for row in read_cfg('subconverters.cfg')['default']]
exclude_en = quote('Traffic|Expire|剩余流量|到期|时间|重置|官.?网|官方|产品|平台|勿连|修复|更新|地址|网站|网址|售后|客服|联系|使用|购买|公告')


def _yaml():
    yaml = YAML()
    yaml.version = (1, 1)
    yaml.width = float('inf')
    return yaml


base_cfg: CommentedMap = read('base.yaml', reader=_yaml().load)
group_to_provider_map = {g['name']: g['use'][0] for g in base_cfg['proxy-groups'] if 'use' in g}


def get(url, suffix=None):
    seesion = Session(choice(subconverters), user_agent='ClashforWindows')
    params = f'exclude={exclude_en}&url=' + quote(f'{url}#{time()}')
    if suffix:
        params += '&rename=' + quote(f'$@{suffix}')
    res = seesion.get(
        clash_url := f'sub?target=clash&udp=true&scv=true&expand=false&classic=true&{params}'
        '&config=https://raw.githubusercontent.com/zsokami/ACL4SSR/main/ACL4SSR_Online_Full_Mannix.ini'
    )
    info = res.headers.get('subscription-userinfo')
    if info:
        info = dict(kv.split('=') for kv in info.split('; '))
    clash = res.content
    res = seesion.get(
        base64_url := f'sub?target=mixed&{params}'
        '&config=https://raw.githubusercontent.com/zsokami/ACL4SSR/main/ACL4SSR_Online_Full_Mannix.ini'
    )
    base64 = res.content
    return info, base64, clash, urljoin(seesion.base, base64_url), urljoin(seesion.base, clash_url)


def parse_node_groups(clash):
    cfg = _yaml().load(clash)
    name_to_node_map = {p['name']: p for p in cfg['proxies']}
    provider_node_names_map = {}
    for g in cfg['proxy-groups']:
        name, proxies = g['name'], g['proxies']
        if (
            name in group_to_provider_map
            and group_to_provider_map[name] not in provider_node_names_map
            and proxies[0] != 'DIRECT'
        ):
            provider_node_names_map[group_to_provider_map[name]] = tuple(proxies)
    return name_to_node_map, provider_node_names_map


def gen_clash_config(config_path, providers_dir, name_to_node_map=None, provider_node_names_map=None):
    y = _yaml()
    provider_node_names_set = set()
    provider_set = set()
    if name_to_node_map:
        clear_files(providers_dir)
        for k, v in provider_node_names_map.items():
            write(
                f'{providers_dir}/{k}.yaml',
                lambda f: y.dump({'proxies': [name_to_node_map[name] for name in v]}, f)
            )
        for k, v in provider_node_names_map.items():
            if v not in provider_node_names_set:
                provider_node_names_set.add(v)
                provider_set.add(k)
    else:
        for path in list_file_paths(providers_dir):
            provider_node_names = tuple(node['name'] for node in read(path, reader=y.load)['proxies'])
            if provider_node_names not in provider_node_names_set:
                provider_node_names_set.add(provider_node_names)
                provider_set.add(os.path.splitext(os.path.basename(path))[0])

    cfg = deepcopy(base_cfg)

    providers = cfg['proxy-providers']
    base_provider = base_cfg['proxy-providers']['All']
    for k in base_cfg['proxy-providers']:
        if k in provider_set:
            provider = deepcopy(base_provider)
            provider['url'] = f'https://ghproxy.com/https://raw.githubusercontent.com/{GITHUB_REPOSITORY}/{providers_dir}/{k}.yaml'
            provider['path'] = f'{providers_dir}/{k}.yaml'
            providers[k] = provider
        else:
            del providers[k]

    groups = []
    removed_groups = set()
    for g in cfg['proxy-groups']:
        if 'use' in g and g['use'][0] not in provider_set:
            removed_groups.add(g['name'])
        else:
            groups.append(g)
    for g in groups:
        if 'proxies' in g:
            g['proxies'] = [name for name in g['proxies'] if name not in removed_groups]
    cfg['proxy-groups'] = groups

    write(config_path, lambda f: y.dump(cfg, f))
