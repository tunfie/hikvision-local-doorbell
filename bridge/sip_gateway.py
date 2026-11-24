"""Local Hikvision call notification receiver; never answers calls or opens audio."""
import datetime,hashlib,json,signal,socket,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from sip_dialog import Dialog
from local_settings import CAMERA_IP

def invite_id(data):
    if len(data)>8192:return None
    lines=data.decode('utf-8',errors='replace').split('\r\n')
    if not lines or not lines[0].startswith('INVITE ') or not lines[0].endswith(' SIP/2.0'):return None
    headers={}
    for line in lines[1:]:
        if not line:break
        if ':' in line:
            key,value=line.split(':',1);headers[key.strip().lower()]=value.strip()
    call=headers.get('call-id') or headers.get('i')
    if not call or len(call)>512 or not headers.get('cseq','').upper().endswith(' INVITE'):return None
    return hashlib.sha256(call.encode()).hexdigest()

def run():
    statefile=Path('/state/status.json');configfile=Path('/data/gateway.json');lock=threading.Lock();stop=threading.Event()
    status={'online':True,'armed':False,'last_ring':None,'ring_count':0,'observed_pulses':0,'last_observed_pulse':None,'two_way_audio_ready':True,'source':'Lokale intercomoproep','popup_seconds':60,'deduplicated':0}
    seen={}
    sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);sock.bind(('0.0.0.0',5060));sock.settimeout(1);dialog=Dialog(sock.sendto)
    status['call_state']='idle'
    if statefile.exists():
        saved=json.loads(statefile.read_text())
        for key in ['last_ring','ring_count','observed_pulses','last_observed_pulse']:status[key]=saved.get(key,status[key])
        seen=saved.get('seen',{})
    def persist():
        tmp=statefile.with_suffix('.tmp');tmp.write_text(json.dumps(dict(status,seen=seen)));tmp.replace(statefile)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path not in ['/answer','/hangup','/reject'] or self.headers.get('Content-Type')!='application/json' or self.headers.get('X-Huis-Local')!='1':self.send_error(403);return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<2048:raise ValueError()
                body=json.loads(self.rfile.read(length));owner=body.get('owner','')
                if len(owner)!=64 or any(c not in '0123456789abcdef' for c in owner):raise ValueError()
                with lock:
                    action={'/answer':dialog.answer,'/hangup':dialog.hangup,'/reject':dialog.reject}[self.path];result=action(owner);status['call_state']=dialog.phase
                if self.path=='/answer' and result.get('state')=='answering':
                    deadline=time.monotonic()+5
                    while time.monotonic()<deadline:
                        with lock:
                            if dialog.phase=='active':result={'state':'active','answered':True,'rtp_port':dialog.rtp_port};break
                            if dialog.phase in ['ended','unconfirmed']:break
                        time.sleep(.05)
                    if not result.get('answered'):
                        with lock:dialog.owner=None;dialog.response=None;dialog.phase='unconfirmed';status['call_state']=dialog.phase
                        self.send_error(504,'Doorbell did not confirm answering');return
                payload=json.dumps(result).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
            except PermissionError:self.send_error(409,'Call already controlled by another device')
            except (ValueError,TypeError,KeyError):self.send_error(400)
        def do_GET(self):
            if self.path!='/status':self.send_error(404);return
            with lock:body=json.dumps(status).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('0.0.0.0',8099),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    signal.signal(signal.SIGTERM,lambda *args:stop.set());signal.signal(signal.SIGINT,lambda *args:stop.set())
    print('LOCAL_CALL_RECEIVER_READY no relay control, microphone, audio or cloud connection',flush=True)
    try:
        while not stop.is_set():
            with lock:
                dialog.maintain();status['call_state']=dialog.phase;status['sip_events']=dialog.diagnostics
            settings=json.loads(configfile.read_text());status['armed']=bool(settings.get('enabled'));status['popup_seconds']=max(15,min(300,int(settings.get('popup_seconds',60))))
            try:data,peer=sock.recvfrom(8193)
            except socket.timeout:continue
            if peer[0]!=CAMERA_IP:continue
            with lock:
                identity=dialog.receive(data,peer);status['call_state']=dialog.phase
            if not identity:continue
            with lock:
                stamp=time.time();seen={k:v for k,v in seen.items() if stamp-v<900}
                if identity in seen:status['deduplicated']+=1;continue
                seen[identity]=stamp
                now=datetime.datetime.now(datetime.timezone.utc).isoformat();status['observed_pulses']+=1;status['last_observed_pulse']=now
                if status['armed']:status['last_ring']=now;status['ring_count']+=1
                persist();print('LOCAL_RING',now,'notification_enabled',status['armed'],flush=True)
            # Answer/hangup only after an authenticated dashboard action, never on ring.
    finally:
        if dialog.owner:dialog.hangup(dialog.owner)
        status['online']=False;persist();sock.close();server.shutdown()

if __name__=='__main__':run()
