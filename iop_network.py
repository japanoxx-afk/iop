"""Launcher networking: owned in-process server, hosts resolution and safe client copy."""
import asyncio
import hashlib
import ipaddress
import logging
import os
from pathlib import Path
import queue
import socket
import threading
import time

from iop_server.main import Accounts, Lobby

SERVER_NAME = 'iop.server'
ORIGINAL_HASH = '489f2217dc13e9b7b8b3fee97bd78843e70a2166f4ce50efc429c5cb1ec86028'
ADDRESS_OFFSET = 0xd2cbc
ORIGINAL_ADDRESS = b'211.239.125.103\0'
PORTS = (6300,6302,6305)

def hosts_mapping():
    path=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/drivers/etc/hosts'
    for line in path.read_bytes().decode('latin-1').splitlines():
        fields=line.split('#',1)[0].split()
        if len(fields)>1 and SERVER_NAME in [part.lower() for part in fields[1:]]:
            return str(ipaddress.IPv4Address(fields[0]))
    return None

def resolve_server(address):
    address = address.strip()
    if not address or len(address)>253 or any(c.isspace() for c in address):
        raise ValueError('서버 IP 또는 hosts 이름을 입력하세요.')
    mapping=None
    if address.lower()==SERVER_NAME:
        mapping=hosts_mapping()
        if not mapping:
            raise ValueError(f'hosts에 {SERVER_NAME}가 없습니다. A PC의 Radmin IP를 등록하거나 IP를 직접 입력하세요.')
    ip = socket.gethostbyname(address)
    if mapping and mapping!=ip:
        raise ValueError('hosts와 Windows 이름 해석 결과가 다릅니다. hosts 등록을 다시 실행하고 접속 검사하세요.')
    value = ipaddress.IPv4Address(ip)
    if value.is_unspecified or value.is_multicast or ip=='255.255.255.255':
        raise ValueError('접속 가능한 서버 IPv4 주소가 아닙니다.')
    return ip

def patched_bytes(data, ip):
    ipaddress.IPv4Address(ip)
    if len(data)<ADDRESS_OFFSET+16:
        raise ValueError('지원되지 않는 iop.exe입니다.')
    from iop_hotkeys import normalize_exe
    from iop_stability import normalize
    base = normalize(normalize_exe(data))
    normalized = base[:ADDRESS_OFFSET]+ORIGINAL_ADDRESS+base[ADDRESS_OFFSET+16:]
    if hashlib.sha256(normalized).hexdigest()!=ORIGINAL_HASH:
        raise ValueError('지원되지 않는 iop.exe 버전입니다. 원본은 변경하지 않았습니다.')
    return data[:ADDRESS_OFFSET]+(ip.encode('ascii')+b'\0').ljust(16,b'\0')+data[ADDRESS_OFFSET+16:]

def prepare_private_exe(game_dir, ip):
    destination = Path(game_dir)/'iop.exe'
    old = destination.read_bytes()
    data = patched_bytes(old, ip)
    if data == old: return destination
    backup = destination.with_name('iop.exe.before-server.bak')
    # Opening the loaded Windows executable for writing fails before any mutation.
    with destination.open('r+b') as stream:
        if not backup.exists():
            with backup.open('xb') as out: out.write(old)
        else:
            patched_bytes(backup.read_bytes(), ip)
        stream.seek(ADDRESS_OFFSET)
        stream.write(data[ADDRESS_OFFSET:ADDRESS_OFFSET+16])
        stream.flush()
        os.fsync(stream.fileno())
    return destination


def hosts_content(original, ip):
    ipaddress.IPv4Address(ip)
    lines=[]
    for line in original.decode('latin-1').splitlines(keepends=True):
        active,sep,comment=line.partition('#')
        parts=active.split()
        if len(parts)>1 and SERVER_NAME in [p.lower() for p in parts[1:]]:
            aliases=[p for p in parts[1:] if p.lower()!=SERVER_NAME]
            if aliases:
                lines.append(parts[0]+'\t'+' '.join(aliases)+((' #'+comment.rstrip('\r\n')) if sep else '')+'\r\n')
            elif sep and 'IOP Launcher' not in comment:
                lines.append('#'+comment.rstrip('\r\n')+'\r\n')
        else:
            lines.append(line)
    text=''.join(lines)
    if text and not text.endswith(('\r','\n')): text+='\r\n'
    return (text+f'{ip}\t{SERVER_NAME}\t# IOP Launcher\r\n').encode('latin-1')

def update_hosts(ip, path=None):
    path=Path(path) if path else Path(os.environ['SystemRoot'])/'System32/drivers/etc/hosts'
    old=path.read_bytes()
    new=hosts_content(old,ip)
    if old==new: return None
    backup=path.with_name(f'hosts.iop-backup-{time.time_ns()}')
    backup.write_bytes(old)
    path.write_bytes(new)
    return backup

class QueueLog(logging.Handler):
    def __init__(self, events):
        super().__init__(); self.events=events
    def emit(self, record):
        self.events.put(('log',self.format(record)))

class ServerController:
    """Only owns its thread/sockets: OFF never kills another server process."""
    def __init__(self):
        self.events=queue.Queue()
        self.thread=None
        self.loop=None
        self.stop_event=None
        self.stop_requested=threading.Event()
        self.state='OFF'
        self.bound_ports=[]

    @property
    def alive(self): return self.thread is not None and self.thread.is_alive()

    def start(self, advertised_ip, data_dir, ports=PORTS, game_dir=None, sync_port=6306):
        if self.alive: raise RuntimeError('서버가 이미 시작 또는 실행 중입니다.')
        ipaddress.IPv4Address(advertised_ip)
        self.stop_requested.clear()
        self.bound_ports=[]
        self.state='STARTING'
        self.thread=threading.Thread(target=self._thread,args=(advertised_ip,Path(data_dir),ports,game_dir,sync_port),daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_requested.set()
        if self.alive: self.state='STOPPING'
        if self.loop and self.stop_event:
            try: self.loop.call_soon_threadsafe(self.stop_event.set)
            except RuntimeError: pass

    def _thread(self, ip, data_dir, ports, game_dir, sync_port):
        try: asyncio.run(self._serve(ip,data_dir,ports,game_dir,sync_port))
        except Exception as exc:
            self.events.put(('error',str(exc)))
        finally:
            self.loop=self.stop_event=None
            self.state='OFF'
            self.events.put(('state','OFF'))

    async def _serve(self, ip, data_dir, ports, game_dir, sync_port):
        self.loop=asyncio.get_running_loop()
        self.stop_event=asyncio.Event()
        data_dir.mkdir(parents=True,exist_ok=True)
        logger=logging.getLogger('iop')
        logger.setLevel(logging.INFO)
        log=QueueLog(self.events)
        disk=logging.FileHandler(data_dir/'server.log',encoding='utf-8')
        for handler in (log,disk):
            handler.setFormatter(logging.Formatter('%(asctime)s %(message)s','%H:%M:%S'))
            logger.addHandler(handler)
        accounts=None; lobby=None; servers=[]; sync_tasks=set()
        try:
            accounts=Accounts(data_dir/'accounts.sqlite3')
            lobby=Lobby(accounts,advertised_ip=ip)
            for port in ports:
                servers.append(await asyncio.start_server(lobby.connection,'0.0.0.0',port,limit=4096))
            self.bound_ports=[server.sockets[0].getsockname()[1] for server in servers]
            if game_dir:
                from iop_sync import serve_manifest
                async def sync_connection(reader, writer):
                    task=asyncio.current_task(); sync_tasks.add(task)
                    try: await serve_manifest(writer, game_dir)
                    finally: sync_tasks.discard(task)
                sync_server=await asyncio.start_server(sync_connection,'0.0.0.0',sync_port)
                self.sync_port=sync_server.sockets[0].getsockname()[1]
                servers.append(sync_server)

            if not self.stop_requested.is_set():
                self.state='ON'
                self.events.put(('state','ON'))
                logger.info('서버 ON | 접속 주소 %s | TCP %s',ip,','.join(map(str,ports)))
                await self.stop_event.wait()
        finally:
            for server in servers: server.close()
            if lobby:
                for client in list(lobby.clients): client.writer.close()
                tasks=list(lobby.tasks)
                for task in tasks: task.cancel()
                if tasks: await asyncio.gather(*tasks,return_exceptions=True)
            for task in list(sync_tasks): task.cancel()
            if sync_tasks: await asyncio.gather(*list(sync_tasks),return_exceptions=True)
            for server in servers: await server.wait_closed()
            if accounts: accounts.db.close()
            for handler in (log,disk): logger.removeHandler(handler); handler.close()
