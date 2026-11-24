"""Private native-voice bridge for HA control and go2rtc G.711 audio; no audio storage."""
import base64,hmac,json,signal,threading,time,hashlib,secrets,socket
from urllib.request import parse_http_list,parse_keqv_list
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from native_voice import NativeVoice
config=json.loads(Path('/data/voice.json').read_text());voice=NativeVoice(config['camera']);stop=threading.Event();nonce_key=secrets.token_bytes(32);media_connections={};media_lock=threading.Lock()
request_counts={};request_lock=threading.Lock()
def close_media_connections(generation=None):
    with media_lock:
        keys=[key for key in media_connections if generation is None or key<=generation];connections=[media_connections.pop(key) for key in keys]
    for connection in connections:
        try:connection.shutdown(socket.SHUT_RDWR)
        except OSError:pass
def digest_nonce():
    stamp=format(int(time.time()),'x');return stamp+':'+hmac.new(nonce_key,stamp.encode(),hashlib.sha256).hexdigest()
def valid_digest(header,method,path):
    try:
        p=parse_keqv_list(parse_http_list(header[7:]));stamp,signature=p['nonce'].split(':',1)
        if abs(time.time()-int(stamp,16))>300:return False
        if not hmac.compare_digest(signature,hmac.new(nonce_key,stamp.encode(),hashlib.sha256).hexdigest()):return False
        if p.get('username')!=config['media']['username'] or p.get('realm')!='HuisVoice' or p.get('uri')!=path or p.get('qop')!='auth':return False
        if len(p.get('cnonce',''))>128 or len(p.get('nc',''))!=8:return False
        md5=lambda text:hashlib.md5(text.encode()).hexdigest()
        ha1=md5(config['media']['username']+':HuisVoice:'+config['media']['password']);ha2=md5(method+':'+path)
        expected=md5(':'.join([ha1,p['nonce'],p['nc'],p['cnonce'],'auth',ha2]));return hmac.compare_digest(p.get('response',''),expected)
    except (ValueError,KeyError,TypeError):return False
class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def role(self):
        known={'/status','/claim','/release','/ISAPI/System/TwoWayAudio/channels','/ISAPI/System/TwoWayAudio/channels/1/open','/ISAPI/System/TwoWayAudio/channels/1/close','/ISAPI/System/TwoWayAudio/channels/1/audioData'}
        endpoint=self.path if self.path in known else 'other'
        with request_lock:
            key=self.command+' '+endpoint;request_counts[key]=request_counts.get(key,0)+1
        header=self.headers.get('Authorization','')
        for role in ['control','media']:
            value=config[role];expected='Basic '+base64.b64encode((value['username']+':'+value['password']).encode()).decode()
            if hmac.compare_digest(header,expected):return role
        if self.path.startswith('/ISAPI/') and header.startswith('Digest ') and valid_digest(header,self.command,self.path):return 'media'
        challenge='Digest realm="HuisVoice", nonce="'+digest_nonce()+'", algorithm=MD5, qop="auth"' if self.path.startswith('/ISAPI/') else 'Basic realm="HuisVoice"'
        self.send_response(401);self.send_header('WWW-Authenticate',challenge);self.send_header('Content-Length','0');self.send_header('Connection','close');self.end_headers();return None
    def reply(self,data=b'',content_type='application/json',status=200,streaming=False,retry_after=None):
        if not isinstance(data,bytes):data=json.dumps(data).encode()
        self.send_response(status);self.send_header('Content-Type',content_type);self.send_header('Connection','close')
        if retry_after is not None:self.send_header('Retry-After',str(retry_after))
        if not streaming:self.send_header('Content-Length',str(len(data)))
        self.end_headers();self.wfile.write(data);self.wfile.flush()
    def do_GET(self):
        role=self.role()
        if not role:return
        if self.path=='/status' and role=='control':
            with request_lock:counts=dict(request_counts)
            self.reply(dict(voice.status(),requests=counts));return
        if self.path=='/ISAPI/System/TwoWayAudio/channels' and role=='media':
            self.reply(b'<TwoWayAudioChannelList><TwoWayAudioChannel><id>1</id><enabled>true</enabled><audioCompressionType>G.711ulaw</audioCompressionType></TwoWayAudioChannel></TwoWayAudioChannelList>','application/xml');return
        self.reply(status=403)
    def do_POST(self):
        role=self.role()
        if not role:return
        if role!='control' or self.path not in ['/claim','/release']:self.reply(status=403);return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<1024:raise ValueError()
            body=json.loads(self.rfile.read(length));owner=body.get('owner','')
            if len(owner)!=64 or any(c not in '0123456789abcdef' for c in owner):raise ValueError()
            if self.path=='/claim':result=voice.claim(owner,body.get('rtp_port'))
            else:
                generation=voice.generation;voice.close(release=True,owner=owner);close_media_connections(generation);result=voice.status()
            self.reply(result)
        except PermissionError:self.reply({'error':'Gesprek is op een ander toestel'},status=409)
        except (ValueError,TypeError):self.reply(status=400)
        except RuntimeError as error:self.reply({'error':str(error)},status=503)
    def do_PUT(self):
        role=self.role()
        if not role:return
        if role!='media':self.reply(status=403);return
        base='/ISAPI/System/TwoWayAudio/channels/1'
        try:
            if self.path==base+'/open':
                # go2rtc reconnects aggressively after an EOF. Wait without CPU
                # until a dashboard claim wakes us, then return a real HTTP error
                # if no owner exists. An authorized claim wakes this immediately.
                voice.ready.wait(5)
                voice.open();self.reply(b'<ResponseStatus><statusCode>1</statusCode><statusString>OK</statusString></ResponseStatus>','application/xml')
            elif self.path==base+'/close':
                generation=voice.generation;voice.close();close_media_connections(generation);self.reply(b'<ResponseStatus><statusCode>1</statusCode><statusString>OK</statusString></ResponseStatus>','application/xml')
            elif self.path==base+'/audioData':
                voice.open();generation=voice.generation
                with media_lock:media_connections[generation]=self.connection
                self.reply(content_type='application/octet-stream',streaming=True);self._voice_streaming=True;self.connection.settimeout(10)
                try:
                    while not stop.is_set():
                        data=self.rfile.read(160)
                        if not data:break
                        voice.send(data,generation)
                finally:
                    voice.close(release=True,generation=generation)
                    with media_lock:media_connections.pop(generation,None)
                    self.close_connection=True
            else:self.reply(status=404)
        except PermissionError:
            if not getattr(self,'_voice_streaming',False):self.reply({'error':'No active conversation; microphone is disabled'},status=423,retry_after=5)
            self.close_connection=True
        except RuntimeError:
            if not getattr(self,'_voice_streaming',False):self.reply({'error':'Native voice channel is unavailable'},status=503,retry_after=5)
            self.close_connection=True
        except (TimeoutError,OSError):self.close_connection=True
    def log_message(self,*args):pass
server=ThreadingHTTPServer(('0.0.0.0',8100),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
signal.signal(signal.SIGTERM,lambda *args:stop.set());signal.signal(signal.SIGINT,lambda *args:stop.set())
print('NATIVE_VOICE_READY: private authenticated routes; microphone audio only after dashboard action; no audio storage',flush=True)
try:
    while not stop.wait(1):voice.expire()
finally:voice.shutdown();close_media_connections();server.shutdown()
