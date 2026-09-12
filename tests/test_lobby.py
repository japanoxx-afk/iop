import asyncio
from pathlib import Path
import socket
import struct
import tempfile
import unittest

from iop_server.main import Accounts,Lobby
from iop_server.protocol import packet,read_packet,fixed,HEADER

async def receive(reader):
    header=await asyncio.wait_for(reader.readexactly(5),2)
    kind,value,length=HEADER.unpack(header)
    return kind,value,await asyncio.wait_for(reader.readexactly(length-5),2)

class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.accounts=Accounts(Path(self.temp.name)/'accounts.db')
        self.lobby=Lobby(self.accounts)
        self.server=await asyncio.start_server(self.lobby.connection,'127.0.0.1',0)
        self.port=self.server.sockets[0].getsockname()[1]
        self.writers=[]

    async def asyncTearDown(self):
        for writer in self.writers: writer.close()
        for writer in self.writers: await writer.wait_closed()
        await asyncio.sleep(.05)
        self.server.close(); await self.server.wait_closed()
        self.accounts.db.close(); self.temp.cleanup()

    async def connect(self):
        reader,writer=await asyncio.open_connection('127.0.0.1',self.port)
        self.writers.append(writer)
        return reader,writer

    async def auth(self,name,kind=3,password=b'testpass'):
        reader,writer=await self.connect()
        writer.write(packet(kind,0,fixed(name,21)+fixed(password,17)+struct.pack('<I',12345)))
        reply=await receive(reader)
        return reader,writer,reply

    async def test_registration_login_wrong_password_and_persistence(self):
        r,w,reply=await self.auth(b'player')
        self.assertEqual(reply[:2],(4,10)); self.assertEqual(len(reply[2]),21)
        w.close(); await w.wait_closed(); await asyncio.sleep(.05)
        _,_,reply=await self.auth(b'player',5,b'wrong')
        self.assertEqual(reply[:2],(6,2))
        _,_,reply=await self.auth(b'player',5)
        self.assertEqual(reply[:2],(6,10))
        _,_,reply=await self.auth(b'player')
        self.assertEqual(reply[:2],(4,4))
        second=Accounts(Path(self.temp.name)/'accounts.db')
        self.assertEqual(second.authenticate(b'player',b'testpass')[0],10)
        second.db.close()

    async def test_two_users_room_join_chat_leave_disconnect(self):
        r1,w1,a=await self.auth(b'host')
        r2,w2,b=await self.auth(b'guest')
        uid1=struct.unpack_from('<I',a[2])[0]
        w2.write(packet(14)+packet(16))
        users=await receive(r2)
        self.assertEqual(users[:2],(15,2)); self.assertEqual(len(users[2]),74)
        self.assertEqual((await receive(r2))[:2],(17,0))
        create=bytes([0])+struct.pack('<H',7000)+fixed(b'My room',31)+fixed(b'host',21)+fixed(b'',17)+bytes([4])
        w1.write(packet(7,0,create))
        response=await receive(r1)
        self.assertEqual(response[:2],(8,1)); rid=struct.unpack('<I',response[2])[0]
        room=await receive(r2)
        self.assertEqual(room[:2],(17,1)); self.assertEqual(len(room[2]),43)
        self.assertEqual(room[2][1:32],fixed(b'My room',31))
        self.assertEqual(room[2][32:34],b'\x01\x04')
        await receive(r2) # host status
        w2.write(packet(10,0,struct.pack('<I',rid)+fixed(b'My room',31)+fixed(b'',17)))
        reply=await receive(r2)
        self.assertEqual(reply[:2],(11,1))
        self.assertEqual(reply[2],struct.pack('<I',rid)+socket.inet_aton('127.0.0.1')+struct.pack('<H',7000))
        await receive(r2); await receive(r2)
        w1.write(packet(19,0,b'\0'+struct.pack('<I',999)+b'Hello\0'))
        self.assertEqual((await receive(r1))[2],b'\0'+struct.pack('<I',uid1)+b'Hello\0')
        self.assertEqual((await receive(r2))[0],19)
        w2.write(packet(9,0,struct.pack('<I',rid)+b'\0'*6))
        await receive(r2); await receive(r2)
        self.assertEqual(len(self.lobby.rooms[rid].members),1)
        w1.close(); await w1.wait_closed(); await asyncio.sleep(.05)
        self.assertFalse(self.lobby.rooms)

    async def test_fragmentation_coalescing_and_malformed_lengths(self):
        r,w=await self.connect()
        body=fixed(b'fragment',21)+fixed(b'pass',17)+b'\0'*4
        data=packet(3,0,body)
        for byte in data:
            w.write(bytes([byte])); await w.drain()
        self.assertEqual((await receive(r))[:2],(4,10))
        r,w=await self.connect()
        w.write(HEADER.pack(3,0,65535))
        self.assertEqual(await asyncio.wait_for(r.read(),2),b'')

    async def test_unauthenticated_request_rejected(self):
        r,w=await self.connect(); w.write(packet(16))
        self.assertEqual(await asyncio.wait_for(r.read(),2),b'')

if __name__=='__main__': unittest.main()
