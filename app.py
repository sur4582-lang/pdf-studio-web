"""
PDF Studio Pro - web version.

Wraps the original desktop engine (engine.py) in a Flask app:
upload -> process -> download, with size limits, rate limits and auto-deletion.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import tempfile
import threading
import time
import zipfile
from collections import defaultdict, deque

from flask import (Flask, abort, jsonify, render_template, request,
                   send_file, send_from_directory, url_for, Response)
from werkzeug.middleware.proxy_fix import ProxyFix

import engine
from engine import TOOLS, ToolError, run_tool, friendly_error, human
from seo import SEO, GROUP_LABELS

# ------------------------------------------------------------------ config
def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


CFG = dict(
    SITE_NAME=os.environ.get("SITE_NAME", "PDF Studio Pro"),
    SITE_URL=os.environ.get("SITE_URL", "").rstrip("/"),
    AUTHOR=os.environ.get("AUTHOR", "Brajesh Yadav"),
    CONTACT_EMAIL=os.environ.get("CONTACT_EMAIL", "contact@example.com"),
    ADSENSE_CLIENT=os.environ.get("ADSENSE_CLIENT", ""),        # e.g. ca-pub-1234567890123456
    ANALYTICS_ID=os.environ.get("ANALYTICS_ID", ""),            # e.g. G-XXXXXXXXXX
    SOURCE_URL=os.environ.get("SOURCE_URL", ""),                # AGPL: link to your source code
    GSC_VERIFY=os.environ.get("GSC_VERIFY", ""),                # Google Search Console meta token
)
MAX_UPLOAD_MB = env_int("MAX_UPLOAD_MB", 50)          # total per request
MAX_FILES = env_int("MAX_FILES", 20)
MAX_PAGES = env_int("MAX_PAGES", 400)                 # per PDF
MAX_PARALLEL_JOBS = env_int("MAX_PARALLEL_JOBS", 2)
RATE_LIMIT = env_int("RATE_LIMIT", 30)                # jobs per IP per window
RATE_WINDOW = env_int("RATE_WINDOW_SEC", 600)
RESULT_TTL = env_int("RESULT_TTL_SEC", 600)           # results are deleted after this
JOB_TIMEOUT = env_int("JOB_TIMEOUT_SEC", 240)
BASE_DIR = os.environ.get("WORK_DIR") or os.path.join(tempfile.gettempdir(), "pdfstudio_jobs")

# lower some limits that are fine on a desktop but dangerous on a shared server
WEB_CAPS = {("pdf2img", "dpi"): 300, ("compress", "dpi"): 400}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
os.makedirs(BASE_DIR, exist_ok=True)

TOOL_MAP = {t.key: t for t in TOOLS}
_slots = threading.BoundedSemaphore(MAX_PARALLEL_JOBS)
_hits: dict[str, deque] = defaultdict(deque)
_hits_lock = threading.Lock()
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


# ----------------------------------------------------------------- helpers
def tool_by_group():
    groups = []
    for g in ["Optimize", "Organize", "Edit", "Convert", "Security"]:
        items = [t for t in TOOLS if t.group == g]
        if items:
            groups.append((g, GROUP_LABELS.get(g, g), items))
    return groups


def public_fields(tool):
    """Field definitions that are safe/serialisable for the template."""
    out = []
    for f in tool.fields:
        if f["key"] == "turbo":          # desktop-only option
            continue
        d = {k: v for k, v in f.items() if not callable(v)}
        cap = WEB_CAPS.get((tool.key, f["key"]))
        if cap and "max" in d:
            d["max"] = min(d["max"], cap)
        out.append(d)
    return out


def default_opts(tool):
    return {f["key"]: f.get("default") for f in tool.fields}


def collect_opts(tool, form) -> dict:
    opts = {}
    for f in tool.fields:
        key, typ = f["key"], f["type"]
        raw = form.get(key)
        if typ == "bool":
            opts[key] = str(raw).lower() in ("1", "true", "on", "yes")
            continue
        if raw is None:
            opts[key] = f.get("default")
            continue
        if typ in ("slider", "int", "float"):
            try:
                v = float(str(raw).strip().replace(",", "."))
            except ValueError:
                raise ToolError(f["label"], f'"{f["label"]}" must be a number')
            hi = f.get("max", v)
            cap = WEB_CAPS.get((tool.key, key))
            if cap:
                hi = min(hi, cap)
            v = max(f.get("min", v), min(hi, v))
            opts[key] = float(v) if typ == "float" else int(round(v))
        elif typ == "choice":
            raw = str(raw)
            opts[key] = raw if raw in f["choices"] else f.get("default")
        elif typ == "color":
            raw = str(raw)
            opts[key] = raw if re.fullmatch(r"#[0-9a-fA-F]{6}", raw) else f.get("default")
        else:
            opts[key] = str(raw)[:500]
    opts["turbo"] = False            # never spawn process pools per web request
    return opts


def clean_filename(name: str, fallback: str) -> str:
    name = os.path.basename((name or "").replace("\\", "/"))
    name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]", "_", name).strip(" .")
    stem, ext = os.path.splitext(name)
    stem = stem[:80].strip(" .") or fallback
    return stem + ext.lower()


def client_ip() -> str:
    return request.remote_addr or "?"


def rate_limited() -> bool:
    now = time.time()
    with _hits_lock:
        q = _hits[client_ip()]
        while q and now - q[0] > RATE_WINDOW:
            q.popleft()
        if len(q) >= RATE_LIMIT:
            return True
        q.append(now)
        return False


def job_dir(job_id: str) -> str:
    if not _ID_RE.match(job_id):
        abort(404)
    return os.path.join(BASE_DIR, job_id)


def cleanup_loop():
    while True:
        try:
            now = time.time()
            for name in os.listdir(BASE_DIR):
                p = os.path.join(BASE_DIR, name)
                try:
                    if now - os.path.getmtime(p) > RESULT_TTL + 60:
                        shutil.rmtree(p, ignore_errors=True)
                except OSError:
                    pass
        except Exception:
            pass
        time.sleep(60)


def start_cleanup_thread():
    threading.Thread(target=cleanup_loop, daemon=True).start()


start_cleanup_thread()


def sniff_ok(path: str, kind: str) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(1024)
    except OSError:
        return False
    if kind == "pdf":
        return b"%PDF-" in head
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.verify()
        return True
    except Exception:
        return False


# ------------------------------------------------------------------- pages
@app.context_processor
def inject_globals():
    return dict(cfg=CFG, groups=tool_by_group(), max_mb=MAX_UPLOAD_MB, max_files=MAX_FILES,
                seo_slug=lambda k: SEO[k]["slug"], now_year=time.strftime("%Y"), ttl_min=max(1, RESULT_TTL // 60),
                canonical=(CFG["SITE_URL"] + request.path) if CFG["SITE_URL"] else request.url.split("?")[0])


@app.route("/")
def index():
    return render_template("index.html", tools=TOOLS, seo=SEO)


PAGES = {
    "about": "About", "privacy": "Privacy Policy",
    "terms": "Terms of Use", "contact": "Contact",
}
SLUG_TO_TOOL = {SEO[k]["slug"]: k for k in SEO}


@app.route("/<slug>")
def page(slug):
    if slug in SLUG_TO_TOOL:
        tool = TOOL_MAP[SLUG_TO_TOOL[slug]]
        info = SEO[tool.key]
        ld_faq = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": f["q"], "acceptedAnswer": {"@type": "Answer", "text": f["a"]}}
            for f in info["faq"]]}
        ld_app = {"@context": "https://schema.org", "@type": "WebApplication", "name": tool.name,
                  "applicationCategory": "UtilitiesApplication", "operatingSystem": "Any",
                  "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"}}
        return render_template("tool.html", tool=tool, fields=public_fields(tool),
                               ld_faq=ld_faq, ld_app=ld_app,
                               info=info, others=[t for t in TOOLS if t.key != tool.key],
                               accept=(".pdf,application/pdf" if tool.kind == "pdf"
                                       else ",".join(engine.IMG_EXT)))
    if slug in PAGES:
        return render_template(f"pages/{slug}.html", title=PAGES[slug])
    abort(404)


@app.route("/robots.txt")
def robots():
    base = CFG["SITE_URL"] or request.url_root.rstrip("/")
    return Response(f"User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: {base}/sitemap.xml\n",
                    mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap():
    base = CFG["SITE_URL"] or request.url_root.rstrip("/")
    urls = [base + "/"] + [f"{base}/{SEO[t.key]['slug']}" for t in TOOLS] + [f"{base}/{s}" for s in PAGES]
    body = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    return Response('<?xml version="1.0" encoding="UTF-8"?>'
                    f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>',
                    mimetype="application/xml")


@app.route("/ads.txt")
def ads_txt():
    cid = CFG["ADSENSE_CLIENT"].replace("ca-", "")
    text = f"google.com, {cid}, DIRECT, f08c47fec0942fa0\n" if cid else ""
    return Response(text, mimetype="text/plain")


@app.route("/healthz")
def healthz():
    return jsonify(ok=True, engine=engine.engine_ready() is None)


# --------------------------------------------------------------------- API
@app.post("/api/visible/<key>")
def api_visible(key):
    """Evaluate the desktop app's show/help lambdas for the current option values."""
    tool = TOOL_MAP.get(key)
    if not tool:
        abort(404)
    try:
        o = collect_opts(tool, request.form)
    except ToolError:
        o = default_opts(tool)
    vis, helps = {}, {}
    for f in tool.fields:
        show = f.get("show")
        try:
            vis[f["key"]] = True if show is None else bool(show(o))
        except Exception:
            vis[f["key"]] = True
        if f.get("help"):
            try:
                helps[f["key"]] = f["help"](o)
            except Exception:
                pass
    return jsonify(visible=vis, help=helps)


@app.post("/api/run/<key>")
def api_run(key):
    tool = TOOL_MAP.get(key)
    if not tool:
        abort(404)
    if rate_limited():
        return jsonify(error="Too many requests. Please wait a few minutes and try again."), 429

    uploads = [f for f in request.files.getlist("files") if f and f.filename]
    if not uploads:
        return jsonify(error="Please add at least one file."), 400
    if len(uploads) > MAX_FILES:
        return jsonify(error=f"You can upload up to {MAX_FILES} files at a time."), 400

    try:
        opts = collect_opts(tool, request.form)
    except ToolError as e:
        return jsonify(error=e.full), 400

    ok_ext = (".pdf",) if tool.kind == "pdf" else engine.IMG_EXT
    job_id = engine.secrets.token_urlsafe(18)
    root = job_dir(job_id)
    in_dir, out_dir = os.path.join(root, "in"), os.path.join(root, "out")
    os.makedirs(in_dir)
    os.makedirs(out_dir)

    def fail(msg, code=400):
        shutil.rmtree(root, ignore_errors=True)
        return jsonify(error=msg), code

    # 1) save + validate uploads (in the order the user arranged them)
    files, used = [], set()
    for i, up in enumerate(uploads, 1):
        name = clean_filename(up.filename, f"file{i}")
        if not name.lower().endswith(ok_ext):
            return fail(f'"{up.filename}" is not a supported file type.')
        base, ext = os.path.splitext(name)
        n = 1
        while name.lower() in used:
            n += 1
            name = f"{base} ({n}){ext}"
        used.add(name.lower())
        path = os.path.join(in_dir, name)
        up.save(path)
        if os.path.getsize(path) == 0 or not sniff_ok(path, tool.kind):
            return fail(f'"{up.filename}" looks damaged or is not a valid file.')
        files.append(path)

    # 2) page-count guard
    if tool.kind == "pdf":
        for p in files:
            try:
                d = engine.fitz.open(p)
                pc, locked = d.page_count, d.needs_pass
                d.close()
            except Exception:
                return fail(f'"{os.path.basename(p)}" could not be opened as a PDF.')
            if pc > MAX_PAGES and not locked:
                return fail(f'"{os.path.basename(p)}" has {pc} pages. The limit here is {MAX_PAGES}.')

    try:
        tool.validate(opts, files)
    except ToolError as e:
        return fail(e.full)

    # 3) run (bounded concurrency)
    if not _slots.acquire(timeout=20):
        return fail("The server is busy right now. Please try again in a minute.", 503)

    events, cancel = [], threading.Event()
    result = {}

    def emit(ev):
        if ev[0] == "row":
            events.append(("row", ev[1], ev[2], ev[4]))
        elif ev[0] == "status":
            events.append(("status", ev[1]))
        elif ev[0] == "finish":
            result["finish"] = ev

    def work():
        try:
            run_tool(tool, files, opts, out_dir, cancel, emit, 1)
        except Exception as e:                       # pragma: no cover
            result["crash"] = str(e)

    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(JOB_TIMEOUT)
    try:
        if th.is_alive():
            cancel.set()
            th.join(10)
            return fail("This file took too long to process. Try a smaller file.", 504)
    finally:
        _slots.release()

    if "crash" in result or "finish" not in result:
        return fail("Something went wrong while processing. Please try another file.", 500)

    _, ok, failed, cancelled, summary, _outs = result["finish"]

    # 4) collect outputs (walk the folder: some tools create sub-folders)
    outs = []
    for dp, _dn, fn in os.walk(out_dir):
        for f in sorted(fn):
            full = os.path.join(dp, f)
            outs.append((os.path.relpath(full, out_dir), os.path.getsize(full)))
    outs.sort()
    if not outs:
        msgs = [e[1] for e in events if e[0] == "status"]
        return fail(msgs[0] if msgs else "Could not process the file(s).", 422)

    messages = [e[1] for e in events if e[0] == "status"]
    rows = [e[2] for e in events if e[0] == "row"]
    if len(outs) == 1:
        dl_name = outs[0][0]
    else:
        stem = os.path.splitext(os.path.basename(files[0]))[0] if tool.per_file else tool.key
        dl_name = f"{stem}_{tool.key}.zip"
    meta = dict(name=dl_name, files=[dict(name=o[0], size=human(o[1])) for o in outs],
                summary=summary, rows=rows, messages=messages, ok=ok, failed=failed)
    with open(os.path.join(root, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh)
    shutil.rmtree(in_dir, ignore_errors=True)       # originals are removed immediately

    return jsonify(id=job_id, ttl=RESULT_TTL, **meta, download=url_for("download", job_id=job_id))


@app.get("/api/download/<job_id>")
def download(job_id):
    root = job_dir(job_id)
    meta_path = os.path.join(root, "meta.json")
    if not os.path.exists(meta_path):
        abort(404)
    with open(meta_path, encoding="utf-8") as fh:
        meta = json.load(fh)
    out_dir = os.path.join(root, "out")
    names = [f["name"] for f in meta["files"]]
    if len(names) == 1:
        return send_file(os.path.join(out_dir, names[0]), as_attachment=True,
                         download_name=os.path.basename(names[0]))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n in names:
            z.write(os.path.join(out_dir, n), n)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=meta["name"], mimetype="application/zip")


@app.post("/api/delete/<job_id>")
def delete_job(job_id):
    shutil.rmtree(job_dir(job_id), ignore_errors=True)
    return jsonify(ok=True)


@app.errorhandler(413)
def too_big(_e):
    return jsonify(error=f"Files are too large. The limit is {MAX_UPLOAD_MB} MB per upload."), 413


@app.errorhandler(404)
def not_found(_e):
    if request.path.startswith("/api/"):
        return jsonify(error="Not found"), 404
    return render_template("pages/404.html", title="Page not found"), 404


@app.after_request
def headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    if request.path.startswith("/static/"):
        resp.headers.setdefault("Cache-Control", "public, max-age=604800")
    return resp


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)
