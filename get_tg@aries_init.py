import json
import re
from base64 import b64decode, b64encode
from concurrent.futures import ThreadPoolExecutor

from bs4 import BeautifulSoup

from utils import new_session, write

re_update_time = re.compile(r'\d+/\d+/\d+')
re_node = re.compile(rb'(?:ssr?|vmess|trojan)://[^\s<]+')
re_node_vmess_base64 = re.compile(rb'^vmess://(?=[A-Za-z0-9+/]+={0,2}$)((?:.{4})+)$')
re_uuid = re.compile(r'[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}')


main = BeautifulSoup(new_session().get('https://t.me/aries_init/560?embed=1&mode=tme').text, 'html.parser')

node_update_time = main.find(text=re_update_time)
print(node_update_time.text)

msg_title, msg_urls = [], []
for node in node_update_time.find_next_sibling(text='–––––––––––––––––––––––––').next_siblings:
    if node.text == '–––––––––––––––––––––––––':
        break
    if node.name == 'a':
        msg_title.append(node.text)
        msg_urls.append(node['href'])


def get_nodes(msg_url):
    nodes = []
    for m in re_node.finditer(new_session().get(msg_url + '?embed=1&mode=tme').content):
        m2 = re_node_vmess_base64.fullmatch(m[0])
        if not m2 or re_uuid.fullmatch(json.loads(b64decode(m2[1]))['id']):
            nodes.append(m[0])
    return nodes


all_nodes = b''
with ThreadPoolExecutor(32) as executor:
    for title, url, nodes in zip(msg_title, msg_urls, executor.map(get_nodes, msg_urls)):
        print(f'[{title}]({url}) node_n: {len(nodes)}')
        for node in nodes:
            all_nodes += node + b'\n'

write('tg@aries_init', b64encode(all_nodes))
