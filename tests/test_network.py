import hashlib
from pathlib import Path
import socket
import struct
import tempfile
import time
import unittest
from unittest.mock import patch

from iop_network import ServerController,hosts_content,update_hosts,resolve_server,patched_bytes,ORIGINAL_HASH,ADDRESS_OFFSET,ORIGINAL_ADDRESS
from iop_server.protocol import packet,fixed,HEADER

def receive(sock):
    def exact(count):
        result=b''
        while len(result)<count:
            data=sock.recv(count-len(result))
            if not data: raise EOFError()
            result+=data
        return result
    kind,value,size=HEADER.unpack(exact(5))
    return kind,value,exact(size-5)

class HostsTests(unittest.TestCase):
    def test_mapping_preserves_other_hosts_and_aliases(self):
        original=b'# note\r\n127.0.0.1 localhost\r\n1.2.3.4 other IOP.SERVER # keep\r\n5.6.7.8 iop.server\r\n'
        result=hosts_content(original,'26.1.2.3')
        self.assertIn(b'127.0.0.1 localhost',result)
        self.assertIn(b'1.2.3.4\tother # keep',result)
        self.assertEqual(result.count(b'iop.server'),1)
        self.assertEqual(hosts_content(result,'26.1.2.3'),result)
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'hosts'; file.write_bytes(original)
            backup=update_hosts('26.1.2.3',file)
            self.assertEqual(backup.read_bytes(),original)
            self.assertEqual(file.read_bytes(),result)

    def test_resolution_uses_system_hosts_resolver(self):
        with patch('iop_network.hosts_mapping',return_value='26.1.2.3'), patch('socket.gethostbyname',return_value='26.1.2.3') as resolver:
            self.assertEqual(resolve_server('iop.server'),'26.1.2.3')
            resolver.assert_called_once_with('iop.server')
        with patch('iop_network.hosts_mapping',return_value=None), patch('socket.gethostbyname') as resolver:
            with self.assertRaises(ValueError): resolve_server('iop.server')
            resolver.assert_not_called()
        with self.assertRaises(ValueError): hosts_content(b'',"26.1.2.3\nattacker")

    def test_client_patch_only_changes_address(self):
        source=Path(__file__).resolve().parents[1]/'game-assets/v0.019/iop.exe'
        if not source.exists(): self.skipTest('reference client unavailable')
        original=source.read_bytes()
        self.assertEqual(hashlib.sha256(original[:ADDRESS_OFFSET]+ORIGINAL_ADDRESS+original[ADDRESS_OFFSET+16:]).hexdigest(),ORIGINAL_HASH)
        changed=patched_bytes(original,'26.157.67.215')
        self.assertEqual(original[:ADDRESS_OFFSET],changed[:ADDRESS_OFFSET])
        self.assertEqual(original[ADDRESS_OFFSET+16:],changed[ADDRESS_OFFSET+16:])
        self.assertEqual(patched_bytes(changed,'127.0.0.1')[ADDRESS_OFFSET:ADDRESS_OFFSET+16],b'127.0.0.1\0'.ljust(16,b'\0'))
        with self.assertRaises(ValueError): patched_bytes(b'bad','127.0.0.1')

class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.controller=ServerController()
        self.sockets=[]
    def tearDown(self):
        for sock in self.sockets: sock.close()
        self.controller.stop()
        if self.controller.thread: self.controller.thread.join(5)
        self.assertFalse(self.controller.alive)
        self.tmp.cleanup()
    def wait_on(self):
        for _ in range(100):
            if self.controller.state=='ON': return
            time.sleep(.02)
        self.fail('server did not turn ON')
    def connect(self):
        sock=socket.create_connection(('127.0.0.1',self.controller.bound_ports[-1]),2)
        self.sockets.append(sock); return sock
    def test_on_off_restart_account_persistence_and_advertised_address(self):
        self.controller.start('26.1.2.3',Path(self.tmp.name),ports=(0,0,0)); self.wait_on()
        sock=self.connect()
        auth=fixed(b'host',21)+fixed(b'pass',17)+b'\0'*4
        sock.sendall(packet(3,0,auth)); self.assertEqual(receive(sock)[:2],(4,10))
        create=b'\0'+socket.htons(4801).to_bytes(2,'little')+fixed(b'test',31)+fixed(b'host',21)+fixed(b'',17)+b'\x04'
        sock.sendall(packet(7,0,create)); self.assertEqual(receive(sock)[:2],(8,1))
        sock.sendall(packet(16)); room=receive(sock)[2]
        self.assertEqual(room[38:42],socket.inet_aton('26.1.2.3'))
        old_ports=list(self.controller.bound_ports)
        self.controller.stop(); self.controller.thread.join(5)
        self.assertEqual(sock.recv(1),b'')
        self.assertEqual(self.controller.state,'OFF')
        for port in old_ports:
            with self.assertRaises(OSError): socket.create_connection(('127.0.0.1',port),.1)
        self.controller.start('26.1.2.3',Path(self.tmp.name),ports=(0,0,0)); self.wait_on()
        second=self.connect(); second.sendall(packet(5,0,auth))
        self.assertEqual(receive(second)[:2],(6,10))

    def test_conflict_does_not_stop_unrelated_listener(self):
        busy=socket.socket(); busy.bind(('0.0.0.0',0)); busy.listen()
        self.sockets.append(busy)
        port=busy.getsockname()[1]
        self.controller.start('127.0.0.1',Path(self.tmp.name),ports=(0,port))
        self.controller.thread.join(5)
        self.assertFalse(self.controller.alive)
        check=socket.create_connection(('127.0.0.1',port),1); check.close()
        self.assertTrue(any(event[0]=='error' for event in list(self.controller.events.queue)))

    def test_immediate_off_during_start(self):
        self.controller.start('127.0.0.1',Path(self.tmp.name),ports=(0,0,0))
        self.controller.stop(); self.controller.thread.join(5)
        self.assertFalse(self.controller.alive)

if __name__=='__main__': unittest.main()
