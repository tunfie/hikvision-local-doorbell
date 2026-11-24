"""Minimal camera-only SIP dialog control; no audio capture or media transport."""
import hashlib,secrets,time,re
from local_settings import CAMERA_IP, SERVER_IP

def parse_message(data):
    if len(data)>8192:return None
    text=data.decode('utf-8',errors='replace');head,_,body=text.partition('\r\n\r\n');lines=head.split('\r\n');headers={}
    for line in lines[1:]:
        if ':' in line:
            key,value=line.split(':',1);headers.setdefault(key.lower(),[]).append(value.strip())
    call=(headers.get('call-id') or headers.get('i') or [''])[0]
    if not call or len(call)>512:return None
    return {'first':lines[0],'headers':headers,'body':body,'call':call,'identity':hashlib.sha256(call.encode()).hexdigest()}

class Dialog:
    def __init__(self,send):self.send=send;self.call=None;self.phase='idle';self.owner=None;self.tag=secrets.token_hex(8);self.updated=time.monotonic();self.response=None;self.rtp_port=None;self.response_at=0;self.diagnostics=[]
    def header(self,name):return self.call['headers'].get(name,[''])[0]
    def reply(self,message,peer,code,reason,body=''):
        headers=message['headers'];lines=[f'SIP/2.0 {code} {reason}']
        for value in headers.get('via',[]):
            value=re.sub(r';rport(?:=\d+)?(?=;|$)',';rport='+str(peer[1]),value)
            if ';received=' not in value:value+=';received='+peer[0]
            lines.append('Via: '+value)
        for key,label in [('from','From'),('to','To'),('call-id','Call-ID'),('cseq','CSeq')]:
            value=headers.get(key,[''])[0]
            if key=='call-id':value=message['call']
            if key=='to' and code!=100 and ';tag=' not in value:value+=';tag='+self.tag
            lines.append(label+': '+value)
        if code>=180:lines+=[f'Contact: <sip:againdevice@{SERVER_IP}:5060>','User-Agent: Huis/1.1','Allow: INVITE, ACK, CANCEL, BYE, OPTIONS']
        if body:lines+=['Content-Type: application/sdp']
        payload=('\r\n'.join(lines)+f'\r\nContent-Length: {len(body.encode())}\r\n\r\n'+body).encode();self.send(payload,peer);return payload
    def receive(self,data,peer):
        if peer[0]!=CAMERA_IP:return None
        message=parse_message(data)
        if not message:return None
        method=message['first'].split(' ',1)[0]
        self.diagnostics=(self.diagnostics+[{'method':method,'cseq_method':message['headers'].get('cseq',[''])[0].split(' ')[-1],'phase':self.phase}])[-12:]
        if method=='INVITE' and message['headers'].get('cseq',[''])[0].upper().endswith(' INVITE'):
            if self.call and message['identity']==self.call['identity']:
                if self.response:self.send(self.response,peer)
                return None
            if self.owner:return None
            self.call=dict(message,peer=peer,started=time.monotonic());self.phase='ringing';self.owner=None;self.response=None;self.rtp_port=None;self.tag=secrets.token_hex(8);self.updated=time.monotonic()
            self.reply(message,peer,100,'Trying');self.response=self.reply(message,peer,180,'Ringing');return message['identity']
        if not self.call or message['identity']!=self.call['identity']:return None
        if method=='ACK' and self.phase=='declined':
            self.phase='ended';self.response=None
        elif method=='ACK' and self.owner:
            self.phase='active';self.updated=time.monotonic()
            lines=message['body'].splitlines();ip=next((line[9:] for line in lines if line.startswith('c=IN IP4 ')),None)
            audio=next((line.split() for line in lines if line.startswith('m=audio ')),None)
            if ip==CAMERA_IP and audio and len(audio)>3 and '0' in audio[3:]:
                try:
                    port=int(audio[1]);self.rtp_port=port if 1024<=port<=65535 else None
                except ValueError:pass
        elif method=='CANCEL':
            self.reply(message,peer,200,'OK')
            if self.phase=='ringing':self.reply(self.call,peer,487,'Request Terminated');self.phase='ended';self.owner=None
        elif method=='BYE':self.reply(message,peer,200,'OK');self.phase='ended';self.owner=None
        elif method=='SIP/2.0' and ' BYE' in message['headers'].get('cseq',[''])[0]:self.phase='ended';self.owner=None
        return None
    def answer(self,owner):
        if self.owner and self.owner!=owner:raise PermissionError('Het gesprek is al op een ander toestel aangenomen')
        if not self.call or self.phase in ['idle','ended'] or (self.phase=='ringing' and time.monotonic()-self.call['started']>120):return {'state':'monitor','answered':False}
        if not self.owner:
            body=f'v=0\r\no=againdevice 1 1 IN IP4 {SERVER_IP}\r\ns=Talk session\r\nc=IN IP4 {SERVER_IP}\r\nt=0 0\r\na=doorFloor:1\r\na=responseType:0\r\na=doorType:0\r\na=isSpecialType:0\r\nm=audio 40000 RTP/AVP 0\r\na=rtpmap:0 PCMU/8000\r\na=sendrecv\r\n'
            self.response=self.reply(self.call,self.call['peer'],200,'OK',body);self.owner=owner;self.phase='answering';self.updated=time.monotonic();self.response_at=self.updated
        return {'state':self.phase,'answered':self.phase=='active','rtp_port':self.rtp_port}
    def maintain(self):
        if self.phase=='declined' and time.monotonic()-self.updated>32:self.phase='ended';self.response=None
        if self.call and self.phase in ['ringing','unconfirmed'] and time.monotonic()-self.call['started']>90:self.phase='ended';self.owner=None;self.response=None
        if self.phase=='answering' and self.response:
            if time.monotonic()-self.updated>6:self.owner=None;self.response=None;self.phase='unconfirmed';return
            if time.monotonic()-self.response_at>.7:self.send(self.response,self.call['peer']);self.response_at=time.monotonic()
    def reject(self,owner):
        if self.owner:raise PermissionError('Het gesprek is al aangenomen')
        if not self.call or self.phase not in ['ringing','unconfirmed']:return {'state':self.phase,'declined':False}
        self.response=self.reply(self.call,self.call['peer'],603,'Decline');self.phase='declined';self.updated=time.monotonic()
        return {'state':self.phase,'declined':True}
    def hangup(self,owner):
        if not self.owner:return {'state':self.phase}
        if self.owner!=owner:raise PermissionError('Het gesprek is op een ander toestel aangenomen')
        destination=self.header('contact').strip('<>') or f'sip:deurbel@{CAMERA_IP}:5060'
        payload=(f'BYE {destination} SIP/2.0\r\nVia: SIP/2.0/UDP {SERVER_IP}:5060;branch=z9hG4bK{secrets.token_hex(8)}\r\nMax-Forwards: 70\r\nFrom: {self.header("to")};tag={self.tag}\r\nTo: {self.header("from")}\r\nCall-ID: {self.call["call"]}\r\nCSeq: 1 BYE\r\nContent-Length: 0\r\n\r\n').encode()
        self.send(payload,self.call['peer']);self.phase='ended';self.owner=None;self.response=None;return {'state':self.phase}
