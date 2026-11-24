# Hikvision Local Doorbell — development preview

Local SIP call receiver, native microphone bridge and Home Assistant controls.

The original home installation was tested with **DS-KB8112-IM, firmware V1.4.5
build 190219**: local incoming call, live image/listening, answer, microphone,
hangup and decline. Decline also stopped the existing Hik-Connect call.
The generalized package in this repository has not yet been installed from
scratch. This is not a ready HACS release or a promise of other-model support.

## Components

- `bridge/sip_gateway.py`: camera-only SIP, immediate 100/180, final answer on
  user action, ACK tracking, BYE and 603 decline.
- `bridge/native_voice_server.py`: separate authenticated control/media routes.
- `bridge/native_voice.py`: explicitly enabled G.711 microphone data to HCNetSDK.
- `custom_components/huis_deurbel`: HA camera-control permission checks and
  `aannemen`, `ophangen`, `negeren` services.
- `frontend/huis-deurbel-live.js`: underlying WebRTC player, not a standalone
  Lovelace card; a host calls `setContext(hass, camera_entity)` and assigns
  `manual` and `statusEntity`. Standalone card/popup packaging remains open.

Frigate/go2rtc and its HA integration provide video/listening. The bridge stores
no audio. Browser microphone access is explicit. Browser autoplay policy may
require a first touch before sound can start.

## Configuration outline

Copy to a Linux Docker host and run `python3 tools/configure.py` locally.
Generated `.env`, credentials and state must stay outside Git; files must be
owned by container uid 1000. Configure the camera's local intercom/SIP target to
the server after backing up the original camera settings. Other firmware may
need different settings.

Copy the custom component into HA, configure its camera entity and copy the
generated control-only credentials to `/config/hikvision_local/voice-control.json`.
Add a go2rtc return-audio source to the private voice bridge using its separate
media credentials; retain the original RTSP camera source.

Do not expose SIP, gateway control or the voice bridge to the internet. The
gateway trusts camera IPv4 and loopback control; it is not an internet-facing
authenticated SIP server. Use HTTPS for browser microphone access and VPN for
remote private access. HA on a different host requires an authenticated
transport arrangement and is outside this draft.

## Validation and remaining release work

`python -m unittest discover -s tests -v` runs offline protocol tests. CI also
checks Python syntax. Fresh Compose installation, isolated credentials,
standalone frontend packaging and full lifecycle tests remain before release.

## Third-party dependency

The native bridge imports SDK bindings from
[pergolafabio/Hikvision-Addons](https://github.com/pergolafabio/Hikvision-Addons).
No SDK binaries or binding modules are included in this repository. The base
image and Hikvision HCNetSDK retain their own terms. Their redistribution rights
must be checked before publishing a derived image. Original bridge and integration code is available under the MIT licence in
LICENSE. Third-party SDK/image terms remain separate; the SDK is not declared
open source by this repository.
