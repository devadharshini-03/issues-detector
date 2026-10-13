"""Local review dashboard for incidents recorded by smart_city_detector.py."""

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sqlite3
from urllib.parse import unquote, urlparse


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Smart City Incidents</title>
<style>
:root{color-scheme:light;--ink:#16232d;--muted:#65737e;--line:#dce3e8;--accent:#176b5b;--paper:#f4f7f8}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.45 system-ui,Segoe UI,Arial,sans-serif}
header{background:#102b35;color:#fff;padding:22px max(20px,calc((100vw - 1180px)/2))}
header h1{margin:0;font-size:24px}header p{margin:5px 0 0;color:#c6d8dc}
main{max-width:1180px;margin:22px auto;padding:0 18px}.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:16px}
.summary{color:var(--muted);margin-right:auto}select,button{font:inherit;border:1px solid var(--line);border-radius:8px;padding:8px 10px;background:#fff;color:var(--ink)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}
article{background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden;box-shadow:0 2px 8px #102b3510}
.photos{display:grid;grid-template-columns:1fr 1fr;gap:3px;background:#e7edef}.photos img{width:100%;height:190px;object-fit:cover}
.body{padding:14px}.body h2{font-size:18px;margin:0 0 5px}.meta{color:var(--muted);font-size:13px;margin:4px 0}
.actions{display:flex;align-items:center;gap:8px;margin-top:12px}.map{color:var(--accent);font-weight:600;text-decoration:none}
.empty,.error{background:#fff;border:1px solid var(--line);border-radius:10px;padding:22px;color:var(--muted)}
@media(max-width:480px){.photos img{height:145px}}
</style>
</head>
<body>
<header><h1>Smart City Incident Review</h1><p>Local incident log from the road detector</p></header>
<main>
<div class="toolbar"><div class="summary" id="summary">Loading incidents…</div>
<label for="filter">Show:</label><select id="filter"><option value="all">All incidents</option><option value="new">New</option><option value="reviewed">Reviewed</option><option value="resolved">Resolved</option></select>
<button id="refresh">Refresh</button></div>
<div class="grid" id="incidents"></div>
</main>
<script>
const grid=document.getElementById("incidents"), summary=document.getElementById("summary"), filter=document.getElementById("filter");
function text(tag,value,className){const node=document.createElement(tag);node.textContent=value;if(className)node.className=className;return node;}
function media(name){return "/media/"+encodeURIComponent(name);}
async function load(){
  grid.replaceChildren(); summary.textContent="Loading incidents…";
  try{
    const response=await fetch("/api/incidents"); if(!response.ok)throw new Error("Could not load incidents");
    const data=await response.json();
    const selected=filter.value==="all"?data.incidents:data.incidents.filter(x=>x.status===filter.value);
    summary.textContent=data.counts.new+" new · "+data.counts.reviewed+" reviewed · "+data.counts.resolved+" resolved";
    if(!selected.length){grid.append(text("div","No incidents match this filter.","empty"));return;}
    for(const item of selected){
      const card=document.createElement("article"), photos=document.createElement("div"), body=document.createElement("div");
      photos.className="photos";
      for(const file of [item.snapshot_path,item.crop_path]){
        const img=document.createElement("img");img.src=media(file);img.alt=item.issue_type+" evidence";photos.append(img);
      }
      body.className="body";
      body.append(text("h2","#"+item.id+" · "+item.issue_type));
      body.append(text("div",item.detected_at,"meta"));
      body.append(text("div",item.label+" · confidence "+Math.round(item.confidence*100)+"%","meta"));
      body.append(text("div","Source: "+item.source,"meta"));
      body.append(text("div",item.location_source+(item.accuracy_m===null?"":" · ±"+Math.round(item.accuracy_m)+" m"),"meta"));
      if(item.latitude!==null&&item.longitude!==null){
        const map=document.createElement("a");map.className="map";map.href="https://maps.google.com/?q="+item.latitude+","+item.longitude;
        map.target="_blank";map.rel="noopener noreferrer";map.textContent="Open map";body.append(map);
      }
      const actions=document.createElement("div"), status=document.createElement("select");actions.className="actions";
      for(const value of ["new","reviewed","resolved"]){const option=document.createElement("option");option.value=value;option.textContent=value[0].toUpperCase()+value.slice(1);status.append(option);}
      status.value=item.status;status.setAttribute("aria-label","Incident status");
      status.addEventListener("change",async()=>{
        status.disabled=true;
        try{const result=await fetch("/api/incidents/"+item.id+"/status",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({status:status.value})});if(!result.ok)throw new Error("Update failed");await load();}
        catch(error){status.disabled=false;alert(error.message);}
      });
      actions.append(status);body.append(actions);card.append(photos,body);grid.append(card);
    }
  }catch(error){summary.textContent="";grid.append(text("div",error.message,"error"));}
}
document.getElementById("refresh").addEventListener("click",load);
filter.addEventListener("change",load);load();setInterval(load,20000);
</script>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    database_path = None
    media_dir = None

    def _send(self, status, body, content_type):
        payload = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status, value):
        self._send(status, json.dumps(value), "application/json; charset=utf-8")

    def do_GET(self):
        route = urlparse(self.path).path
        if route == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
            return
        if route == "/api/incidents":
            try:
                with sqlite3.connect(self.database_path) as connection:
                    connection.row_factory = sqlite3.Row
                    rows = connection.execute(
                        "SELECT * FROM incidents ORDER BY id DESC LIMIT 500"
                    ).fetchall()
                    counts = {row["status"]: row["count"] for row in connection.execute(
                        "SELECT status, COUNT(*) AS count FROM incidents GROUP BY status"
                    )}
                counts = {key: counts.get(key, 0) for key in ("new", "reviewed", "resolved")}
                incidents = [dict(row) for row in rows]
                self._json(200, {"incidents": incidents, "counts": counts})
            except sqlite3.Error as exc:
                self._json(500, {"error": "Incident database is unavailable", "detail": str(exc)})
            return
        if route.startswith("/media/"):
            name = unquote(route[len("/media/"):])
            if not name or Path(name).name != name or "/" in name or "\\" in name:
                self.send_error(404)
                return
            path = (Path(self.media_dir) / name).resolve()
            if path.parent != Path(self.media_dir).resolve() or not path.is_file():
                self.send_error(404)
                return
            try:
                self._send(200, path.read_bytes(), "image/jpeg")
            except OSError:
                self.send_error(404)
            return
        self.send_error(404)

    def do_POST(self):
        parts = urlparse(self.path).path.strip("/").split("/")
        if len(parts) != 4 or parts[0:2] != ["api", "incidents"] or parts[3] != "status":
            self.send_error(404)
            return
        try:
            incident_id = int(parts[2])
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 4096:
                self._json(400, {"error": "Invalid request body"})
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            status = payload.get("status")
            if status not in ("new", "reviewed", "resolved"):
                self._json(400, {"error": "Status must be new, reviewed, or resolved"})
                return
            with sqlite3.connect(self.database_path) as connection:
                cursor = connection.execute(
                    "UPDATE incidents SET status = ? WHERE id = ?", (status, incident_id)
                )
                if cursor.rowcount == 0:
                    self._json(404, {"error": "Incident not found"})
                    return
            self._json(200, {"id": incident_id, "status": status})
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"error": "Invalid incident ID or JSON"})
        except sqlite3.Error as exc:
            self._json(500, {"error": "Could not update incident", "detail": str(exc)})


def main():
    parser = argparse.ArgumentParser(description="Review detector incidents in a local browser dashboard")
    parser.add_argument("--database", default="reports/incidents.sqlite3")
    parser.add_argument("--media-dir", default="alerts")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind locally by default; use a trusted network only")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    DashboardHandler.database_path = str(Path(args.database).resolve())
    DashboardHandler.media_dir = str(Path(args.media_dir).resolve())
    if not Path(DashboardHandler.database_path).is_file():
        raise SystemExit("Incident database not found. Run the detector first.")
    server = HTTPServer((args.host, args.port), DashboardHandler)
    print("Dashboard: http://{}:{}/".format(args.host, args.port))
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
