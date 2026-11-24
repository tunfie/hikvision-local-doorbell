"""Forward explicitly enabled G.711 microphone frames to the camera's native SDK."""
import ctypes,json,threading,time,socket,struct,secrets
from pathlib import Path

class NativeVoice:
    def __init__(self,credentials):
        self.ready=threading.Event()
        self.credentials=credentials;self.lock=threading.RLock();self.sdk=None;self.uid=-1;self.handle=-1;self.owner=None;self.touched=0;self.bytes_sent=0;self.errors=0;self.last_error=0;self.generation=0;self.rtp_socket=None;self.rtp_port=None;self.seq=secrets.randbits(16);self.timestamp=secrets.randbits(32);self.ssrc=secrets.randbits(32)
    def connect(self):
        if self.uid>=0:return
        from sdk.utils import loadSDK,setupSDK
        from sdk.hcnetsdk import NET_DVR_DEVICEINFO_V30
        self.sdk=loadSDK();setupSDK(self.sdk);self.sdk.NET_DVR_SetConnectTime(4000,1)
        info=NET_DVR_DEVICEINFO_V30();cfg=self.credentials
        self.uid=self.sdk.NET_DVR_Login_V30(cfg['ip'].encode(),8000,cfg['username'].encode(),cfg['password'].encode(),ctypes.byref(info))
        if self.uid<0:raise RuntimeError('Deurbel niet bereikbaar voor lokaal spreken')
        self.CB=ctypes.CFUNCTYPE(None,ctypes.c_int32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_ubyte,ctypes.c_void_p);self.callback=self.CB(lambda *args:None)
        self.sdk.NET_DVR_StartVoiceCom_MR_V30.argtypes=[ctypes.c_int32,ctypes.c_uint32,self.CB,ctypes.c_void_p];self.sdk.NET_DVR_StartVoiceCom_MR_V30.restype=ctypes.c_int32
        self.sdk.NET_DVR_VoiceComSendData.argtypes=[ctypes.c_int32,ctypes.c_void_p,ctypes.c_uint32];self.sdk.NET_DVR_VoiceComSendData.restype=ctypes.c_int32
    def claim(self,owner,rtp_port=None):
        with self.lock:
            self.expire()
            if self.owner and self.owner!=owner:raise PermissionError('Er is al een gesprek op een ander toestel')
            if rtp_port is not None and (not isinstance(rtp_port,int) or not 1024<=rtp_port<=65535):raise ValueError('Ongeldige deurbelpoort')
            if self.owner==owner and self.rtp_port!=rtp_port:self.close()
            self.owner=owner;self.touched=time.monotonic();self.rtp_port=rtp_port
            try:self.open();self.ready.set()
            except Exception:self.owner=None;self.ready.clear();raise
            return self.status()
    def open(self):
        with self.lock:
            if not self.owner:raise PermissionError('Druk eerst op Praten in het dashboard')
            if self.handle>=0 or self.rtp_socket:return
            if self.rtp_port:
                self.rtp_socket=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);self.rtp_socket.bind(('0.0.0.0',40000));self.generation+=1;self.bytes_sent=0;return
            self.connect()
            for attempt in range(4):
                self.handle=self.sdk.NET_DVR_StartVoiceCom_MR_V30(self.uid,1,self.callback,None)
                if self.handle>=0:break
                self.last_error=self.sdk.NET_DVR_GetLastError()
                if self.last_error not in [29,31]:break
                time.sleep(.15)
            if self.handle<0:
                self.last_error=self.sdk.NET_DVR_GetLastError();raise RuntimeError('Deurbel accepteert het spraakkanaal niet')
            self.generation+=1;self.touched=time.monotonic();self.bytes_sent=0;self.errors=0;self.last_error=0
    def send(self,payload,generation=None):
        with self.lock:
            if generation is not None and generation!=self.generation:raise RuntimeError('Oude audioverbinding')
            if not self.owner:raise RuntimeError('Gesprek is gesloten')
            if self.rtp_socket:
                header=struct.pack('!BBHII',0x80,0,self.seq,self.timestamp,self.ssrc);self.rtp_socket.sendto(header+payload,(self.credentials['ip'],self.rtp_port));self.seq=(self.seq+1)&65535;self.timestamp=(self.timestamp+len(payload))&0xffffffff;self.bytes_sent+=len(payload);self.touched=time.monotonic();return
            if self.handle<0:raise RuntimeError('Gesprek is gesloten')
            buffer=ctypes.create_string_buffer(payload)
            if not self.sdk.NET_DVR_VoiceComSendData(self.handle,buffer,len(payload)):
                self.errors+=1;self.last_error=self.sdk.NET_DVR_GetLastError();raise RuntimeError('Spraak niet afgeleverd bij de deurbel')
            self.bytes_sent+=len(payload);self.touched=time.monotonic()
    def close(self,release=False,owner=None,generation=None):
        with self.lock:
            if generation is not None and generation!=self.generation:return
            if owner and self.owner and owner!=self.owner:raise PermissionError('Gesprek hoort bij een ander toestel')
            if self.handle>=0:self.sdk.NET_DVR_StopVoiceCom(self.handle);self.handle=-1;self.generation+=1
            if self.rtp_socket:self.rtp_socket.close();self.rtp_socket=None;self.generation+=1
            if release:self.owner=None;self.rtp_port=None;self.touched=time.monotonic();self.ready.clear()
    def release_sdk(self):
        """Free native background workers only when no conversation owns them."""
        with self.lock:
            if self.owner or self.handle>=0 or self.rtp_socket:return
            if self.uid>=0:self.sdk.NET_DVR_Logout_V30(self.uid);self.uid=-1
            if self.sdk:
                from sdk.utils import shutdownSDK
                shutdownSDK(self.sdk);self.sdk=None;self.generation+=1
                self.callback=None;self.CB=None
    def expire(self):
        with self.lock:
            if self.owner and time.monotonic()-self.touched>45:self.close(release=True)
            if not self.owner and self.sdk and time.monotonic()-self.touched>30:self.release_sdk()
    def status(self):
        with self.lock:return {'active':self.handle>=0 or self.rtp_socket is not None,'claimed':bool(self.owner),'bytes_sent':self.bytes_sent,'send_errors':self.errors,'last_error':self.last_error,'transport':'rtp' if self.rtp_socket else 'native_sdk','sdk_loaded':self.sdk is not None,'standby_after_seconds':30}
    def shutdown(self):
        with self.lock:
            self.close(release=True)
            self.release_sdk()
