"""Export a local, standalone replay inspector without requiring a web service."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pyarrow.dataset as ds
import pyarrow as pa
import pyarrow.parquet as pq

from .io import exclusive_output, parsed_manifest, publish, read_json, sha256_file, staging_paths


def json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"protobuf_bytes": len(value)}
    if isinstance(value, dict):
        return {key: str(item) if key in ("steam_id", "buttonstate1", "buttonstate2", "buttonstate3", "button")
                and item is not None else json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


@exclusive_output(file_output=True)
def viewer(aligned: Path, out: Path) -> dict[str, Any]:
    manifest = read_json(aligned / "alignment_manifest.json")
    if manifest.get("status") != "complete" or manifest.get("alignment_version") != 1:
        raise ValueError("Viewer requires a completed supported alignment stage")
    for name in ("frame_alignment.parquet", "aligned_commands.parquet"):
        if manifest.get("files", {}).get(name) != sha256_file(aligned / name):
            raise ValueError(f"Aligned artifact hash disagrees with manifest: {name}")
    frames = pq.read_table(aligned / "frame_alignment.parquet").to_pylist()
    commands = pq.read_table(aligned / "aligned_commands.parquet").to_pylist()
    states = []
    parsed = manifest.get("parsed_directory")
    if parsed and (Path(parsed) / "player_state.parquet").is_file():
        source_manifest = parsed_manifest(Path(parsed), ["player_state.parquet"])
        if source_manifest["demo_id"] != manifest["demo_id"]:
            raise ValueError("Debug player-state source belongs to another demo")
        predicate = ((ds.field("demo_id") == manifest["demo_id"]) &
                     (ds.field("steam_id") == pa.scalar(int(manifest["steam_id"]), type=pa.uint64())) &
                     (ds.field("player_slot") == manifest["player_slot"]) &
                     (ds.field("round_id") == manifest["round_id"]) &
                     (ds.field("demo_tick") >= int(frames[0].get("action_window_demo_tick_start", frames[0]["source_demo_tick_start"]))) &
                     (ds.field("demo_tick") < frames[-1].get("action_window_demo_tick_end", frames[-1]["source_demo_tick_end"])))
        states = ds.dataset(Path(parsed) / "player_state.parquet", format="parquet").to_table(filter=predicate).to_pylist()
    payload = json_safe({"manifest": manifest, "frames": frames, "commands": commands, "states": states})
    encoded = base64.b64encode(json.dumps(payload, allow_nan=False).encode()).decode()
    html = _PAGE.replace("__PAYLOAD__", encoded)
    staged = staging_paths([out])
    staged[0].write_text(html, encoding="utf-8")
    publish(staged, [out])
    return {"output": str(out), "num_frames": len(frames), "command_count": len(commands)}


_PAGE = r'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CS2 frame and command inspector</title>
<style>
body{margin:0;background:#10181e;color:#e4edf2;font:15px system-ui}main{max-width:1400px;margin:30px auto;padding:0 24px}
h1{font-size:24px}p{color:#adbdc8}video{width:100%;max-height:65vh;background:#000}button,input{font:inherit}
button{padding:8px 15px;background:#243e4b;color:white;border:1px solid #486170;border-radius:5px;cursor:pointer}
.grid{display:grid;grid-template-columns:2fr 1fr;gap:22px}.controls{display:flex;gap:12px;align-items:center;margin:15px 0}
input[type=range]{width:100%}pre{white-space:pre-wrap;word-break:break-word;background:#182731;padding:16px;max-height:65vh;overflow:auto}
#stamp{font:15px ui-monospace;color:#8bddd6}.warning{border-left:3px solid #f5c56e;padding-left:12px}@media(max-width:900px){.grid{display:block}}
</style><main><h1>CS2 frame and command inspector</h1>
<p class="warning">Inspect firing, movement, camera changes and timing before marking this clip ready for training.</p>
<p id="identity"></p><p>Select the local video named in the manifest: <input id="file" type="file" accept="video/*"></p>
<div class="grid"><section><video id="video" controls preload="metadata"></video>
<div class="controls"><button id="previous">Previous frame</button><button id="next">Next frame</button><span id="stamp"></span></div>
<input id="slider" type="range" min="0" value="0"><p id="path"></p></section>
<section><pre id="labels"></pre></section></div></main>
<script>
const data=JSON.parse(new TextDecoder().decode(Uint8Array.from(atob('__PAYLOAD__'), c=>c.charCodeAt(0))));
const $=id=>document.getElementById(id), video=$('video'), frames=data.frames;
let current=0, objectUrl=null;
$('identity').textContent=`Demo ${data.manifest.demo_id} | Round ${data.manifest.round_id} | Player ${data.manifest.steam_id}`;
$('path').textContent=data.manifest.video_uri;$('slider').max=frames.length-1;
function show(index,seek=false){
 current=Math.max(0,Math.min(frames.length-1,index));const frame=frames[current];$('slider').value=current;
 $('stamp').textContent=`Frame ${current} · PTS ${frame.pts_seconds.toFixed(5)} s`;
 const commands=data.commands.slice(frame.clip_command_start,frame.clip_command_end);
 const observationTick=frame.action_window_demo_tick_start??frame.source_demo_tick_start;
 const state=data.states.findLast?data.states.findLast(s=>s.demo_tick<=observationTick):[...data.states].reverse().find(s=>s.demo_tick<=observationTick);
 $('labels').textContent=JSON.stringify({frame,player_state:state??null,commands},null,2);
 if(seek&&video.readyState){video.pause();video.currentTime=frame.pts_seconds+0.000001;}
}
function onTime(seconds){let lo=0,hi=frames.length;while(lo<hi){const mid=(lo+hi)>>1;if(frames[mid].pts_seconds<=seconds)lo=mid+1;else hi=mid;}show(Math.max(0,lo-1));}
$('slider').oninput=()=>show(Number($('slider').value),true);$('previous').onclick=()=>show(current-1,true);$('next').onclick=()=>show(current+1,true);
$('file').onchange=()=>{if(objectUrl)URL.revokeObjectURL(objectUrl);if($('file').files[0]){objectUrl=URL.createObjectURL($('file').files[0]);video.src=objectUrl;}};
if(video.requestVideoFrameCallback){const callback=(now,metadata)=>{onTime(metadata.mediaTime);video.requestVideoFrameCallback(callback);};video.requestVideoFrameCallback(callback);}
else video.ontimeupdate=()=>onTime(video.currentTime);
document.onkeydown=e=>{if(e.target.tagName==='INPUT')return;if(e.key==='ArrowLeft'){e.preventDefault();show(current-1,true);}if(e.key==='ArrowRight'){e.preventDefault();show(current+1,true);}};show(0);
</script></html>'''
