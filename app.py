"""
YouTube Downloader Pro
- Fila de downloads com progresso real (SSE)
- Suporte a playlists
- Campo de pasta de destino
- Estimativa de tamanho
- MP3 ou MP4 com seleção de qualidade

Rode com: python app.py
Acesse:   http://localhost:5000
"""

from flask import Flask, request, jsonify, send_file, render_template_string, Response, stream_with_context
import subprocess, sys, os, threading, queue, uuid, json, time, re
from pathlib import Path

# ── deps ──────────────────────────────────────────────────────────────────────
def ensure_deps():
    try:
        import yt_dlp
    except ImportError:
        print("Instalando yt-dlp...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "yt-dlp"])

ensure_deps()

# ── app ───────────────────────────────────────────────────────────────────────
app = Flask(__name__)

DEFAULT_DIR = str(Path.home() / "Downloads" / "YT-Downloader")
os.makedirs(DEFAULT_DIR, exist_ok=True)

# job store: { job_id: { status, progress, speed, eta, title, filename, error, mode, items } }
JOBS = {}
JOBS_LOCK = threading.Lock()

# SSE subscribers: { job_id: [queue, queue, ...] }
SUBSCRIBERS = {}
SUBS_LOCK   = threading.Lock()

# ── SSE helpers ───────────────────────────────────────────────────────────────
def publish(job_id, data: dict):
    with SUBS_LOCK:
        subs = SUBSCRIBERS.get(job_id, [])
        dead = []
        for q in subs:
            try:
                q.put_nowait(data)
            except Exception:
                dead.append(q)
        for q in dead:
            subs.remove(q)

def update_job(job_id, **kwargs):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(kwargs)
    publish(job_id, kwargs)

# ── download worker ───────────────────────────────────────────────────────────
def sizeof_fmt(num_bytes):
    if num_bytes is None:
        return "?"
    for unit in ["B","KB","MB","GB"]:
        if abs(num_bytes) < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"

def run_download(job_id):
    import yt_dlp

    with JOBS_LOCK:
        job = dict(JOBS[job_id])

    out_dir = job["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    mode    = job["mode"]
    quality = job["quality"]
    url     = job["url"]

    # ── progress hook ────────────────────────────────────────────────────────
    current_item = {"index": 0, "total": 1, "title": ""}

    def progress_hook(d):
        if d["status"] == "downloading":
            raw_pct   = d.get("_percent_str", "0%").strip().replace("%","")
            raw_speed = d.get("_speed_str", "").strip()
            raw_eta   = d.get("_eta_str",   "").strip()
            total_b   = d.get("total_bytes") or d.get("total_bytes_estimate")
            down_b    = d.get("downloaded_bytes", 0)
            try:
                pct = float(raw_pct)
            except Exception:
                pct = 0.0
            update_job(job_id,
                status   = "downloading",
                progress = round(pct, 1),
                speed    = raw_speed,
                eta      = raw_eta,
                downloaded = sizeof_fmt(down_b),
                total_size = sizeof_fmt(total_b),
                item_index = current_item["index"],
                item_total = current_item["total"],
                item_title = current_item["title"],
            )
        elif d["status"] == "finished":
            update_job(job_id, status="processing",
                       progress=99, item_title=current_item["title"])

    # ── build yt-dlp options ─────────────────────────────────────────────────
    outtmpl = os.path.join(out_dir, "%(title).80s.%(ext)s")

    if mode == "audio":
        ydl_opts = {
            "format":        "bestaudio/best",
            "outtmpl":       outtmpl,
            "progress_hooks":[progress_hook],
            "quiet":         True,
            "no_warnings":   True,
            "postprocessors":[{
                "key":            "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality":"192",
            }],
        }
    else:
        if quality == "best":
            fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        else:
            h   = quality
            fmt = (f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]"
                   f"/best[height<={h}][ext=mp4]/best[height<={h}]")
        ydl_opts = {
            "format":              fmt,
            "outtmpl":             outtmpl,
            "progress_hooks":      [progress_hook],
            "quiet":               True,
            "no_warnings":         True,
            "merge_output_format": "mp4",
        }

    # ── run ──────────────────────────────────────────────────────────────────
    try:
        update_job(job_id, status="fetching_info")

        with yt_dlp.YoutubeDL({**ydl_opts, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)

        # playlist vs single
        entries = info.get("entries")
        if entries:
            entries = [e for e in entries if e]
            current_item["total"] = len(entries)
            titles = [e.get("title","?") for e in entries]
            update_job(job_id,
                is_playlist  = True,
                playlist_title = info.get("title","Playlist"),
                item_total   = len(entries),
                items        = [{"title": t, "status":"pending"} for t in titles],
            )
        else:
            entries = None
            current_item["total"] = 1
            update_job(job_id,
                is_playlist = False,
                item_total  = 1,
                item_title  = info.get("title",""),
            )

        # actual download
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            if entries:
                downloaded_files = []
                for i, entry in enumerate(entries):
                    current_item["index"] = i
                    current_item["title"] = entry.get("title", f"Item {i+1}")
                    with JOBS_LOCK:
                        items = JOBS[job_id].get("items", [])
                        if i < len(items):
                            items[i]["status"] = "downloading"
                    update_job(job_id, items=items if entries else [])
                    try:
                        ydl.download([entry.get("webpage_url") or entry.get("url")])
                        with JOBS_LOCK:
                            items = JOBS[job_id].get("items", [])
                            if i < len(items):
                                items[i]["status"] = "done"
                        update_job(job_id, items=items)
                        downloaded_files.append(entry.get("title",""))
                    except Exception as e:
                        with JOBS_LOCK:
                            items = JOBS[job_id].get("items", [])
                            if i < len(items):
                                items[i]["status"] = "error"
                        update_job(job_id, items=items)
                update_job(job_id,
                    status   = "done",
                    progress = 100,
                    out_dir  = out_dir,
                )
            else:
                # Capture the actual filename written by yt-dlp.
                # Adivinhar o nome falha porque yt-dlp aplica suas próprias regras
                # de sanitização. Usamos um hook 'finished' para pegar o caminho real,
                # e ainda corrigimos a extensão para o resultado pós-processamento.
                final_path = {"path": None}

                def capture_hook(d):
                    if d.get("status") == "finished":
                        # 'filename' é o caminho do arquivo antes do pós-processamento;
                        # para áudio, a extensão final será .mp3 (FFmpegExtractAudio).
                        final_path["path"] = d.get("filename")

                ydl.add_progress_hook(capture_hook)
                ydl.download([url])

                actual = final_path["path"]
                if actual:
                    base, _ = os.path.splitext(actual)
                    if mode == "audio":
                        actual = base + ".mp3"
                    elif not os.path.exists(actual):
                        # vídeo: pode ter virado .mp4 após merge
                        cand = base + ".mp4"
                        if os.path.exists(cand):
                            actual = cand

                # Fallback: se nada foi capturado ou o arquivo sumiu, varre o diretório
                # pelo arquivo mais recente com a extensão esperada.
                if not actual or not os.path.exists(actual):
                    ext = "mp3" if mode == "audio" else "mp4"
                    try:
                        candidates = [
                            os.path.join(out_dir, f)
                            for f in os.listdir(out_dir)
                            if f.lower().endswith("." + ext)
                        ]
                        if candidates:
                            actual = max(candidates, key=os.path.getmtime)
                    except Exception:
                        pass

                if actual and os.path.exists(actual):
                    update_job(job_id,
                        status   = "done",
                        progress = 100,
                        filename = os.path.basename(actual),
                        out_dir  = out_dir,
                    )
                else:
                    update_job(job_id,
                        status = "error",
                        error  = "Download terminou mas o arquivo não foi localizado no disco.",
                    )

    except Exception as e:
        update_job(job_id, status="error", error=str(e)[:300])

# ── routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/info", methods=["POST"])
def info_route():
    import yt_dlp
    data = request.get_json()
    url  = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL vazia"})
    try:
        opts = {"quiet": True, "no_warnings": True, "skip_download": True,
                "extract_flat": "in_playlist"}
        with yt_dlp.YoutubeDL(opts) as ydl:
            meta = ydl.extract_info(url, download=False)

        entries = meta.get("entries")
        if entries:
            entries = [e for e in entries if e]
            # estimate total size: sample first entry for avg filesize
            total_size = None
            sample_opts = {**opts, "extract_flat": False}
            try:
                with yt_dlp.YoutubeDL(sample_opts) as ydl2:
                    s = ydl2.extract_info(
                        entries[0].get("url") or entries[0].get("webpage_url",""),
                        download=False)
                formats = s.get("formats") or []
                best    = max((f for f in formats if f.get("filesize")),
                              key=lambda f: f.get("filesize",0), default=None)
                if best:
                    avg = best["filesize"]
                    total_size = avg * len(entries)
            except Exception:
                pass
            return jsonify({
                "is_playlist":     True,
                "title":           meta.get("title","Playlist"),
                "count":           len(entries),
                "estimated_size":  sizeof_fmt(total_size) if total_size else "?",
            })
        else:
            formats = meta.get("formats") or []
            best = max((f for f in formats if f.get("filesize")),
                       key=lambda f: f.get("filesize",0), default=None)
            size = sizeof_fmt(best["filesize"]) if best else "?"
            return jsonify({
                "is_playlist": False,
                "title":       meta.get("title","Sem título"),
                "duration":    meta.get("duration_string") or "",
                "thumbnail":   meta.get("thumbnail") or "",
                "estimated_size": size,
            })
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


@app.route("/enqueue", methods=["POST"])
def enqueue():
    data    = request.get_json()
    url     = (data.get("url") or "").strip()
    mode    = data.get("mode","audio")
    quality = data.get("quality","best")
    out_dir = (data.get("out_dir") or DEFAULT_DIR).strip() or DEFAULT_DIR

    if not url:
        return jsonify({"ok": False, "error": "URL vazia"})

    job_id = str(uuid.uuid4())
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id":          job_id,
            "url":         url,
            "mode":        mode,
            "quality":     quality,
            "out_dir":     out_dir,
            "status":      "queued",
            "progress":    0,
            "speed":       "",
            "eta":         "",
            "downloaded":  "",
            "total_size":  "",
            "filename":    "",
            "error":       "",
            "is_playlist": False,
            "playlist_title":"",
            "item_index":  0,
            "item_total":  1,
            "item_title":  "",
            "items":       [],
        }

    t = threading.Thread(target=run_download, args=(job_id,), daemon=True)
    t.start()
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/stream/<job_id>")
def stream(job_id):
    q = queue.Queue(maxsize=50)
    with SUBS_LOCK:
        SUBSCRIBERS.setdefault(job_id, []).append(q)

    # send current state immediately
    with JOBS_LOCK:
        current = dict(JOBS.get(job_id, {}))

    def generate():
        # send snapshot
        yield f"data: {json.dumps(current)}\n\n"
        while True:
            try:
                data = q.get(timeout=30)
                yield f"data: {json.dumps(data)}\n\n"
                if data.get("status") in ("done","error"):
                    break
            except queue.Empty:
                yield ": ping\n\n"

    return Response(stream_with_context(generate()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})


@app.route("/jobs")
def jobs():
    with JOBS_LOCK:
        return jsonify(list(JOBS.values()))


@app.route("/file/<job_id>")
def serve_file(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return "Job não encontrado", 404
    if not job.get("filename"):
        return "Nome do arquivo não foi registrado (download pode não ter terminado)", 404
    filepath = os.path.join(job["out_dir"], job["filename"])
    if not os.path.exists(filepath):
        # diagnóstico útil: lista o que de fato está na pasta
        try:
            files = os.listdir(job["out_dir"])
        except Exception:
            files = []
        return (
            f"Arquivo não encontrado.<br>Esperado: {filepath}<br>"
            f"Pasta contém: {files}"
        ), 404
    return send_file(filepath, as_attachment=True)


@app.route("/open_folder", methods=["POST"])
def open_folder():
    data = request.get_json()
    path = data.get("path", DEFAULT_DIR)
    if os.path.isdir(path):
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", path])
        else:
            subprocess.Popen(["xdg-open", path])
    return jsonify({"ok": True})


@app.route("/default_dir")
def default_dir():
    return jsonify({"path": DEFAULT_DIR})


# ── HTML ──────────────────────────────────────────────────────────────────────
HTML = r"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>YT Downloader Pro</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@700;800;900&display=swap" rel="stylesheet">
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:     #070707;
  --surf:   #0f0f0f;
  --surf2:  #141414;
  --border: #1c1c1c;
  --border2:#252525;
  --ga:     #c8f542;   /* green-audio  */
  --gv:     #42c8f5;   /* blue-video   */
  --err:    #ff5f3f;
  --warn:   #f5a742;
  --text:   #efefef;
  --muted:  #444;
  --r:      6px;
}

body {
  background: var(--bg);
  color: var(--text);
  font-family: 'Space Mono', monospace;
  min-height: 100vh;
  padding: 2rem 1rem 4rem;
  display: flex;
  flex-direction: column;
  align-items: center;
}

body::before {
  content:'';
  position:fixed; inset:0;
  background: radial-gradient(ellipse 80% 35% at 50% -5%, #162606 0%, transparent 60%);
  pointer-events:none; z-index:0;
  transition: background .5s;
}
body.vm::before { background: radial-gradient(ellipse 80% 35% at 50% -5%, #061626 0%, transparent 60%); }

.wrap { width:100%; max-width:660px; position:relative; z-index:1; }

/* ── HEADER ── */
.hdr { margin-bottom: 2.25rem; }
.eye { font-size:.6rem; letter-spacing:.35em; color:var(--ga); text-transform:uppercase; margin-bottom:.35rem; transition:color .4s; }
h1   { font-family:'Syne',sans-serif; font-size:clamp(2rem,7vw,3.6rem); font-weight:900; line-height:1; letter-spacing:-.03em; }
h1 .yt  { color:var(--err); }
h1 .sep { color:var(--muted); }
h1 .fmt { color:var(--ga); transition:color .4s; }
.sub { margin-top:.5rem; font-size:.7rem; color:var(--muted); }

/* ── CARD ── */
.card {
  background: var(--surf);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 1.6rem;
  position: relative;
  margin-bottom: 1rem;
}
.cbar {
  position:absolute; top:0; left:0; right:0; height:2px;
  border-radius: var(--r) var(--r) 0 0;
  background: linear-gradient(90deg,var(--err),var(--ga));
  transition: background .4s;
}

/* ── TOGGLE ── */
.tog {
  display:flex; gap:.4rem;
  background:var(--bg); border:1px solid var(--border); border-radius:var(--r);
  padding:3px; margin-bottom:1.4rem;
}
.tb {
  flex:1; border:none; background:transparent; color:var(--muted);
  font-family:'Syne',sans-serif; font-weight:700; font-size:.78rem;
  letter-spacing:.04em; text-transform:uppercase; padding:.6rem;
  cursor:pointer; border-radius:calc(var(--r) - 2px); transition:all .2s;
  display:flex; align-items:center; justify-content:center; gap:.35rem;
}
.tb.aa { background:var(--ga); color:#000; }
.tb.av { background:var(--gv); color:#000; }

/* ── FIELDS ── */
.field { margin-bottom:1.1rem; }
label  { display:block; font-size:.58rem; letter-spacing:.25em; text-transform:uppercase; color:var(--muted); margin-bottom:.4rem; }

.inp-row { display:flex; gap:.5rem; }
.inp-row input { flex:1; }

input[type="text"], select {
  width:100%;
  background:var(--bg); border:1px solid var(--border); color:var(--text);
  font-family:'Space Mono',monospace; font-size:.8rem;
  padding:.75rem .9rem; outline:none; border-radius:var(--r);
  transition:border-color .2s;
}
input[type="text"]:focus, select:focus { border-color:var(--ga); }
body.vm input[type="text"]:focus, body.vm select:focus { border-color:var(--gv); }
input::placeholder { color:var(--muted); }

select {
  cursor:pointer; appearance:none;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%23444' stroke-width='1.5' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");
  background-repeat:no-repeat; background-position:right .9rem center;
}
select option { background:#111; }

.folder-btn {
  flex-shrink:0; background:var(--surf2); border:1px solid var(--border2);
  color:var(--muted); font-family:'Space Mono',monospace; font-size:.75rem;
  padding:.75rem .9rem; border-radius:var(--r); cursor:pointer;
  transition:all .2s; white-space:nowrap;
}
.folder-btn:hover { border-color:var(--border2); color:var(--text); }

/* ── PREVIEW ── */
.preview-box {
  background:var(--bg); border:1px solid var(--border); border-radius:var(--r);
  padding:.75rem .9rem; font-size:.76rem; min-height:2.6rem;
  display:flex; align-items:flex-start; gap:.6rem;
  word-break:break-word; transition:all .3s;
}
.preview-box .dot {
  width:6px; height:6px; background:currentColor; border-radius:50%;
  flex-shrink:0; margin-top:.3rem; animation:blink 1.4s infinite;
}
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:.2} }

.preview-box.empty   { color:var(--muted); }
.preview-box.empty .dot { animation:none; }
.preview-box.loading { color:#666; }
.preview-box.ok      { color:var(--ga); }
.preview-box.pl      { color:var(--warn); }
.preview-box.er      { color:var(--err); }
.preview-box.er .dot { animation:none; }

.preview-meta { display:flex; gap:1rem; margin-top:.3rem; font-size:.65rem; color:var(--muted); flex-wrap:wrap; }
.preview-meta span { display:flex; align-items:center; gap:.25rem; }

.qs { display:none; }
.qs.v { display:block; }

/* ── ADD BUTTON ── */
.add-btn {
  width:100%; background:var(--ga); color:#000; border:none;
  font-family:'Syne',sans-serif; font-weight:800; font-size:.92rem;
  padding:.9rem; cursor:pointer; letter-spacing:.05em; text-transform:uppercase;
  transition:all .2s; border-radius:var(--r); margin-top:.2rem;
  display:flex; align-items:center; justify-content:center; gap:.5rem;
}
.add-btn.vb { background:var(--gv); }
.add-btn:hover:not(:disabled) { filter:brightness(1.12); transform:translateY(-1px); }
.add-btn:active:not(:disabled) { transform:translateY(0); }
.add-btn:disabled { opacity:.35; cursor:not-allowed; }

/* ── QUEUE ── */
.q-hdr {
  display:flex; align-items:center; justify-content:space-between;
  margin-bottom:.75rem;
}
.q-hdr h2 {
  font-family:'Syne',sans-serif; font-size:.9rem; font-weight:800;
  letter-spacing:.04em; text-transform:uppercase; color:var(--text);
}
.q-count {
  font-size:.65rem; background:var(--surf2); border:1px solid var(--border2);
  border-radius:99px; padding:.15rem .6rem; color:var(--muted);
}
.q-empty {
  text-align:center; padding:2rem; font-size:.72rem; color:var(--muted);
  border:1px dashed var(--border2); border-radius:var(--r);
}

/* ── JOB CARD ── */
.job {
  background:var(--surf); border:1px solid var(--border); border-radius:var(--r);
  margin-bottom:.6rem; overflow:hidden; transition:border-color .3s;
}
.job.done  { border-color:#1e3300; }
.job.error { border-color:#331500; }

.job-top {
  padding:.9rem 1rem;
  display:flex; align-items:flex-start; gap:.75rem;
}
.job-icon { font-size:1.3rem; flex-shrink:0; margin-top:.1rem; }
.job-info  { flex:1; min-width:0; }
.job-title {
  font-size:.78rem; color:var(--text);
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  margin-bottom:.25rem;
}
.job-meta  { font-size:.62rem; color:var(--muted); display:flex; gap:.75rem; flex-wrap:wrap; }
.job-status-badge {
  font-size:.58rem; letter-spacing:.15em; text-transform:uppercase;
  padding:.15rem .5rem; border-radius:99px; border:1px solid;
  flex-shrink:0;
}
.badge-queued      { color:#666;      border-color:#333; }
.badge-fetching_info { color:var(--warn); border-color:#3a2a00; }
.badge-downloading { color:var(--gv); border-color:#003344; }
.badge-processing  { color:var(--warn);border-color:#3a2a00; }
.badge-done        { color:var(--ga); border-color:#1e3300; }
.badge-error       { color:var(--err);border-color:#331500; }

/* progress bar inside job */
.job-bar-track { height:3px; background:var(--border); }
.job-bar-fill  {
  height:100%;
  background:linear-gradient(90deg,var(--err),var(--ga));
  width:0%; transition:width .35s;
}
.job-bar-fill.vb { background:linear-gradient(90deg,var(--err),var(--gv)); }
.job-bar-fill.ind { width:30%; animation:sl 1s ease-in-out infinite; }
@keyframes sl { 0%{transform:translateX(-100%)} 100%{transform:translateX(450%)} }

/* playlist sub-items */
.pl-items {
  padding:.5rem 1rem .75rem 2.8rem;
  border-top:1px solid var(--border); display:none;
}
.pl-items.open { display:block; }
.pl-item {
  font-size:.65rem; color:var(--muted); padding:.2rem 0;
  display:flex; align-items:center; gap:.4rem;
}
.pl-item .pi-dot {
  width:5px; height:5px; border-radius:50%; flex-shrink:0;
  background:var(--muted);
}
.pi-done  .pi-dot { background:var(--ga); }
.pi-dl    .pi-dot { background:var(--gv); animation:blink .8s infinite; }
.pi-error .pi-dot { background:var(--err); }

.pl-toggle {
  background:none; border:none; color:var(--muted); font-size:.6rem;
  cursor:pointer; text-transform:uppercase; letter-spacing:.1em;
  padding:0; margin-left:auto; flex-shrink:0;
}
.pl-toggle:hover { color:var(--text); }

/* action row */
.job-actions { display:flex; gap:.5rem; padding:.5rem 1rem .75rem; }
.ja-btn {
  font-size:.65rem; font-family:'Syne',sans-serif; font-weight:700;
  letter-spacing:.06em; text-transform:uppercase;
  padding:.35rem .75rem; border-radius:calc(var(--r) - 2px);
  cursor:pointer; border:1px solid var(--border2); background:var(--surf2);
  color:var(--muted); text-decoration:none; transition:all .2s;
}
.ja-btn:hover { color:var(--text); border-color:#444; }
.ja-btn.dl    { background:var(--ga); color:#000; border-color:var(--ga); }
.ja-btn.dl:hover { filter:brightness(1.1); }
.ja-btn.dlv   { background:var(--gv); color:#000; border-color:var(--gv); }
.ja-btn.dlv:hover { filter:brightness(1.1); }

.footer {
  margin-top:2rem; font-size:.58rem; color:var(--muted);
  text-align:center; letter-spacing:.12em;
}
</style>
</head>
<body>

<div class="wrap">

  <!-- HEADER -->
  <div class="hdr">
    <div class="eye" id="eye">⬡ Downloader Pro</div>
    <h1><span class="yt">YT</span><span class="sep"> → </span><span class="fmt" id="fmt">MP3</span></h1>
    <p class="sub">Fila · Playlist · Progresso real · Qualidade livre</p>
  </div>

  <!-- INPUT CARD -->
  <div class="card">
    <div class="cbar" id="cbar"></div>

    <!-- mode toggle -->
    <div class="tog">
      <button class="tb aa" id="tAudio" onclick="setMode('audio')">🎵 Áudio MP3</button>
      <button class="tb"    id="tVideo" onclick="setMode('video')">🎬 Vídeo MP4</button>
    </div>

    <!-- URL -->
    <div class="field">
      <label>Link do YouTube (vídeo ou playlist)</label>
      <input type="text" id="url" placeholder="https://www.youtube.com/watch?v=... ou /playlist?list=..." autocomplete="off" spellcheck="false">
    </div>

    <!-- PREVIEW -->
    <div class="field">
      <label>Informações do vídeo / playlist</label>
      <div class="preview-box empty" id="prev">
        <span class="dot"></span>
        <div>
          <div id="prevTitle">Aguardando link...</div>
          <div class="preview-meta" id="prevMeta"></div>
        </div>
      </div>
    </div>

    <!-- QUALITY (video only) -->
    <div class="field qs" id="qs">
      <label>Qualidade do vídeo</label>
      <select id="qual">
        <option value="best">Melhor disponível</option>
        <option value="1080">1080p</option>
        <option value="720">720p</option>
        <option value="480">480p</option>
        <option value="360">360p (menor tamanho)</option>
      </select>
    </div>

    <!-- DESTINATION -->
    <div class="field">
      <label>Pasta de destino</label>
      <div class="inp-row">
        <input type="text" id="destDir" placeholder="Carregando...">
        <button class="folder-btn" onclick="openFolder()">📂 Abrir</button>
      </div>
    </div>

    <!-- ADD TO QUEUE -->
    <button class="add-btn" id="addBtn" disabled onclick="addToQueue()">
      <span>＋</span> <span id="addTxt">Adicionar à fila</span>
    </button>
  </div>

  <!-- QUEUE CARD -->
  <div class="card">
    <div class="q-hdr">
      <h2>Fila de Downloads</h2>
      <span class="q-count" id="qCount">0 itens</span>
    </div>
    <div id="qList">
      <div class="q-empty">Nenhum download na fila ainda.</div>
    </div>
  </div>

  <div class="footer">yt-dlp + ffmpeg · processamento local · nenhum dado enviado</div>
</div>

<script>
// ── state ────────────────────────────────────────────────────────────────────
let mode = 'audio';
let fetchTimer = null;
let metaCache = null;
const jobs = {};   // job_id -> { el, data, es }

// ── init ─────────────────────────────────────────────────────────────────────
fetch('/default_dir').then(r=>r.json()).then(d=>{ document.getElementById('destDir').value = d.path; });
initCbar();

// ── mode toggle ───────────────────────────────────────────────────────────────
function setMode(m) {
  mode = m;
  const v = m === 'video';
  document.getElementById('tAudio').className = 'tb' + (v ? '' : ' aa');
  document.getElementById('tVideo').className = 'tb' + (v ? ' av' : '');
  document.getElementById('qs').className     = 'field qs' + (v ? ' v' : '');
  document.getElementById('fmt').textContent  = v ? 'MP4' : 'MP3';
  document.getElementById('fmt').style.color  = v ? 'var(--gv)' : 'var(--ga)';
  document.getElementById('eye').style.color  = v ? 'var(--gv)' : 'var(--ga)';
  document.getElementById('addBtn').className = 'add-btn' + (v ? ' vb' : '');
  document.body.classList.toggle('vm', v);
  initCbar();
}
function initCbar() {
  const v = mode === 'video';
  document.getElementById('cbar').style.background =
    v ? 'linear-gradient(90deg,var(--err),var(--gv))'
      : 'linear-gradient(90deg,var(--err),var(--ga))';
}

// ── url input / info ──────────────────────────────────────────────────────────
document.getElementById('url').addEventListener('input', () => {
  clearTimeout(fetchTimer);
  metaCache = null;
  document.getElementById('addBtn').disabled = true;
  const url = document.getElementById('url').value.trim();
  if (!url || !url.includes('youtu')) {
    setPrev('empty','Aguardando link...','');
    return;
  }
  setPrev('loading','Buscando informações...','');
  fetchTimer = setTimeout(()=>fetchInfo(url), 700);
});

function setPrev(cls, title, metaHTML) {
  const box = document.getElementById('prev');
  box.className = 'preview-box ' + cls;
  document.getElementById('prevTitle').textContent = title;
  document.getElementById('prevMeta').innerHTML = metaHTML;
}

async function fetchInfo(url) {
  try {
    const r = await fetch('/info', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({url})
    });
    const d = await r.json();
    if (d.error) { setPrev('er', d.error, ''); return; }

    metaCache = d;
    if (d.is_playlist) {
      setPrev('pl',
        `📋 ${d.title}`,
        `<span>🎬 ${d.count} vídeos</span><span>💾 ~${d.estimated_size}</span>`
      );
      document.getElementById('addTxt').textContent = `Adicionar playlist (${d.count} vídeos)`;
    } else {
      setPrev('ok', d.title,
        `${d.duration ? `<span>⏱ ${d.duration}</span>` : ''}
         <span>💾 ~${d.estimated_size}</span>`
      );
      document.getElementById('addTxt').textContent = 'Adicionar à fila';
    }
    document.getElementById('addBtn').disabled = false;
  } catch(e) {
    setPrev('er','Erro de conexão com o servidor','');
  }
}

// ── queue ─────────────────────────────────────────────────────────────────────
async function addToQueue() {
  const url     = document.getElementById('url').value.trim();
  const quality = document.getElementById('qual').value;
  const outDir  = document.getElementById('destDir').value.trim();
  if (!url) return;

  document.getElementById('addBtn').disabled = true;

  const r = await fetch('/enqueue', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ url, mode, quality, out_dir: outDir })
  });
  const d = await r.json();
  if (!d.ok) { alert('Erro: ' + d.error); document.getElementById('addBtn').disabled=false; return; }

  // clear input
  document.getElementById('url').value = '';
  setPrev('empty','Aguardando link...','');
  document.getElementById('addTxt').textContent = 'Adicionar à fila';
  metaCache = null;

  createJobCard(d.job_id, metaCache, mode);
  subscribeJob(d.job_id);
  updateQCount();
}

// ── job card DOM ──────────────────────────────────────────────────────────────
function createJobCard(jobId, meta, jobMode) {
  const list = document.getElementById('qList');
  // remove empty placeholder
  const emp = list.querySelector('.q-empty');
  if (emp) emp.remove();

  const isV = jobMode === 'video';
  const title = (meta && meta.title) ? meta.title : 'Aguardando...';
  const icon  = (meta && meta.is_playlist) ? '📋' : (isV ? '🎬' : '🎵');

  const el = document.createElement('div');
  el.className = 'job';
  el.id = 'job-' + jobId;
  el.innerHTML = `
    <div class="job-top">
      <span class="job-icon">${icon}</span>
      <div class="job-info">
        <div class="job-title" id="jt-${jobId}">${esc(title)}</div>
        <div class="job-meta" id="jm-${jobId}">
          <span>${isV ? 'MP4' : 'MP3'}</span>
        </div>
      </div>
      <span class="job-status-badge badge-queued" id="jb-${jobId}">Na fila</span>
    </div>
    <div class="job-bar-track">
      <div class="job-bar-fill ind ${isV?'vb':''}" id="jbf-${jobId}"></div>
    </div>
    <div class="pl-items" id="jpl-${jobId}"></div>
    <div class="job-actions" id="ja-${jobId}"></div>
  `;
  list.prepend(el);
  jobs[jobId] = { el, mode: jobMode };
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── SSE subscription ──────────────────────────────────────────────────────────
function subscribeJob(jobId) {
  const es = new EventSource('/stream/' + jobId);
  jobs[jobId].es = es;

  es.onmessage = (e) => {
    try { applyUpdate(jobId, JSON.parse(e.data)); }
    catch(err) { console.error(err); }
  };
  es.onerror = () => { es.close(); };
}

const BADGE_LABELS = {
  queued:       ['Na fila',     'badge-queued'],
  fetching_info:['Buscando',    'badge-fetching_info'],
  downloading:  ['Baixando',    'badge-downloading'],
  processing:   ['Processando', 'badge-processing'],
  done:         ['Concluído',   'badge-done'],
  error:        ['Erro',        'badge-error'],
};

function applyUpdate(jobId, d) {
  const jref = jobs[jobId];
  if (!jref) return;

  // merge
  if (!jref.data) jref.data = {};
  Object.assign(jref.data, d);
  const data = jref.data;

  // title
  if (d.item_title || d.playlist_title) {
    const t = data.is_playlist ? (data.playlist_title || '') : (data.item_title || '');
    if (t) document.getElementById('jt-'+jobId).textContent = t;
  }

  // badge
  const st = d.status || data.status;
  if (st && BADGE_LABELS[st]) {
    const [lbl, cls] = BADGE_LABELS[st];
    const badge = document.getElementById('jb-'+jobId);
    badge.textContent = lbl;
    badge.className   = 'job-status-badge ' + cls;
    const jobEl = document.getElementById('job-'+jobId);
    jobEl.className = 'job' + (st==='done'?' done':st==='error'?' error':'');
  }

  // progress bar
  const bar = document.getElementById('jbf-'+jobId);
  if (d.progress !== undefined) {
    bar.classList.remove('ind');
    bar.style.width = d.progress + '%';
  }
  if (st === 'fetching_info' || st === 'queued') {
    bar.classList.add('ind');
    bar.style.width = '';
  }

  // meta row
  const meta = document.getElementById('jm-'+jobId);
  if (data.status === 'downloading') {
    const parts = [];
    if (data.downloaded && data.total_size) parts.push(`${data.downloaded} / ${data.total_size}`);
    if (data.speed) parts.push(data.speed);
    if (data.eta)   parts.push(`ETA ${data.eta}`);
    if (data.is_playlist) parts.push(`Item ${(data.item_index||0)+1}/${data.item_total||1}`);
    meta.textContent = parts.join(' · ');
  }

  // playlist items
  if (d.items) {
    const plEl = document.getElementById('jpl-'+jobId);
    plEl.innerHTML = '';
    d.items.forEach((item, i) => {
      const cls = item.status==='done'?'pi-done':item.status==='downloading'?'pi-dl':item.status==='error'?'pi-error':'';
      const div = document.createElement('div');
      div.className = 'pl-item ' + cls;
      div.innerHTML = `<span class="pi-dot"></span><span>${i+1}. ${esc(item.title)}</span>`;
      plEl.appendChild(div);
    });
    if (d.items.length && !plEl.classList.contains('init')) {
      plEl.classList.add('open','init');
    }
  }

  // actions on done / error
  if ((d.status === 'done' || d.status === 'error') && jref.es) {
    jref.es.close();
    renderActions(jobId, data);
    updateQCount();
  }
}

function renderActions(jobId, data) {
  const ja = document.getElementById('ja-'+jobId);
  ja.innerHTML = '';

  if (data.status === 'done') {
    if (!data.is_playlist && data.filename) {
      const a = document.createElement('a');
      a.href = '/file/' + jobId;
      a.className = 'ja-btn ' + (jobs[jobId].mode==='video' ? 'dlv' : 'dl');
      a.textContent = '⬇ Baixar arquivo';
      a.target = '_blank';
      ja.appendChild(a);
    }
    const ob = document.createElement('button');
    ob.className = 'ja-btn';
    ob.textContent = '📂 Abrir pasta';
    ob.onclick = () => openFolderPath(data.out_dir);
    ja.appendChild(ob);
  }

  if (data.status === 'error') {
    const msg = document.createElement('span');
    msg.style.cssText = 'font-size:.65rem;color:var(--err);padding:.35rem 0;';
    msg.textContent = data.error || 'Erro desconhecido';
    ja.appendChild(msg);
  }
}

function updateQCount() {
  const total = Object.keys(jobs).length;
  document.getElementById('qCount').textContent = total + (total===1?' item':' itens');
}

// ── folder helpers ────────────────────────────────────────────────────────────
function openFolder() {
  const path = document.getElementById('destDir').value.trim();
  openFolderPath(path);
}

function openFolderPath(path) {
  fetch('/open_folder', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({path})
  });
}
</script>
</body>
</html>
"""

if __name__ == "__main__":
    print("\n🎬 YouTube Downloader Pro")
    print("──────────────────────────")
    print("Acesse: http://localhost:5000")
    print("Ctrl+C para encerrar\n")
    app.run(debug=False, port=5000, threaded=True)
