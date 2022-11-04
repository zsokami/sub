import re
from queue import Queue
from threading import Condition, RLock, Thread
from time import sleep

from pymailtm import Account, MailTm

re_email_code = re.compile(r'(?<!\d)\d{6}(?!\d)')

lock = RLock()
non_empty = Condition(lock)

account: Account = None
queues: list[tuple[str, Queue]] = []


def get_email() -> str:
    global account
    with lock:
        if not account:
            account = MailTm().get_account()
            print('temp email:', account.address)
            Thread(target=_run).start()
        return account.address


def get_email_code(keyword):
    queue = Queue(1)
    with lock:
        queues.append((keyword, queue))
        non_empty.notify_all()
    return queue.get()


def _run():
    while True:
        with lock:
            if not queues:
                non_empty.wait()
        sleep(1)
        messages = account.get_messages()
        with lock:
            new_len = 0
            for keyword, queue in queues:
                for message in messages:
                    if keyword in message.text:
                        queue.put(re_email_code.search(message.text)[0])
                        break
                else:
                    queues[new_len] = (keyword, queue)
                    new_len += 1
            del queues[new_len:]
