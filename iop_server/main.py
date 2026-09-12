"""Local experimental Big Brain compatible lobby. Python 3.11+, stdlib only."""
import argparse
import asyncio
import hashlib
import hmac
import logging
import os
from pathlib import Path
import socket
import sqlite3
import struct
from dataclasses import dataclass, field

from .protocol import packet, read_packet, fixed, cstring, list_packets

LOG = logging.getLogger('iop')

class Accounts:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS accounts (id INTEGER PRIMARY KEY, name BLOB UNIQUE, salt BLOB, digest BLOB)')
        self.db.commit()

    def authenticate(self, name, password, register=False):
        if not name or len(name) > 20 or not password or len(password) > 16:
            return 1, None
        row = self.db.execute('SELECT id,salt,digest FROM accounts WHERE name=?', (name,)).fetchone()
        if register and row:
            return 4, None
        if not row:
            if not register:
                return 1, None
            salt = os.urandom(16)
            digest = hashlib.pbkdf2_hmac('sha256', password, salt, 200000)
            cur = self.db.execute('INSERT INTO accounts(name,salt,digest) VALUES(?,?,?)', (name,salt,digest))
            self.db.commit()
            return 10, cur.lastrowid
        digest = hashlib.pbkdf2_hmac('sha256', password, row[1], 200000)
        return (10, row[0]) if hmac.compare_digest(digest,row[2]) else (2,None)

@dataclass(eq=False)
class Client:
    writer: object
    ip: str
    uid: int = 0
    name: bytes = b''
    room: int = 0
    users_subscribed: bool = False
    rooms_subscribed: bool = False

    def send(self, data):
        if not self.writer.is_closing():
            self.writer.write(data)

    def user_record(self, action=0):
        # action, wins/losses/draws, name[21], status, room id, user id
        return struct.pack('<BHHH21sBII', action,0,0,0,fixed(self.name,21),bool(self.room),self.room,self.uid)

@dataclass
class Room:
    rid: int
    host: Client
    title: bytes
    port: int
    password: bytes
    capacity: int
    members: set = field(default_factory=set)
    state: int = 0

    def record(self, action=0):
        return (struct.pack('<B31sBBI',action,fixed(self.title,31),len(self.members),self.capacity,self.rid)
                + socket.inet_aton(self.host.ip) + bytes([self.state]))

class Lobby:
    def __init__(self, accounts, advertised_ip=None):
        self.accounts = accounts
        self.clients = set()
        self.rooms = {}
        self.next_room = 1
        self.advertised_ip = advertised_ip
        self.tasks = set()

    def users_update(self, client, action=2):
        data = packet(15,1,client.user_record(action))
        for other in self.clients:
            if other.uid and other.users_subscribed:
                other.send(data)

    def room_update(self, room, action=2):
        data = packet(17,1,room.record(action))
        for other in self.clients:
            if other.uid and other.rooms_subscribed:
                other.send(data)

    def leave(self, client):
        room = self.rooms.get(client.room)
        if room:
            if room.host is client:
                self.room_update(room,1)
                del self.rooms[room.rid]
                for member in room.members:
                    member.room = 0
                    self.users_update(member)
            else:
                room.members.discard(client)
                client.room = 0
                self.room_update(room)
                self.users_update(client)
        client.room = 0

    async def handle(self, client, kind, value, body, port):
        LOG.info('RX port=%s uid=%s kind=%s value=%s bytes=%s',port,client.uid,kind,value,len(body))
        if port in (6300,6302):
            if kind != 1:
                raise ValueError('expected version request')
            client.send(packet(2,1)) # client returns 2, skipping updater
            return
        if kind in (3,5):
            if client.uid:
                raise ValueError('already authenticated')
            name,password = cstring(body[:21]),cstring(body[21:38])
            status,uid = self.accounts.authenticate(name,password,register=kind==3)
            if uid and any(c.uid == uid for c in self.clients):
                status,uid = 5,None
            if uid:
                client.uid,client.name = uid,name
            # Client reads 21 bytes of identity/statistics after status 10.
            reply = struct.pack('<IIHHHIHB',uid or 0,0,0,0,0,0,0,0) if uid else b''
            client.send(packet(kind+1,status,reply))
            if uid:
                self.users_update(client,0)
            return
        if not client.uid:
            raise ValueError('login required')
        if kind == 14:
            client.users_subscribed = True
            for data in list_packets(15,[c.user_record() for c in self.clients if c.uid]): client.send(data)
        elif kind == 16:
            client.rooms_subscribed = True
            for data in list_packets(17,[r.record() for r in self.rooms.values()]): client.send(data)
        elif kind == 7:
            title = cstring(body[3:34])
            capacity = body[72]
            if client.room or not title or not 2 <= capacity <= 8:
                client.send(packet(8,6,b'\0'*4)); return
            room = Room(self.next_room,client,title,struct.unpack_from('<H',body,1)[0],cstring(body[55:72]),capacity,{client})
            self.next_room += 1
            self.rooms[room.rid] = room
            client.room = room.rid
            client.send(packet(8,1,struct.pack('<I',room.rid)))
            self.room_update(room,0)
            self.users_update(client)
        elif kind == 10:
            rid = struct.unpack_from('<I',body)[0]
            room = self.rooms.get(rid)
            if not room or client.room or room.state or len(room.members) >= room.capacity:
                client.send(packet(11,6,b'\0'*10)); return
            if cstring(body[35:52]) != room.password:
                client.send(packet(11,2,b'\0'*10)); return
            room.members.add(client)
            client.room = rid
            client.send(packet(11,1,struct.pack('<I',rid)+socket.inet_aton(room.host.ip)+struct.pack('<H',room.port)))
            self.room_update(room)
            self.users_update(client)
        elif kind == 9:
            self.leave(client)
        elif kind == 12:
            room = self.rooms.get(client.room)
            if room and room.host is client:
                room.state = min(value,255)
                self.room_update(room)
        elif kind == 19:
            if len(body)<6 or len(body)>1024 or b'\0' not in body[5:]:
                raise ValueError('malformed chat')
            if body[0] != 0:
                raise ValueError('unsupported chat scope')
            # Route only authenticated sender identity; never trust claimed sender id.
            data = packet(19,value,body[:1]+struct.pack('<I',client.uid)+cstring(body[5:])+b'\0')
            for other in self.clients:
                if other.uid: other.send(data)
        elif kind == 13:
            LOG.info('Match result received; ranked persistence is not implemented')
        else:
            raise ValueError(f'unsupported message {kind}')

    async def connection(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        client = Client(writer,writer.get_extra_info('peername')[0])
        if client.ip.startswith('127.') and self.advertised_ip:
            client.ip = self.advertised_ip
        port = writer.get_extra_info('sockname')[1]
        self.clients.add(client)
        LOG.info('Connected port=%s',port)
        try:
            while True:
                kind,value,body = await asyncio.wait_for(read_packet(reader),timeout=600)
                await self.handle(client,kind,value,body,port)
                await asyncio.wait_for(writer.drain(),timeout=10)
        except (asyncio.IncompleteReadError,ConnectionError,TimeoutError):
            pass
        except ValueError as exc:
            LOG.warning('Rejected client: %s',exc)
        except Exception:
            LOG.exception('Connection failure')
        finally:
            self.leave(client)
            self.clients.discard(client)
            if client.uid: self.users_update(client,1)
            writer.close()
            try: await writer.wait_closed()
            except ConnectionError: pass
            LOG.info('Disconnected uid=%s',client.uid)
            self.tasks.discard(task)

async def run(host, data_dir):
    data_dir.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s',
                        handlers=[logging.StreamHandler(),logging.FileHandler(data_dir/'server.log',encoding='utf-8')])
    accounts = Accounts(data_dir/'accounts.sqlite3')
    lobby = Lobby(accounts)
    servers = []
    try:
        for port in (6300,6302,6305):
            servers.append(await asyncio.start_server(lobby.connection,host,port,limit=4096))
            LOG.info('Listening %s:%s',host,port)
        await asyncio.Future()
    finally:
        for server in servers: server.close()
        for client in list(lobby.clients): client.writer.close()
        tasks=list(lobby.tasks)
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks,return_exceptions=True)
        for server in servers: await server.wait_closed()
        accounts.db.close()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--data-dir',type=Path,default=Path('server_data'))
    args = parser.parse_args()
    try: asyncio.run(run(args.host,args.data_dir))
    except KeyboardInterrupt: pass

if __name__ == '__main__':
    main()
