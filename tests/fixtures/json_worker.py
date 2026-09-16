"""Small real subprocess for transport regression tests; no model dependencies."""

import json
import os
import sys
import threading
import time

lock = threading.Lock()
mode = sys.argv[1]


def send(message):
    with lock:
        print(json.dumps(message), flush=True)


def echo(message):
    time.sleep(message.get('delay', 0))
    send({'id': message['id'], 'result': message.get('value')})


if mode != 'init':
    send({'id': 0, 'result': {'ready': True}})
for line in sys.stdin:
    message = json.loads(line)
    op = message['op']
    if op == 'close':
        if mode == 'ignore-close':
            continue
        break
    if op == 'init':
        if message.get('hang'):
            continue
        send({'id': 0, 'result': {'ready': True, 'config': message.get('config')}})
    elif op == 'malformed':
        print('not-json', flush=True)
    elif op == 'exit':
        os._exit(1)
    elif op == 'error':
        send({'id': message['id'], 'error': 'requested failure'})
    elif op == 'event':
        send({'event': 'feedback', 'phase': 'running'})
        send({'id': message['id'], 'result': 'done'})
    else:
        threading.Thread(target=echo, args=(message,), daemon=True).start()
