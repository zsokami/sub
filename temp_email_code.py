import re
from queue import Queue
from threading import RLock, Thread
import time

from pymailtm import Account, MailTm

re_email_code = re.compile(r'(?<!\d)\d{6}(?!\d)')

lock_account = RLock()
lock = RLock()

account: Account = None
queues: list[tuple[str, Queue]] = []

th: Thread = None


def get_email() -> str:
    global account
    with lock_account:
        if not account:
            account = MailTm().get_account()
            print('temp email:', account.address)
    return account.address


def get_email_code(keyword):
    global th
    queue = Queue(1)
    with lock:
        queues.append((keyword, queue, time.time() + 60))
        if not th:
            th = Thread(target=_run)
            th.start()
    return queue.get()


def _run():
    global th
    while True:
        time.sleep(1)
        messages = account.get_messages()
        with lock:
            new_len = 0
            for item in queues:
                keyword, queue, end_time = item
                for message in messages:
                    if keyword in message.text:
                        m = re_email_code.search(message.text)
                        queue.put(m[0] if m else m)
                        break
                else:
                    if time.time() > end_time:
                        queue.put(None)
                    else:
                        queues[new_len] = item
                        new_len += 1
            del queues[new_len:]
            if new_len == 0:
                th = None
                break
