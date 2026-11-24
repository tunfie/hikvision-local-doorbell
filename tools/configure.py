"""Run locally on the Linux Docker host; never commit generated credentials."""
import getpass,ipaddress,json,os,secrets
from pathlib import Path
root=Path(__file__).resolve().parents[1]
if (root/'config/voice.json').exists():raise SystemExit('Existing credentials found; refusing to overwrite')
camera=str(ipaddress.IPv4Address(input('Doorbell IPv4: ').strip()))
server=str(ipaddress.IPv4Address(input('Docker server LAN IPv4: ').strip()))
username=input('Camera username: ').strip();password=getpass.getpass('Camera password: ')
if not username or not password:raise SystemExit('Camera credentials are required')
os.umask(0o077)
(root/'config').mkdir(exist_ok=True);(root/'state').mkdir(exist_ok=True)
control={'username':'ha_doorbell_control','password':secrets.token_urlsafe(32)}
media={'username':'go2rtc_doorbell_media','password':secrets.token_urlsafe(32)}
(root/'config/voice.json').write_text(json.dumps({'camera':{'ip':camera,'username':username,'password':password},'control':control,'media':media}))
(root/'config/gateway.json').write_text(json.dumps({'enabled':True,'popup_seconds':60}))
(root/'config/voice-control.json').write_text(json.dumps(control))
(root/'.env').write_text(f'HUIS_CAMERA_IP={camera}\nHUIS_SERVER_IP={server}\nDOCKER_GATEWAY=172.17.0.1\n')
print('Generated local configuration. Review uid 1000 ownership before starting containers.')
