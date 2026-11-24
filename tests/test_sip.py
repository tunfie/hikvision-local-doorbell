import unittest,os,sys
from pathlib import Path
os.environ['HUIS_CAMERA_IP']='192.0.2.20';os.environ['HUIS_SERVER_IP']='192.0.2.10'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'bridge'))
from sip_dialog import Dialog

class ProvisionalTests(unittest.TestCase):
    def packet(self,method='INVITE',body=''):
        return (f'{method} sip:againdevice@192.0.2.10:5060 SIP/2.0\r\nVia: SIP/2.0/UDP 192.0.2.20:5060;rport;branch=z9hG4bKtest\r\nFrom: <sip:againdevice@192.0.2.20>;tag=camera\r\nTo: <sip:againdevice@192.0.2.10:5060>\r\nCall-ID: offline-test\r\nCSeq: 20 {method}\r\nContent-Length: {len(body)}\r\n\r\n'+body).encode()
    def setUp(self):
        self.sent=[];self.dialog=Dialog(lambda data,peer:self.sent.append(data));self.peer=('192.0.2.20',5060)
    def test_ring_does_not_answer(self):
        self.assertIsNotNone(self.dialog.receive(self.packet(),self.peer))
        self.assertEqual([p.split(b'\r\n')[0] for p in self.sent],[b'SIP/2.0 100 Trying',b'SIP/2.0 180 Ringing'])
        self.assertIsNone(self.dialog.owner);self.assertEqual(self.dialog.phase,'ringing')
        self.assertNotIn(b';tag=',self.sent[0].split(b'To: ')[1].split(b'\r\n')[0])
        self.assertIn(b';rport=5060',self.sent[1]);self.assertIn(b';received=192.0.2.20',self.sent[1])
        self.assertIsNone(self.dialog.receive(self.packet(),self.peer))
        self.assertEqual(self.sent[-1],self.sent[-2])
    def test_answer_requires_ack(self):
        self.dialog.receive(self.packet(),self.peer);tag=self.dialog.tag
        self.assertFalse(self.dialog.answer('a'*64)['answered'])
        self.assertEqual(self.dialog.tag,tag);self.assertIn((';tag='+tag).encode(),self.sent[-1])
        self.assertEqual(self.dialog.phase,'answering')
        self.dialog.receive(self.packet('ACK','v=0\r\nc=IN IP4 192.0.2.20\r\nm=audio 41000 RTP/AVP 0\r\n'),self.peer)
        self.assertTrue(self.dialog.answer('a'*64)['answered']);self.assertEqual(self.dialog.rtp_port,41000)
        with self.assertRaises(PermissionError):self.dialog.answer('b'*64)
    def test_other_host_ignored(self):
        self.assertIsNone(self.dialog.receive(self.packet(),('192.0.2.99',5060)));self.assertEqual(self.sent,[])
    def test_decline_never_opens_conversation(self):
        self.dialog.receive(self.packet(),self.peer)
        self.assertTrue(self.dialog.reject('a'*64)['declined']);self.assertIsNone(self.dialog.owner)
        self.assertTrue(self.sent[-1].startswith(b'SIP/2.0 603 Decline'))
        self.dialog.receive(self.packet(),self.peer);self.assertTrue(self.sent[-1].startswith(b'SIP/2.0 603 Decline'))
        self.dialog.receive(self.packet('ACK'),self.peer);self.assertEqual(self.dialog.phase,'ended')
    def test_decline_does_not_interrupt_other_user(self):
        self.dialog.receive(self.packet(),self.peer);self.dialog.answer('a'*64)
        with self.assertRaises(PermissionError):self.dialog.reject('b'*64)

