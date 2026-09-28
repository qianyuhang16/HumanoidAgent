"""JSON-lines worker: input remains responsive while loading/running the policy."""
import json
import sys
import threading

class Server:
    def __init__(self,emit,backend_factory=None):
        self.emit=emit;self.factory=backend_factory;self.backend=None
        self.active=None;self.cancelled=None;self.thread=None
        self.lock=threading.Lock();self.closed=False
        self.cancelled_ids=set()

    def handle(self,message):
        ident=message['id'];op=message['op']
        if op=='init':
            if self.backend is not None:raise ValueError('Already initialized')
            if self.factory is None:
                from .backend import Backend
                self.factory=Backend
            self.backend=self.factory(message['options'],message['connection'],message['media'])
            self.emit({'id':ident,'result':{'ready':True}})
        elif op=='run':
            with self.lock:
                if self.closed or self.active is not None:raise RuntimeError('Worker busy or closed')
                if self.backend is None:raise RuntimeError('Worker not initialized')
                token=message['call_id'];self.active=token;cancel=self.cancelled=threading.Event()
                if token in self.cancelled_ids:cancel.set()
            def work():
                try:
                    result=self.backend.run(message,cancel)
                except BaseException as error:
                    # Unknown failures must never claim that physical control was released.
                    result={'error':str(error),'control_released':False}
                finally:
                    with self.lock:self.active=None
                self.emit({'id':ident,'result':result})
            self.thread=threading.Thread(target=work,daemon=True);self.thread.start()
        elif op=='cancel':
            with self.lock:
                self.cancelled_ids.add(message['call_id'])
                if self.active==message['call_id']:self.cancelled.set()
            self.emit({'id':ident,'result':{'cancel_requested':True}})
        elif op=='close':
            self.closed=True
            if self.cancelled:self.cancelled.set()
            if self.thread:self.thread.join(timeout=8)
            self.emit({'id':ident,'result':{'closed':True}})
            return False
        else:raise ValueError('Unknown worker operation')
        return True

def main():
    protocol=sys.stdout;sys.stdout=sys.stderr;write_lock=threading.Lock()
    def emit(value):
        with write_lock:
            protocol.write(json.dumps(value,allow_nan=False)+'\n');protocol.flush()
    server=Server(emit)
    try:
        for line in sys.stdin:
            message={}
            try:
                message=json.loads(line)
                if not server.handle(message):break
            except Exception as error:emit({'id':message.get('id'),'error':str(error)})
    finally:
        if not server.closed:server.handle({'id':-1,'op':'close'})

if __name__=='__main__':main()
