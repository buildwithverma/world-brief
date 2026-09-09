"""Run the local UI and API together; Ctrl+C stops both children."""
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

root=Path(__file__).resolve().parent.parent
node=shutil.which('node')
if not node: sys.exit('Node.js 22 or later is required.')
for port in (3000,8000):
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1',port))==0:
            sys.exit(f'Port {port} is already in use. If World Brief is already running, open http://127.0.0.1:3000. Otherwise stop that service and retry.')
cli=root/'web'/'node_modules'/'vinext'/'dist'/'cli.js'
if not cli.exists(): sys.exit('Web dependencies are missing. Follow the install steps in README.md.')
logs=root/'data'/'logs'
logs.mkdir(parents=True,exist_ok=True)
children=[]
handles=[]
try:
    for name,command,cwd in [
        ('backend',[sys.executable,'-m','uvicorn','backend.main:app','--host','127.0.0.1','--port','8000'],root),
        ('web',[node,str(cli),'dev','--port','3000','--hostname','127.0.0.1'],root/'web')]:
        log=(logs/f'{name}.log').open('w',encoding='utf-8')
        handles.append(log)
        child=subprocess.Popen(command,cwd=cwd,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        children.append(child)
    print('World Brief is starting at http://127.0.0.1:3000',flush=True)
    print('Keep this terminal open. Press Ctrl+C to stop. First startup may take a minute.',flush=True)
    while all(p.poll() is None for p in children): time.sleep(1)
    print('A service stopped. See data/logs/backend.log and data/logs/web.log.',flush=True)
finally:
    for child in children:
        if child.poll() is None: child.terminate()
    for child in children:
        try: child.wait(timeout=10)
        except subprocess.TimeoutExpired: child.kill()
    for log in handles: log.close()
