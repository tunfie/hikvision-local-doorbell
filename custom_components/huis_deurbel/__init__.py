"""Authenticated local call and native microphone control; no cloud call dependency."""
import hashlib,json
from pathlib import Path
import voluptuous as vol
from aiohttp import BasicAuth
from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.exceptions import ServiceValidationError,Unauthorized
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.core import SupportsResponse
DOMAIN='huis_deurbel'
CONFIG_SCHEMA=vol.Schema({DOMAIN:vol.Schema({vol.Required('camera_entity'):str,vol.Optional('gateway_url',default='http://127.0.0.1:8099'):str,vol.Optional('voice_url',default='http://127.0.0.1:8100'):str})},extra=vol.ALLOW_EXTRA)

async def async_setup(hass,config):
    settings=config[DOMAIN];camera_entity=settings['camera_entity']
    path=Path(hass.config.path('hikvision_local','voice-control.json'))
    credentials=json.loads(await hass.async_add_executor_job(path.read_text));auth=BasicAuth(credentials['username'],credentials['password'])
    async def request(endpoint,body,voice=False):
        base=settings['voice_url'] if voice else settings['gateway_url']
        async with async_get_clientsession(hass).post(base+endpoint,json=body,auth=auth if voice else None,headers={} if voice else {'X-Huis-Local':'1'},timeout=10) as response:
            if response.status==409:raise ServiceValidationError('Er is al een gesprek op een ander toestel')
            if response.status==504:raise ServiceValidationError('De deurbel bevestigt het aannemen nog niet. Spreken zonder oproep kan via de camera.')
            if response.status!=200:raise ServiceValidationError('De lokale spraakverbinding is niet beschikbaar')
            return await response.json()
    async def control(call):
        if call.context.user_id:
            user=await hass.auth.async_get_user(call.context.user_id)
            if not user or not user.permissions.check_entity(camera_entity,POLICY_CONTROL):raise Unauthorized()
        owner=hashlib.sha256(((call.context.user_id or 'system')+':'+call.data['session_id']).encode()).hexdigest();body={'owner':owner}
        if call.service=='negeren':return await request('/reject',body)
        if call.service=='ophangen':
            await request('/release',body,True)
            return await request('/hangup',body)
        result={'state':'monitor','answered':False,'rtp_port':None}
        if not call.data['monitor']:result=await request('/answer',body)
        try:status=await request('/claim',dict(body,rtp_port=result.get('rtp_port')),True)
        except Exception:
            if result.get('answered'):await request('/hangup',body)
            raise
        return {'state':result['state'],'answered':result.get('answered',False),'conversation':bool(status['active'])}
    schema=vol.Schema({vol.Required('session_id'):vol.All(str,vol.Match(r'^[a-f0-9-]{16,64}$')),vol.Optional('monitor',default=False):bool})
    for name in ['aannemen','ophangen','negeren']:hass.services.async_register(DOMAIN,name,control,schema=schema,supports_response=SupportsResponse.OPTIONAL)
    async def native_webrtc(event):
        from custom_components.frigate.const import CONF_ENABLE_WEBRTC
        for entry in hass.config_entries.async_entries('frigate'):
            if not entry.options.get(CONF_ENABLE_WEBRTC,False):hass.config_entries.async_update_entry(entry,options={**entry.options,CONF_ENABLE_WEBRTC:True})
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED,native_webrtc)
    return True
