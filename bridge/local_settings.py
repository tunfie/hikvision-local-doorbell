"""Explicit local IPv4 configuration; validated before constructing SIP headers."""
import ipaddress,os
CAMERA_IP=str(ipaddress.IPv4Address(os.environ['HUIS_CAMERA_IP']))
SERVER_IP=str(ipaddress.IPv4Address(os.environ['HUIS_SERVER_IP']))
