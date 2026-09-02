import http.server, re, sys, threading
sys.path.insert(0, "/app/9x_security")
import ptz

PROFILES_RESP = """<?xml version="1.0"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
<s:Body><trt:GetProfilesResponse xmlns:trt="http://www.onvif.org/ver10/media/wsdl">
<trt:Profiles token="Profile_1" fixed="true"><tt:Name xmlns:tt="http://www.onvif.org/ver10/schema">main</tt:Name></trt:Profiles>
</trt:GetProfilesResponse></s:Body></s:Envelope>"""

MOVE_RESP = """<?xml version="1.0"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
<s:Body><tptz:ContinuousMoveResponse xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"/></s:Body></s:Envelope>"""

reqs = []
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        reqs.append((self.path, body))
        if "GetProfiles" in body:
            resp = PROFILES_RESP
        else:
            resp = MOVE_RESP
        self.send_response(200)
        self.send_header("Content-Type", "application/soap+xml")
        self.end_headers()
        self.wfile.write(resp.encode())
    def log_message(self, *a): pass

srv = http.server.HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_port

# route discovery to our mock port only
ptz._PORTS = (port,)
ptz.reset_cache()

rtsp = "rtsp://admin:Admin@123@127.0.0.1:554/stream1"

# 1. creds parse (incl @ in password)
host, user, pw = ptz.creds_from_rtsp(rtsp)
assert (host, user, pw) == ("127.0.0.1", "admin", "Admin@123"), (host, user, pw)
print("PASS 1: creds parsed from RTSP url (@ in password handled)")

# 2. zoom start -> discovery + ContinuousMove with profile token & velocity
ok, supported, detail = ptz.zoom(rtsp, "in", "start")
assert ok and supported, (ok, supported, detail)
move = [b for _, b in reqs if "ContinuousMove" in b][-1]
assert "Profile_1" in move and 'x="0.5"' in move
assert "PasswordDigest" in move and "Nonce" in move, "WS-Security header missing"
print("PASS 2: zoom-in start -> ContinuousMove x=0.5 with WS-UsernameToken auth")

# 3. zoom out uses -0.5, stop sends Stop with Zoom=true
ok, _, _ = ptz.zoom(rtsp, "out", "start")
assert 'x="-0.5"' in [b for _, b in reqs if "ContinuousMove" in b][-1]
ok, _, _ = ptz.zoom(rtsp, "in", "stop")
assert "<Zoom>true</Zoom>" in [b for _, b in reqs if "<Stop" in b][-1]
print("PASS 3: zoom-out velocity -0.5; stop sends Stop Zoom=true")

# 4. discovery cached (GetProfiles called once)
n_prof = len([b for _, b in reqs if "GetProfiles" in b])
assert n_prof == 1, f"GetProfiles called {n_prof}x, expected 1 (cache)"
print("PASS 4: ONVIF discovery cached (single GetProfiles)")

# 5. unreachable camera -> supported=False, friendly detail
ptz._PORTS = (9,)
ptz.reset_cache()
ok, supported, detail = ptz.zoom("rtsp://u:p@127.0.0.1:554/s", "in", "start")
assert not ok and not supported and "Digital Zoom" in detail
print("PASS 5: no ONVIF -> supported=False with Digital Zoom hint")

# 6. no host in url
ok, supported, detail = ptz.zoom("", "in", "start")
assert not ok and not supported
print("PASS 6: empty RTSP url handled")

print("ALL PTZ TESTS PASSED")
