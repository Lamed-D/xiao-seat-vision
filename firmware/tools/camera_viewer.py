#!/usr/bin/env python3
"""Live camera check in the browser over USB serial — no Wi-Fi, MQTT or Docker needed.

    pip install pyserial
    python firmware/tools/camera_viewer.py COM5
    -> open http://127.0.0.1:8000

Shows the latest frame (refreshed continuously), frame size/FPS, the device status
('s' command: PSRAM, camera sensor, ...) and the firmware log. "사진 저장" saves the
current frame to ./snapshots (also useful for Edge Impulse datasets) and the ROI
overlay draws the seats from seats_config.h so you can check the layout.

Close the Arduino IDE Serial Monitor first (only one program can open the port).
Docker Desktop on Windows cannot pass USB serial ports to containers, which is why this
runs directly on the PC.
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import serial  # pyserial

from capture_snapshot import capture

SEATS_FILE = Path(__file__).resolve().parents[1] / "xiao_seat_vision" / "seats_config.h"
SEAT_RE = re.compile(
    r'\{\s*"([^"]+)"\s*,\s*"([^"]*)"\s*,\s*([\d.]+)f?\s*,\s*([\d.]+)f?\s*,\s*([\d.]+)f?\s*,\s*([\d.]+)f?\s*\}'
)

PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>XIAO 카메라 확인</title>
<style>
body{font-family:system-ui,"Noto Sans KR",sans-serif;margin:16px;background:#f5f6f8;color:#1d232b}
main{max-width:980px;margin:auto;display:grid;gap:12px}
.card{background:#fff;border:1px solid #e2e5ea;border-radius:10px;padding:12px}
.wrap{position:relative;line-height:0;max-width:640px}
.wrap img{width:100%;image-rendering:pixelated;border-radius:6px;background:#222;min-height:200px}
svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
button{padding:8px 14px;border:0;border-radius:6px;background:#1c64d8;color:#fff;cursor:pointer}
button.ghost{background:#fff;color:#1d232b;border:1px solid #ccd}
pre{background:#111;color:#cfe;padding:8px;border-radius:6px;max-height:260px;overflow:auto;font-size:12px;line-height:1.35;white-space:pre-wrap}
.ok{color:#2b8a3e;font-weight:600}.bad{color:#e03131;font-weight:600}.muted{color:#6b7480;font-size:13px}
</style></head><body><main>
<h2>XIAO ESP32S3 Sense 카메라 확인</h2>
<div class="card"><div class="wrap"><img id="img" alt="frame"><svg id="roi" viewBox="0 0 1 1" preserveAspectRatio="none"></svg></div>
<div class="row" style="margin-top:8px">
<button id="pause">일시정지</button><button id="save">사진 저장</button>
<label><input type="checkbox" id="showRoi" checked> 좌석 ROI 표시</label>
<button class="ghost" id="status">장치 상태 새로고침</button></div>
<p id="info" class="muted">연결 중...</p></div>
<div class="card"><b>점검 결과</b><div id="checks" class="muted">상태 정보를 기다리는 중...</div></div>
<div class="card"><b>펌웨어 로그</b><pre id="log"></pre></div>
</main><script>
let paused=false, seats=[];
const $=id=>document.getElementById(id);
function drawRoi(){const s=$("roi");s.innerHTML="";if(!$("showRoi").checked)return;
 for(const r of seats){const e=document.createElementNS("http://www.w3.org/2000/svg","rect");
  e.setAttribute("x",r.x);e.setAttribute("y",r.y);e.setAttribute("width",r.w);e.setAttribute("height",r.h);
  e.setAttribute("fill","rgba(77,171,247,.12)");e.setAttribute("stroke","#4dabf7");e.setAttribute("stroke-width",".005");s.append(e);
  const t=document.createElementNS("http://www.w3.org/2000/svg","text");t.setAttribute("x",r.x+.01);t.setAttribute("y",r.y+.05);
  t.setAttribute("font-size",".045");t.setAttribute("fill","#4dabf7");t.textContent=r.id;s.append(t);}}
async function tick(){
 try{const st=await (await fetch("/api/state")).json();
  if(!paused&&st.frame_id){$("img").src="/frame.jpg?id="+st.frame_id;}
  $("info").textContent=st.error?("오류: "+st.error):`프레임 ${st.width}x${st.height}, ${st.bytes} bytes, ${st.fps.toFixed(1)} fps (USB 전송 속도 포함), 총 ${st.frames}장`;
  $("log").textContent=st.log.join("\\n");$("log").scrollTop=1e9;
  const c=[];const L=st.log.join("\\n");
  const ps=L.match(/\\[mem\\] psram: total=(\\d+)/), nops=/psram: NOT FOUND/.test(L);
  c.push(ps?`<span class=ok>✔ PSRAM ${(ps[1]/1048576).toFixed(1)} MB</span>`:nops?`<span class=bad>✘ PSRAM 꺼짐 → Arduino IDE Tools > PSRAM > OPI PSRAM 후 다시 업로드</span>`:"PSRAM: 확인 중");
  const cam=L.match(/camera=(ready|NOT ready) sensor=(\\S+)/)||L.match(/\\[camera\\] ready: sensor=(\\S+)/);
  c.push(cam?(cam[1]==="NOT ready"?`<span class=bad>✘ 카메라 준비 안 됨</span>`:`<span class=ok>✔ 카메라 센서 ${cam[2]||cam[1]}</span>`):"카메라: 확인 중");
  c.push(st.frames>0?`<span class=ok>✔ 프레임 수신 (${st.width}x${st.height})</span>`:"프레임: 대기 중");
  if(st.width&&st.width<320)c.push(`<span class=bad>✘ 해상도 ${st.width}x${st.height}: PSRAM이 꺼져 축소 모드</span>`);
  if(/FB-OVF/.test(L))c.push(`<span class=bad>! cam_hal FB-OVF 발생 (프레임 버퍼 초과)</span>`);
  $("checks").innerHTML=c.join("<br>");
 }catch(e){$("info").textContent="뷰어 서버 연결 끊김";}
 setTimeout(tick,500);}
$("pause").onclick=()=>{paused=!paused;$("pause").textContent=paused?"재개":"일시정지";};
$("save").onclick=async()=>{const r=await (await fetch("/api/save",{method:"POST"})).json();alert(r.saved?("저장됨: "+r.saved):("실패: "+r.error));};
$("status").onclick=()=>fetch("/api/status",{method:"POST"});
$("showRoi").onchange=drawRoi;
fetch("/api/seats").then(r=>r.json()).then(s=>{seats=s;drawRoi();});
tick();
</script></body></html>"""


class Viewer:
    def __init__(self, port_name: str, out_dir: Path, interval: float) -> None:
        self.port_name = port_name
        self.out_dir = out_dir
        self.interval = interval
        self.lock = threading.Lock()
        self.frame: bytes | None = None
        self.meta = {"frame_id": 0, "width": 0, "height": 0, "bytes": 0, "fps": 0.0, "frames": 0, "error": ""}
        self.log: deque[str] = deque(maxlen=200)
        self.want_status = threading.Event()
        self.want_status.set()  # ask once at start
        self.stop = threading.Event()

    def add_log(self, line: str) -> None:
        with self.lock:
            self.log.append(line)

    def run_serial(self) -> None:
        while not self.stop.is_set():
            try:
                with serial.Serial(self.port_name, 115200, timeout=1) as port:
                    self.add_log(f"[viewer] {self.port_name} opened")
                    time.sleep(0.5)
                    port.reset_input_buffer()
                    last = time.monotonic()
                    while not self.stop.is_set():
                        if self.want_status.is_set():
                            self.want_status.clear()
                            port.write(b"s")
                        jpeg, w, h = capture(port, on_log=self.add_log)
                        now = time.monotonic()
                        with self.lock:
                            self.frame = jpeg
                            fps = 1.0 / max(now - last, 1e-3)
                            m = self.meta
                            m.update(frame_id=m["frame_id"] + 1, width=w, height=h, bytes=len(jpeg),
                                     frames=m["frames"] + 1, error="",
                                     fps=fps if m["fps"] == 0 else m["fps"] * 0.8 + fps * 0.2)
                        last = now
                        self.stop.wait(self.interval)
            except serial.SerialException as exc:
                self.set_error(f"시리얼 포트를 열 수 없음 ({exc}). Arduino 시리얼 모니터를 닫았는지 확인하세요.")
                self.stop.wait(3)
            except (TimeoutError, RuntimeError, ValueError) as exc:
                self.set_error(str(exc))
                self.stop.wait(1)

    def set_error(self, msg: str) -> None:
        with self.lock:
            self.meta["error"] = msg
        self.add_log(f"[viewer] {msg}")

    def save(self) -> dict:
        with self.lock:
            frame = self.frame
        if not frame:
            return {"error": "아직 프레임이 없습니다"}
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"{time.strftime('%Y%m%d-%H%M%S')}.jpg"
        path.write_bytes(frame)
        return {"saved": str(path)}


def load_seats() -> list[dict]:
    try:
        text = SEATS_FILE.read_text(encoding="utf-8")
    except OSError:
        return []
    return [
        {"id": m[0], "label": m[1], "x": float(m[2]), "y": float(m[3]), "w": float(m[4]), "h": float(m[5])}
        for m in SEAT_RE.findall(text)
    ]


def make_handler(viewer: Viewer):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("content-type", ctype)
            self.send_header("cache-control", "no-store")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj) -> None:
            self._send(200, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_GET(self):  # noqa: N802
            path = self.path.split("?")[0]
            if path == "/":
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            elif path == "/frame.jpg":
                with viewer.lock:
                    frame = viewer.frame
                if frame:
                    self._send(200, frame, "image/jpeg")
                else:
                    self._send(404, b"no frame yet", "text/plain")
            elif path == "/api/state":
                with viewer.lock:
                    self._json({**viewer.meta, "log": list(viewer.log)[-80:]})
            elif path == "/api/seats":
                self._json(load_seats())
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):  # noqa: N802
            if self.path == "/api/save":
                self._json(viewer.save())
            elif self.path == "/api/status":
                viewer.want_status.set()
                self._json({"ok": True})
            else:
                self._send(404, b"not found", "text/plain")

        def log_message(self, *args):
            pass

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port", help="serial port, e.g. COM5 or /dev/ttyACM0")
    ap.add_argument("--http-port", type=int, default=8000)
    ap.add_argument("--interval", type=float, default=0.2, help="pause between frames (s)")
    ap.add_argument("--out", default="snapshots", help="folder for saved photos")
    args = ap.parse_args()

    viewer = Viewer(args.port, Path(args.out), args.interval)
    threading.Thread(target=viewer.run_serial, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", args.http_port), make_handler(viewer))
    print(f"camera viewer: http://127.0.0.1:{args.http_port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        viewer.stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
