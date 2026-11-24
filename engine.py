#!/usr/bin/env python3
"""
PDF Studio Pro
==============
All-in-one PDF toolkit with a fast, high-quality compression engine.
PDF Studio by Mr. Brajesh Yadav

  Optimize : Compress (up to 99% smaller, target-size mode, Turbo multi-core)
  Organize : Merge, Split, Extract / Delete / Reorder pages, Rotate
  Edit     : Page Numbers, Watermark
  Convert  : Images to PDF, PDF to Images, PDF to Text
  Security : Protect (AES-256), Unlock, Repair

Install:   pip install -U pymupdf pillow      (done automatically on first run)
Run:       python pdf_studio_pro.py
"""

from __future__ import annotations

import colorsys
import io
import math
import multiprocessing
import os
import queue
import secrets
import shutil
import sys
import threading
import time
import zlib
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool

fitz = None
Image = None


def load_libs() -> bool:
    """(Re)try importing the required libraries. True when both are available."""
    global fitz, Image
    import importlib
    importlib.invalidate_caches()
    try:
        import site
        user_site = site.getusersitepackages()
        if os.path.isdir(user_site) and user_site not in sys.path:
            sys.path.append(user_site)
    except Exception:
        pass
    if fitz is None:
        for name in ("pymupdf", "fitz"):
            try:
                mod = importlib.import_module(name)
                if hasattr(mod, "open"):          # ignore the unrelated PyPI "fitz"
                    fitz = mod
                    break
            except Exception:
                continue
    if Image is None:
        try:
            from PIL import Image as _Image
            Image = _Image
        except Exception:
            pass
    return fitz is not None and Image is not None


load_libs()

APP_NAME = "PDF Studio Pro"

# name: (dpi_threshold, dpi_target, jpeg_quality, description)
PRESETS = {
    "Maximum Quality": (400, 300, 90, "Print-grade. Smallest visual change, modest saving."),
    "Balanced (Recommended)": (250, 200, 80, "Crisp on screen and print. Best size / quality mix."),
    "Strong Compression": (180, 150, 70, "Much smaller files, still clear and readable."),
    "Extreme (Smallest File)": (160, 120, 60, "Smallest possible size. Fine for reading, email and web."),
    "Custom": (None, None, None, "Set your own DPI and quality with the sliders below."),
}

BG, CARD, INK = "#F3F5F9", "#FFFFFF", "#0F1B2D"
MUTED, ACCENT, ACCENT_DK = "#64708A", "#1F4FD8", "#173DAE"
OK, ERR, LINE = "#15855F", "#C0392B", "#DDE3EE"


# ================================================================== engine
class PasswordError(Exception):
    pass


def human(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} B" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


def engine_ready() -> str | None:
    """Return None if everything needed is available, else a readable problem."""
    if not load_libs():
        return "Required libraries are missing."
    if not (hasattr(fitz.Page, "get_image_info") and hasattr(fitz.Document, "update_stream")):
        return "Your PyMuPDF version is too old (it will be upgraded automatically)."
    return None


def install_requirements(log=lambda msg: None) -> tuple[bool, str]:
    """pip-install the needed libraries into the running Python. Returns (ok, details)."""
    import subprocess
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    base = [sys.executable, "-m", "pip", "install", "--upgrade",
            "--disable-pip-version-check", "pymupdf", "pillow"]
    attempts = [base, base + ["--user"]]
    last = ""
    try:                                              # make sure pip exists
        subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"],
                       capture_output=True, text=True, creationflags=flags, timeout=300)
    except Exception:
        pass
    for cmd in attempts:
        log("Downloading and installing components...")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               creationflags=flags, timeout=900)
        except Exception as e:
            last = last or str(e)
            continue
        if r.returncode == 0 and load_libs():
            return True, ""
        last = last or (r.stderr or r.stdout or "").strip()[-600:]
    return False, last


def unique_path(path: str, taken: set[str]) -> str:
    """Return a path that does not exist on disk and is not already planned."""
    base, ext = os.path.splitext(path)
    cand, i = path, 1
    while os.path.exists(cand) or os.path.normcase(cand) in taken:
        i += 1
        cand = f"{base} ({i}){ext}"
    taken.add(os.path.normcase(cand))
    return cand


class Cancelled(Exception):
    pass


def _is_photo(img) -> bool:
    """True for continuous-tone images (photos, scans); False for flat graphics."""
    w, h = img.size
    if w > 640 or h > 640:                                  # sample the centre only (fast)
        left, top = max(0, (w - 640) // 2), max(0, (h - 640) // 2)
        img = img.crop((left, top, left + min(w, 640), top + min(h, 640)))
    colors = img.getcolors(maxcolors=8192)
    if colors is None:                                      # > 8192 distinct colours
        return True
    total = img.size[0] * img.size[1]
    top4 = sum(c for c, _ in sorted(colors, reverse=True)[:4])
    return top4 / total < 0.80                              # flat graphics: few dominant colours


_SAFE_CS = {"DeviceRGB", "DeviceGray", "ICCBased", "CalRGB", "CalGray"}
_OK_FILTERS = {"DCTDecode", "FlateDecode", "LZWDecode", ""}


def _noop():
    return 0


def _image_worker(job):
    """
    Heavy pixel work for ONE image. Runs in a worker process (Turbo mode) or inline.
    Pure Pillow - never touches the PDF. Returns (xref, None | (data, filter, w, h)).
    """
    (xref, kind, payload, w, h, n, dpi, threshold, target, quality, old_len) = job
    try:
        lanczos = getattr(Image, "Resampling", Image).LANCZOS
        if kind == "jpeg":
            pil = Image.open(io.BytesIO(payload))
            if pil.mode not in ("L", "RGB"):
                return xref, None
            w, h = pil.size
        else:
            pil = Image.frombytes("L" if n == 1 else "RGB", (w, h), payload)

        resized = False
        nw, nh = w, h
        if dpi > threshold and target < dpi:
            k = target / dpi
            nw, nh = max(1, round(w * k)), max(1, round(h * k))
            resized = nw < w and nh < h
        if resized:
            if kind == "jpeg":
                pil.draft(pil.mode, (nw, nh))               # decode at reduced size = much faster
            if pil.size != (nw, nh):
                pil = pil.resize((nw, nh), lanczos, reducing_gap=3.0)
        else:
            nw, nh = w, h
            if kind == "jpeg":
                pil.load()

        photo = True if kind == "jpeg" else _is_photo(pil)
        if not photo and not resized:
            return xref, None                               # lossless graphic: leave as is

        if photo:
            buf = io.BytesIO()
            pil.save(buf, "JPEG", quality=quality,
                     subsampling=0 if quality >= 90 else 2)
            data, flt = buf.getvalue(), "/DCTDecode"
        else:
            data, flt = zlib.compress(pil.tobytes(), 6), "/FlateDecode"

        limit = old_len if resized else old_len * 0.9
        if 0 < len(data) < limit:
            return xref, (data, flt, pil.size[0], pil.size[1])
        return xref, None
    except Exception:
        return xref, None


def _stream_len(doc, xref: int) -> int:
    kind, val = doc.xref_get_key(xref, "Length")
    if kind == "int":
        try:
            return int(val)
        except ValueError:
            pass
    return len(doc.xref_stream_raw(xref))


def optimise_images(doc, threshold, target, quality, rep, check, pool=None):
    """
    Exact-resolution image optimiser (see module docs).
    rep(stage, fraction_of_file) reports live progress; check() raises Cancelled.
    """
    # ---- 1) scan pages: which images exist and at what effective DPI -------
    seen: dict[int, dict] = {}
    n_pages = max(1, doc.page_count)
    for pno in range(doc.page_count):
        check()
        page = doc[pno]
        imgs = page.get_images(full=True)
        for img in imgs:
            seen.setdefault(img[0], dict(smask=img[1], w=img[2], h=img[3], bpc=img[4],
                                         cs=img[5], flt=img[8], dpi=0.0))
        if not imgs:
            rep("Analysing pages", 0.02 + 0.10 * (pno + 1) / n_pages)
            continue
        # Fast path: identify placed images by pixel size. Only if two images on this
        # page share identical dimensions do we pay for the slow exact (hashing) lookup.
        by_dims: dict[tuple, list] = {}
        for img in imgs:
            by_dims.setdefault((img[2], img[3]), []).append(img[0])
        ambiguous = any(len(v) > 1 for v in by_dims.values())
        try:
            infos = page.get_image_info(xrefs=ambiguous)
        except Exception:
            infos = []
        for it in infos:
            xref = it.get("xref", 0) if ambiguous else 0
            if not xref:
                cands = by_dims.get((it.get("width"), it.get("height")), [])
                if len(cands) != 1:
                    continue
                xref = cands[0]
            x0, y0, x1, y1 = it["bbox"]
            area_in = abs(x1 - x0) * abs(y1 - y0) / 5184.0
            if xref in seen and area_in > 0:
                dpi = math.sqrt(it["width"] * it["height"] / area_in)
                seen[xref]["dpi"] = max(seen[xref]["dpi"], dpi)
        rep("Analysing pages", 0.02 + 0.10 * (pno + 1) / n_pages)

    # ---- 2) choose candidates (cheap checks only, no decoding) -------------
    specs = []
    for xref, m in seen.items():
        try:
            if m["smask"] or m["bpc"] != 8 or m["cs"] not in _SAFE_CS:
                continue
            if m["flt"] not in _OK_FILTERS or m["w"] * m["h"] < 10_000:
                continue
            if doc.xref_get_key(xref, "Mask")[0] != "null":
                continue
            if doc.xref_get_key(xref, "Decode")[0] != "null":
                continue
            w, h, dpi = m["w"], m["h"], m["dpi"]
            will_resize = dpi > threshold and target < dpi
            old_len = _stream_len(doc, xref)
            ncomp = 1 if m["cs"] in ("DeviceGray", "CalGray") else 3
            if not will_resize:
                if m["flt"] == "DCTDecode":                  # already a lean JPEG
                    if old_len / (w * h) < 0.004 * quality * (0.6 if ncomp == 1 else 1.0):
                        continue
                elif old_len / (w * h * ncomp) < 0.25:       # very flat -> graphic
                    continue
            specs.append(dict(xref=xref, w=w, h=h, dpi=dpi, flt=m["flt"], old_len=old_len))
        except Exception:
            continue
    total_w = float(sum(sp["w"] * sp["h"] for sp in specs)) or 1.0

    # ---- 3) run the pixel work (parallel when a pool is given) -------------
    def make_job(sp):
        xref = sp["xref"]
        if sp["flt"] == "DCTDecode":
            return (xref, "jpeg", doc.xref_stream_raw(xref), sp["w"], sp["h"], 0,
                    sp["dpi"], threshold, target, quality, sp["old_len"])
        pix = fitz.Pixmap(doc, xref)                         # raw pixels: no PNG detour
        if (pix.alpha or pix.colorspace is None or pix.n not in (1, 3)
                or pix.stride != pix.width * pix.n):
            return None
        return (xref, "raw", pix.samples, pix.width, pix.height, pix.n,
                sp["dpi"], threshold, target, quality, sp["old_len"])

    def apply(res):
        xref, out = res
        if not out:
            return
        data, flt, nw, nh = out
        doc.update_stream(xref, data, compress=False)        # replace in place, no duplicates
        doc.xref_set_key(xref, "Filter", flt)
        if doc.xref_get_key(xref, "DecodeParms")[0] != "null":
            doc.xref_set_key(xref, "DecodeParms", "null")
        doc.xref_set_key(xref, "Width", str(nw))
        doc.xref_set_key(xref, "Height", str(nh))
        doc.xref_set_key(xref, "BitsPerComponent", "8")

    workers = getattr(pool, "_max_workers", 1) if pool else 1
    window = max(2, workers * 2) if pool else 1
    pending: dict = {}
    done_w = 0.0
    it = iter(specs)
    exhausted = False

    def advance(weight):
        nonlocal done_w
        done_w += weight
        rep("Optimising images", 0.12 + 0.78 * min(1.0, done_w / total_w))

    rep("Optimising images", 0.12)
    while True:
        check()
        while not exhausted and len(pending) < window:
            sp = next(it, None)
            if sp is None:
                exhausted = True
                break
            weight = sp["w"] * sp["h"]
            try:
                job = make_job(sp)
            except Exception:
                job = None
            if job is None:
                advance(weight)
                continue
            if pool is not None:
                try:
                    pending[pool.submit(_image_worker, job)] = weight
                    continue
                except Exception:                            # pool died: carry on in-process
                    pool = None
                    window = 1
            apply(_image_worker(job))
            advance(weight)
        if not pending:
            if exhausted:
                break
            continue
        done, _ = wait(list(pending), timeout=0.2, return_when=FIRST_COMPLETED)
        for fut in done:
            weight = pending.pop(fut)
            try:
                apply(fut.result())
            except BrokenProcessPool:
                pool = None
                window = 1
            except Exception:
                pass
            advance(weight)
    rep("Optimising images", 0.90)


def compress_pdf(src: str, dst: str, dpi_threshold: int, dpi_target: int,
                 quality: int, strip_meta: bool = False, progress=None,
                 cancel=None, pool=None) -> tuple[int, int, bool]:
    """
    Compress `src` into `dst` (must differ from src).
    progress(stage, fraction) gets live updates; cancel is a threading.Event.
    Returns (original_size, final_size, was_compressed).
    """
    if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst)):
        raise ValueError("Output path equals input path")

    def rep(stage, frac):
        if progress:
            progress(stage, max(0.0, min(1.0, frac)))

    def check():
        if cancel is not None and cancel.is_set():
            raise Cancelled()

    original = os.path.getsize(src)
    tmp = dst + ".part"
    doc = None
    try:
        rep("Opening", 0.0)
        doc = fitz.open(src)
        if doc.needs_pass:
            raise PasswordError("Password protected")
        pages = doc.page_count
        rep("Opening", 0.02)

        optimise_images(doc, dpi_threshold, dpi_target, quality, rep, check, pool)

        if strip_meta:
            try:
                doc.set_metadata({})
                doc.del_xml_metadata()
            except Exception:
                pass

        check()
        rep("Saving & optimising structure", 0.91)
        save_opts = dict(garbage=4, deflate=True, deflate_images=True, deflate_fonts=True)
        try:
            doc.save(tmp, use_objstms=True, **save_opts)
        except TypeError:             # very old PyMuPDF without use_objstms
            doc.save(tmp, **save_opts)
        doc.close()
        doc = None

        rep("Verifying", 0.97)
        check_doc = fitz.open(tmp)
        ok = check_doc.page_count == pages
        check_doc.close()
        if not ok:
            raise RuntimeError("Verification failed (page count mismatch)")

        new = os.path.getsize(tmp)
        if 0 < new < original:
            os.replace(tmp, dst)
            rep("Done", 1.0)
            return original, new, True

        os.remove(tmp)                 # no gain: keep an exact copy of the original
        shutil.copyfile(src, dst)
        rep("Done", 1.0)
        return original, original, False
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def friendly_error(exc: Exception) -> tuple[str, str]:
    """(short label for the table, full message for the status bar)"""
    name = type(exc).__name__
    text = str(exc)
    if isinstance(exc, PasswordError):
        return "Password", "Password-protected PDFs are skipped"
    if name in ("EmptyFileError",) or "empty" in text.lower():
        return "Empty file", "The file is empty"
    if name in ("FileDataError", "FileNotFoundError") or "Failed to open" in text:
        return "Not a PDF", "The file is damaged or not a valid PDF"
    if isinstance(exc, PermissionError):
        return "No access", "Permission denied (check the output folder)"
    return "Failed", text or name


# ===================================================================== GUI
# ====================================================================
#  TOOL FRAMEWORK
# ====================================================================
class ToolError(Exception):
    """A problem the user can understand. short = table label, full = status line."""

    def __init__(self, short: str, full: str | None = None):
        super().__init__(full or short)
        self.short = short
        self.full = full or short


def open_pdf(path: str, password: str = ""):
    try:
        doc = fitz.open(path)
    except Exception as e:
        short, full = friendly_error(e)
        raise ToolError(short, full)
    if doc.needs_pass:
        if password and doc.authenticate(password):
            return doc
        doc.close()
        raise ToolError("Password", "Password-protected: use the Unlock PDF tool first")
    return doc


def parse_pages(spec: str, n: int, allow_dupes: bool = False) -> list[int]:
    """'1-3,5,8-' / 'all' / 'odd' / 'even' / 'last' -> list of 0-based page indexes."""
    spec = (spec or "").strip().lower()
    if spec in ("", "all", "*"):
        return list(range(n))
    if spec == "odd":
        return list(range(0, n, 2))
    if spec == "even":
        return list(range(1, n, 2))
    out: list[int] = []

    def num(tok: str) -> int:
        tok = tok.strip()
        if tok in ("last", "n"):
            return n
        try:
            v = int(tok)
        except ValueError:
            raise ToolError("Pages", f"Cannot understand '{tok}' in the page list")
        if not 1 <= v <= n:
            raise ToolError("Pages", f"Page {v} is outside 1-{n} for this file")
        return v

    for part in spec.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part and not part.startswith("-"):
            a_s, b_s = part.split("-", 1)
            a = num(a_s) if a_s.strip() else 1
            b = num(b_s) if b_s.strip() else n
        else:
            a = b = num(part)
        out.extend(range(a - 1, b) if a <= b else range(a - 1, b - 2, -1))
    if not out:
        raise ToolError("Pages", "No pages selected")
    if not allow_dupes:
        seen: set[int] = set()
        out = [p for p in out if not (p in seen or seen.add(p))]
    return out


def hex_to_rgb(value: str) -> tuple[float, float, float]:
    h = (value or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        if len(h) != 6:
            raise ValueError
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))      # type: ignore
    except ValueError:
        raise ToolError("Colour", f"'{value}' is not a valid colour (use #RRGGBB)")


def save_pdf(doc, dst: str, strong: bool = False, **kw) -> None:
    tmp = dst + ".part"
    try:
        doc.save(tmp, garbage=4 if strong else 3, deflate=True, **kw)
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


_UNICODE_FONTS = [
    r"C:\Windows\Fonts\Nirmala.ttf", r"C:\Windows\Fonts\arialuni.ttf",
    r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]
_BASE14 = {"Helvetica": ("helv", "hebo"), "Times": ("tiro", "tibo"), "Courier": ("cour", "cobo")}


def make_font(text: str, family: str = "Helvetica", bold: bool = False):
    """Return (insert_text kwargs, measure(text, size)). Handles non-Latin text too."""
    try:
        text.encode("latin-1")
        name = _BASE14.get(family, _BASE14["Helvetica"])[1 if bold else 0]
        return dict(fontname=name), lambda t, s: fitz.get_text_length(t, fontname=name, fontsize=s)
    except UnicodeEncodeError:
        for path in _UNICODE_FONTS:
            if os.path.exists(path):
                font = fitz.Font(fontfile=path)
                return (dict(fontname="UF", fontfile=path),
                        lambda t, s, f=font: f.text_length(t, fontsize=s))
        raise ToolError("Font", "This text needs a Unicode font, but none was found on this computer")


def draw_text(page, x, y, text, fk, size, color, opacity=1.0, angle=0.0,
              pivot=None, overlay=True):
    """Draw text at a baseline-left point given in VISIBLE page coordinates (rotation-safe)."""
    pt = fitz.Point(x, y) * page.derotation_matrix
    kw = dict(fontsize=size, color=color, rotate=page.rotation, overlay=overlay)
    kw.update(fk)
    if opacity < 1.0:
        kw["fill_opacity"] = opacity
    if angle:
        pv = fitz.Point(*(pivot or (x, y))) * page.derotation_matrix
        kw["morph"] = (pv, fitz.Matrix(angle))
    page.insert_text(pt, text, **kw)


def to_roman(n: int, upper: bool = True) -> str:
    vals = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
            (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
    out = ""
    for v, s in vals:
        while n >= v:
            out += s
            n -= v
    return out.upper() if upper else out


def to_alpha(n: int, upper: bool = True) -> str:
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(97 + r) + out
    return out.upper() if upper else out


NUM_STYLES = {
    "1, 2, 3": lambda n: str(n), "i, ii, iii": lambda n: to_roman(n, False),
    "I, II, III": lambda n: to_roman(n, True), "a, b, c": lambda n: to_alpha(n, False),
    "A, B, C": lambda n: to_alpha(n, True),
}
NUM_FORMATS = {
    "1": "{n}", "Page 1": "Page {n}", "Page 1 of N": "Page {n} of {total}",
    "1 of N": "{n} of {total}", "1 / N": "{n} / {total}", "- 1 -": "- {n} -",
}


class Job:
    """Everything a tool needs while running (lives in the worker thread)."""

    def __init__(self, files, opts, out_dir, pool, cancel, emit):
        self.files, self.opts, self.out_dir = files, opts, out_dir
        self.pool, self.cancel, self._emit = pool, cancel, emit
        self.outputs: list[str] = []
        self.totals = [0, 0]
        self.cur = 0
        self._taken: set[str] = set()
        self._base, self._w, self._total = 0.0, 1.0, 1.0
        self._last = (0.0, "")

    def check(self):
        if self.cancel.is_set():
            raise Cancelled()

    def report(self, frac: float, stage: str = ""):
        frac = max(0.0, min(1.0, frac))
        now = time.monotonic()
        if frac >= 1.0 or stage != self._last[1] or now - self._last[0] >= 0.08:
            self._last = (now, stage)
            self._emit(("prog", (self._base + self._w * frac) / self._total, stage, self.cur, frac))

    def mark(self, idx: int, text: str, tag: str = "done"):
        self._emit(("row", idx, text, "", tag))

    def folder_for(self, src: str) -> str:
        return self.out_dir or os.path.dirname(os.path.abspath(src))

    def out_path(self, src: str, suffix: str, ext: str = ".pdf", name: str | None = None) -> str:
        folder = self.folder_for(src)
        os.makedirs(folder, exist_ok=True)
        base = name or os.path.splitext(os.path.basename(src))[0]
        return unique_path(os.path.join(folder, f"{base}{suffix}{ext}"), self._taken)

    def out_folder(self, src: str, suffix: str) -> str:
        base = os.path.join(self.folder_for(src), os.path.splitext(os.path.basename(src))[0] + suffix)
        path, i = base, 1
        while os.path.exists(path) or os.path.normcase(path) in self._taken:
            i += 1
            path = f"{base} ({i})"
        self._taken.add(os.path.normcase(path))
        os.makedirs(path, exist_ok=True)
        return path

    def add_output(self, path: str):
        self.outputs.append(path)


class Tool:
    key = name = group = desc = ""
    color = "#E5322D"
    kind = "pdf"                 # input kind: "pdf" or "image"
    per_file = True              # False: process_all() gets the whole list (merge...)
    uses_pool = False
    suffix = "_out"
    action = "Start"
    fields: list[dict] = []

    def validate(self, opts: dict, files: list[str]) -> None:
        pass

    def process_one(self, src: str, job: Job) -> str:
        raise NotImplementedError

    def process_all(self, files: list[str], job: Job) -> str:
        raise NotImplementedError

    def summary(self, job: Job) -> str:
        return ""


TOOLS: list[Tool] = []


def register(cls):
    TOOLS.append(cls())
    return cls


def _pct(a: int, b: int) -> str:
    if not a:
        return "0%"
    p = (1 - b / a) * 100
    return f"{p:.1f}%" if p >= 99 else f"{p:.0f}%"


# ====================================================================
#  OPTIMIZE
# ====================================================================
COMPRESS_LADDER = [(400, 300, 90), (250, 200, 80), (180, 150, 70),
                   (160, 120, 60), (130, 96, 50), (100, 72, 40)]


@register
class CompressTool(Tool):
    key, name, group = "compress", "Compress PDF", "Optimize"
    color = "#E5322D"
    desc = "Shrink PDFs by up to 99% while text stays sharp and images stay clear."
    uses_pool = True
    suffix = "_compressed"
    action = "Compress PDFs"
    fields = [
        dict(key="level", label="Compression level", type="choice", choices=list(PRESETS),
             default="Balanced (Recommended)", help=lambda o: PRESETS[o["level"]][3]),
        dict(key="dpi", label="Image resolution", type="slider", min=72, max=400, default=200,
             fmt="{} DPI", show=lambda o: o["level"] == "Custom"),
        dict(key="quality", label="Image quality", type="slider", min=30, max=95, default=80,
             fmt="{} %", show=lambda o: o["level"] == "Custom"),
        dict(key="target", label="Target size in MB  (0 = off)", type="float", min=0, max=100000,
             step=0.5, default=0.0,
             help=lambda o: "Automatically tries stronger settings until each file fits."
             if o["target"] else "Optional: e.g. 2 for email limits."),
        dict(key="strip", label="Strip metadata (author, software)", type="bool", default=False),
        dict(key="turbo", label="Turbo mode - use all CPU cores", type="bool", default=True),
    ]

    def process_one(self, src, job):
        o = job.opts
        thr, dpi, q, _ = PRESETS[o["level"]]
        if thr is None:
            dpi, q = int(o["dpi"]), int(o["quality"])
            thr = int(dpi * 1.25)
        target = float(o["target"]) * 1024 * 1024
        ladder = [(thr, dpi, q)]
        if target > 0:
            ladder += [e for e in COMPRESS_LADDER if e[1] < dpi]
        dst = job.out_path(src, self.suffix)
        k = len(ladder)
        tmps: list[str] = []
        best = None
        try:
            for j, (t, d, qq) in enumerate(ladder):
                job.check()
                tmp = f"{dst}.try{j}"
                tmps.append(tmp)

                def cb(stage, frac, j=j):
                    job.report((j + frac) / k, stage + (f" (pass {j + 1}/{k})" if k > 1 else ""))

                a, b, _ = compress_pdf(src, tmp, t, d, qq, strip_meta=o["strip"],
                                       progress=cb, cancel=job.cancel, pool=job.pool)
                if best is None or b < best[1]:
                    best = (tmp, b, a)
                if target <= 0 or b <= target:
                    break
            os.replace(best[0], dst)
        finally:
            for t in tmps:
                if os.path.exists(t):
                    try:
                        os.remove(t)
                    except OSError:
                        pass
        job.add_output(dst)
        a, b = best[2], best[1]
        job.totals[0] += a
        job.totals[1] += b
        if b >= a:
            return "Already optimal"
        note = ""
        if target > 0:
            note = "  - target met" if b <= target else "  - closest possible"
        return f"{human(a)} → {human(b)}  (-{_pct(a, b)}){note}"

    def summary(self, job):
        a, b = job.totals
        return f"{human(a)} → {human(b)}  (saved {_pct(a, b)})" if a and b < a else ""


# ====================================================================
#  ORGANIZE
# ====================================================================
@register
class MergeTool(Tool):
    key, name, group = "merge", "Merge PDF", "Organize"
    color = "#F28C00"
    desc = "Combine several PDFs into one. Drag the order with Move Up / Move Down."
    per_file = False
    action = "Merge PDFs"
    fields = [
        dict(key="outname", label="Output file name", type="text", default="merged"),
        dict(key="bookmarks", label="Add a bookmark for every file", type="bool", default=True),
    ]

    def validate(self, opts, files):
        if len(files) < 2:
            raise ToolError("Files", "Add at least two PDFs to merge")

    def process_all(self, files, job):
        out = fitz.open()
        toc, n, total_pages = [], len(files), 0
        try:
            for i, path in enumerate(files):
                job.check()
                job.report(i / n * 0.9, f"Adding {i + 1}/{n}")
                try:
                    src = open_pdf(path)
                except ToolError as e:
                    raise ToolError(e.short, f"{os.path.basename(path)}: {e.full}")
                start = out.page_count
                out.insert_pdf(src)
                if src.page_count:
                    toc.append([1, os.path.splitext(os.path.basename(path))[0], start + 1])
                src.close()
                job.mark(i, "Added")
            if job.opts["bookmarks"] and toc:
                out.set_toc(toc)
            job.report(0.93, "Saving")
            name = (job.opts["outname"] or "merged").strip()
            name = name[:-4] if name.lower().endswith(".pdf") else name
            dst = job.out_path(files[0], "", name=name or "merged")
            total_pages = out.page_count
            save_pdf(out, dst, strong=True)
        finally:
            out.close()
        job.add_output(dst)
        return f"Merged {n} files ({total_pages} pages) → {os.path.basename(dst)}"


@register
class SplitTool(Tool):
    key, name, group = "split", "Split PDF", "Organize"
    color = "#2D9CDB"
    desc = "Cut a PDF into separate files by page, by count or by your own ranges."
    suffix = "_split"
    action = "Split PDFs"
    fields = [
        dict(key="mode", label="Split mode", type="choice",
             choices=["Every page", "Every N pages", "Custom ranges"], default="Every N pages"),
        dict(key="n", label="Pages per file", type="int", min=1, max=100000, default=2,
             show=lambda o: o["mode"] == "Every N pages"),
        dict(key="ranges", label="Ranges (one file each)", type="text", default="1-3, 4-6, 7-",
             show=lambda o: o["mode"] == "Custom ranges",
             help=lambda o: "Example: 1-3, 4-6, 7-  creates three files."),
    ]

    def validate(self, opts, files):
        if opts["mode"] == "Custom ranges" and not str(opts["ranges"]).strip():
            raise ToolError("Ranges", "Enter at least one page range")

    def process_one(self, src, job):
        doc = open_pdf(src)
        try:
            n = doc.page_count
            o = job.opts
            if o["mode"] == "Every page":
                groups = [[i] for i in range(n)]
            elif o["mode"] == "Every N pages":
                step = max(1, int(o["n"]))
                groups = [list(range(i, min(i + step, n))) for i in range(0, n, step)]
            else:
                groups = [parse_pages(part, n) for part in str(o["ranges"]).split(",") if part.strip()]
            folder = job.out_folder(src, self.suffix)
            base = os.path.splitext(os.path.basename(src))[0]
            width = len(str(len(groups)))
            for k, pages in enumerate(groups, 1):
                job.check()
                job.report((k - 1) / len(groups), f"Writing file {k}/{len(groups)}")
                part = fitz.open()
                try:
                    a, b = pages[0], pages[-1]
                    if pages == list(range(a, b + 1)):
                        part.insert_pdf(doc, from_page=a, to_page=b)
                    else:
                        for p in pages:
                            part.insert_pdf(doc, from_page=p, to_page=p)
                    label = f"{a + 1}" if a == b else f"{a + 1}-{b + 1}"
                    dst = os.path.join(folder, f"{base}_part{k:0{width}d}_p{label}.pdf")
                    save_pdf(part, dst)
                finally:
                    part.close()
                job.add_output(dst)
            return f"{len(groups)} files created"
        finally:
            doc.close()


@register
class OrganizeTool(Tool):
    key, name, group = "pages", "Extract / Delete Pages", "Organize"
    color = "#27AE60"
    desc = "Keep only the pages you need, remove pages, reverse or re-order them."
    suffix = "_pages"
    action = "Apply to PDFs"
    fields = [
        dict(key="mode", label="What to do", type="choice",
             choices=["Extract pages", "Delete pages", "Reverse order", "Custom order"],
             default="Extract pages",
             help=lambda o: {"Extract pages": "Keeps only the pages you list.",
                             "Delete pages": "Removes the pages you list.",
                             "Reverse order": "Last page becomes first.",
                             "Custom order": "Pages appear exactly as listed (repeats allowed)."}[o["mode"]]),
        dict(key="pages", label="Pages", type="text", default="1-3, 5",
             show=lambda o: o["mode"] != "Reverse order",
             help=lambda o: "Examples: 1-3, 5, 8-   |   odd   |   even   |   last"),
    ]

    def process_one(self, src, job):
        doc = open_pdf(src)
        try:
            n, mode = doc.page_count, job.opts["mode"]
            if mode == "Reverse order":
                order = list(range(n - 1, -1, -1))
            elif mode == "Extract pages":
                order = parse_pages(job.opts["pages"], n)
            elif mode == "Delete pages":
                gone = set(parse_pages(job.opts["pages"], n))
                order = [i for i in range(n) if i not in gone]
                if not order:
                    raise ToolError("Pages", "That would delete every page")
            else:
                order = parse_pages(job.opts["pages"], n, allow_dupes=True)
            job.report(0.3, "Rearranging")
            doc.select(order)
            dst = job.out_path(src, self.suffix)
            job.report(0.6, "Saving")
            save_pdf(doc, dst, strong=True)
        finally:
            doc.close()
        job.add_output(dst)
        return f"{n} → {len(order)} pages"


@register
class RotateTool(Tool):
    key, name, group = "rotate", "Rotate PDF", "Organize"
    color = "#8E44AD"
    desc = "Turn all pages, or only the ones you choose, the right way up."
    suffix = "_rotated"
    action = "Rotate PDFs"
    fields = [
        dict(key="angle", label="Rotation", type="choice",
             choices=["90° clockwise", "180°", "90° counter-clockwise"], default="90° clockwise"),
        dict(key="pages", label="Pages", type="text", default="all",
             help=lambda o: "all, odd, even, or a list like 1-3, 7"),
    ]

    def process_one(self, src, job):
        doc = open_pdf(src)
        try:
            deg = {"90° clockwise": 90, "180°": 180, "90° counter-clockwise": 270}[job.opts["angle"]]
            pages = parse_pages(job.opts["pages"], doc.page_count)
            for k, p in enumerate(pages):
                job.check()
                pg = doc[p]
                pg.set_rotation((pg.rotation + deg) % 360)
                if k % 20 == 0:
                    job.report(0.8 * k / len(pages), "Rotating")
            dst = job.out_path(src, self.suffix)
            job.report(0.85, "Saving")
            save_pdf(doc, dst)
        finally:
            doc.close()
        job.add_output(dst)
        return f"{len(pages)} pages rotated"


# ====================================================================
#  EDIT
# ====================================================================
NUM_POSITIONS = ["Bottom center", "Bottom right", "Bottom left", "Top center", "Top right",
                 "Top left", "Bottom outside (books)", "Top outside (books)"]


@register
class NumberTool(Tool):
    key, name, group = "numbers", "Page Numbers", "Edit"
    color = "#16A085"
    desc = "Add professional page numbers: any position, style, range and starting number."
    suffix = "_numbered"
    action = "Add Page Numbers"
    fields = [
        dict(key="format", label="Number format", type="choice",
             choices=list(NUM_FORMATS) + ["Custom"], default="Page 1 of N"),
        dict(key="custom", label="Custom text", type="text", default="Page {n} of {total}",
             show=lambda o: o["format"] == "Custom", help=lambda o: "Use {n} for the number and {total} for the last number."),
        dict(key="style", label="Numerals", type="choice", choices=list(NUM_STYLES), default="1, 2, 3"),
        dict(key="position", label="Position", type="choice", choices=NUM_POSITIONS, default="Bottom center"),
        dict(key="pages", label="Pages to number", type="text", default="all",
             help=lambda o: "all, odd, even, or e.g. 3-  to skip a cover and contents."),
        dict(key="start", label="First number", type="int", min=0, max=99999, default=1,
             help=lambda o: "The first numbered page gets this number."),
        dict(key="font", label="Font", type="choice", choices=list(_BASE14), default="Helvetica"),
        dict(key="bold", label="Bold", type="bool", default=False),
        dict(key="size", label="Font size", type="slider", min=6, max=36, default=11, fmt="{} pt"),
        dict(key="color", label="Colour", type="color", default="#000000"),
        dict(key="margin", label="Distance from edge", type="slider", min=10, max=90, default=30, fmt="{} pt"),
    ]

    def validate(self, opts, files):
        if opts["format"] == "Custom":
            try:
                str(opts["custom"]).format(n=1, total=9)
            except (KeyError, IndexError, ValueError):
                raise ToolError("Format", "Custom text may only use {n} and {total}")
        hex_to_rgb(opts["color"])

    def process_one(self, src, job):
        o = job.opts
        doc = open_pdf(src)
        try:
            pages = parse_pages(o["pages"], doc.page_count)
            tpl = str(o["custom"]) if o["format"] == "Custom" else NUM_FORMATS[o["format"]]
            style = NUM_STYLES[o["style"]]
            first = int(o["start"])
            last = first + len(pages) - 1
            color = hex_to_rgb(o["color"])
            size, margin = float(o["size"]), float(o["margin"])
            pos = o["position"]
            probe = tpl.format(n=style(max(first, 1)), total=style(max(last, 1)))
            fk, measure = make_font(probe, o["font"], o["bold"])
            for k, pno in enumerate(pages):
                job.check()
                page = doc[pno]
                n = first + k
                text = tpl.format(n=style(n) if n > 0 or o["style"] == "1, 2, 3" else str(n),
                                  total=style(last) if last > 0 or o["style"] == "1, 2, 3" else str(last))
                w_, h_ = page.rect.width, page.rect.height
                tw = measure(text, size)
                where = pos
                if "outside" in pos:                      # book layout: odd pages right, even left
                    side = "right" if (pno + 1) % 2 == 1 else "left"
                    where = ("Bottom " if pos.startswith("Bottom") else "Top ") + side
                x = {"left": margin, "center": (w_ - tw) / 2, "right": w_ - margin - tw}[where.split()[1]]
                y = (h_ - margin) if where.startswith("Bottom") else (margin + size * 0.8)
                draw_text(page, x, y, text, fk, size, color)
                if k % 10 == 0:
                    job.report(0.9 * k / len(pages), "Numbering pages")
            dst = job.out_path(src, self.suffix)
            job.report(0.92, "Saving")
            save_pdf(doc, dst, strong=True)
        finally:
            doc.close()
        job.add_output(dst)
        return f"{len(pages)} pages numbered"


@register
class WatermarkTool(Tool):
    key, name, group = "watermark", "Watermark", "Edit"
    color = "#E84393"
    desc = "Stamp text such as CONFIDENTIAL or DRAFT across your pages."
    suffix = "_watermarked"
    action = "Add Watermark"
    fields = [
        dict(key="text", label="Watermark text", type="text", default="CONFIDENTIAL"),
        dict(key="layout", label="Layout", type="choice", choices=["Single (centre)", "Tiled"],
             default="Single (centre)"),
        dict(key="size", label="Font size", type="slider", min=10, max=200, default=64, fmt="{} pt"),
        dict(key="opacity", label="Opacity", type="slider", min=5, max=100, default=25, fmt="{} %"),
        dict(key="angle", label="Angle", type="slider", min=-90, max=90, default=45, fmt="{}°"),
        dict(key="color", label="Colour", type="color", default="#808080"),
        dict(key="font", label="Font", type="choice", choices=list(_BASE14), default="Helvetica"),
        dict(key="bold", label="Bold", type="bool", default=True),
        dict(key="over", label="Place over the page content", type="bool", default=True,
             help=lambda o: "Turn off to put it behind text and images."),
        dict(key="pages", label="Pages", type="text", default="all"),
    ]

    def validate(self, opts, files):
        if not str(opts["text"]).strip():
            raise ToolError("Text", "Enter the watermark text")
        hex_to_rgb(opts["color"])

    def process_one(self, src, job):
        o = job.opts
        doc = open_pdf(src)
        try:
            pages = parse_pages(o["pages"], doc.page_count)
            text = str(o["text"]).strip()
            fk, measure = make_font(text, o["font"], o["bold"])
            size, op = float(o["size"]), float(o["opacity"]) / 100
            color, angle, over = hex_to_rgb(o["color"]), float(o["angle"]), bool(o["over"])
            tw = measure(text, size)
            for k, pno in enumerate(pages):
                job.check()
                page = doc[pno]
                W, H = page.rect.width, page.rect.height
                if o["layout"].startswith("Single"):
                    cx, cy = W / 2, H / 2
                    draw_text(page, cx - tw / 2, cy + size * 0.3, text, fk, size, color, op, angle,
                              pivot=(cx, cy), overlay=over)
                else:
                    gx, gy = tw * 1.5, size * 5
                    row, y = 0, gy / 2
                    while y < H + gy:
                        x = gx / 2 - (gx / 2 if row % 2 else 0)
                        while x < W + gx:
                            draw_text(page, x - tw / 2, y + size * 0.3, text, fk, size, color, op,
                                      angle, pivot=(x, y), overlay=over)
                            x += gx
                        y += gy
                        row += 1
                if k % 5 == 0:
                    job.report(0.9 * k / len(pages), "Stamping pages")
            dst = job.out_path(src, self.suffix)
            job.report(0.92, "Saving")
            save_pdf(doc, dst, strong=True)
        finally:
            doc.close()
        job.add_output(dst)
        return f"{len(pages)} pages stamped"


# ====================================================================
#  CONVERT
# ====================================================================
IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".gif")


@register
class ImagesToPdfTool(Tool):
    key, name, group = "img2pdf", "Images to PDF", "Convert"
    color = "#F1B500"
    kind = "image"
    desc = "Turn photos and scans into one PDF. JPEGs are embedded without quality loss."
    per_file = False
    action = "Create PDF"
    fields = [
        dict(key="size", label="Page size", type="choice", choices=["A4", "US Letter", "Fit to image"],
             default="A4"),
        dict(key="orient", label="Orientation", type="choice", choices=["Auto", "Portrait", "Landscape"],
             default="Auto", show=lambda o: o["size"] != "Fit to image"),
        dict(key="margin", label="Margin", type="slider", min=0, max=80, default=0, fmt="{} pt",
             show=lambda o: o["size"] != "Fit to image"),
        dict(key="outname", label="Output file name", type="text", default="images"),
    ]

    def validate(self, opts, files):
        if not files:
            raise ToolError("Files", "Add at least one image")

    def process_all(self, files, job):
        o, n = job.opts, len(files)
        base = {"A4": (595.28, 841.89), "US Letter": (612.0, 792.0)}.get(o["size"])
        margin = float(o["margin"])
        doc = fitz.open()
        try:
            for i, path in enumerate(files):
                job.check()
                job.report(i / n * 0.92, f"Adding image {i + 1}/{n}")
                try:
                    with Image.open(path) as im:
                        fmt = (im.format or "").upper()
                        ori = im.getexif().get(0x0112, 1)
                        plain = im.mode in ("RGB", "L", "RGBA") and fmt in ("JPEG", "PNG") and ori in (1, None)
                        if plain:
                            w, h = im.size
                            stream = None
                        else:
                            from PIL import ImageOps
                            im2 = ImageOps.exif_transpose(im)
                            if im2.mode in ("P", "LA", "PA"):
                                im2 = im2.convert("RGBA")
                            elif im2.mode not in ("RGB", "L", "RGBA"):
                                im2 = im2.convert("RGB")
                            w, h = im2.size
                            buf = io.BytesIO()
                            if fmt in ("JPEG", "MPO", "WEBP") and im2.mode != "RGBA":
                                im2.save(buf, "JPEG", quality=95)
                            else:
                                im2.save(buf, "PNG")
                            stream = buf.getvalue()
                except Exception:
                    raise ToolError("Image", f"Cannot read image: {os.path.basename(path)}")
                if base is None:                             # fit to image
                    k = max(300.0, min(1500.0, max(w, h) * 0.48)) / max(w, h)
                    pw, ph = w * k, h * k
                    rect = fitz.Rect(0, 0, pw, ph)
                else:
                    pw, ph = base
                    landscape = (w > h) if o["orient"] == "Auto" else o["orient"] == "Landscape"
                    if landscape:
                        pw, ph = ph, pw
                    s = min((pw - 2 * margin) / w, (ph - 2 * margin) / h)
                    iw, ih = w * s, h * s
                    rect = fitz.Rect((pw - iw) / 2, (ph - ih) / 2, (pw + iw) / 2, (ph + ih) / 2)
                page = doc.new_page(width=pw, height=ph)
                if stream is None:
                    page.insert_image(rect, filename=path)
                else:
                    page.insert_image(rect, stream=stream)
                job.mark(i, "Added")
            job.report(0.95, "Saving")
            name = (o["outname"] or "images").strip()
            name = name[:-4] if name.lower().endswith(".pdf") else name
            dst = job.out_path(files[0], "", name=name or "images")
            save_pdf(doc, dst, strong=True)
        finally:
            doc.close()
        job.add_output(dst)
        return f"{n} images → {os.path.basename(dst)}"


@register
class PdfToImagesTool(Tool):
    key, name, group = "pdf2img", "PDF to Images", "Convert"
    color = "#9B59B6"
    desc = "Export pages as crisp PNG or JPG pictures at the resolution you choose."
    suffix = "_images"
    action = "Convert to Images"
    fields = [
        dict(key="format", label="Image format", type="choice", choices=["PNG", "JPG"], default="PNG"),
        dict(key="dpi", label="Resolution", type="slider", min=72, max=600, default=200, fmt="{} DPI"),
        dict(key="quality", label="JPG quality", type="slider", min=50, max=100, default=92, fmt="{} %",
             show=lambda o: o["format"] == "JPG"),
        dict(key="pages", label="Pages", type="text", default="all"),
    ]

    def process_one(self, src, job):
        o = job.opts
        doc = open_pdf(src)
        try:
            pages = parse_pages(o["pages"], doc.page_count)
            folder = job.out_folder(src, self.suffix)
            base = os.path.splitext(os.path.basename(src))[0]
            width = len(str(doc.page_count))
            dpi, png = int(o["dpi"]), o["format"] == "PNG"
            for k, pno in enumerate(pages):
                job.check()
                job.report(k / len(pages), f"Rendering page {k + 1}/{len(pages)}")
                pix = doc[pno].get_pixmap(dpi=dpi, alpha=False)
                path = os.path.join(folder, f"{base}_page_{pno + 1:0{width}d}.{'png' if png else 'jpg'}")
                if png:
                    pix.save(path)
                else:
                    mode = "L" if pix.n == 1 else "RGB"
                    Image.frombytes(mode, (pix.width, pix.height), pix.samples).save(
                        path, "JPEG", quality=int(o["quality"]), optimize=True, dpi=(dpi, dpi))
                pix = None
                job.add_output(path)
            return f"{len(pages)} images"
        finally:
            doc.close()


@register
class PdfToTextTool(Tool):
    key, name, group = "pdf2txt", "PDF to Text", "Convert"
    color = "#34495E"
    desc = "Extract all selectable text into a .txt file."
    suffix = "_text"
    action = "Extract Text"
    fields = [
        dict(key="markers", label="Mark page breaks", type="bool", default=True),
        dict(key="pages", label="Pages", type="text", default="all"),
    ]

    def process_one(self, src, job):
        doc = open_pdf(src)
        try:
            pages = parse_pages(job.opts["pages"], doc.page_count)
            chunks, chars = [], 0
            for k, pno in enumerate(pages):
                job.check()
                job.report(k / len(pages), "Reading text")
                t = doc[pno].get_text("text", sort=True)
                chars += len(t.strip())
                chunks.append((f"\n===== Page {pno + 1} =====\n" if job.opts["markers"] else "") + t)
            if chars == 0:
                raise ToolError("No text", "No selectable text found (this looks like a scanned PDF)")
            dst = job.out_path(src, self.suffix, ext=".txt")
            with open(dst, "w", encoding="utf-8") as f:
                f.write("".join(chunks).lstrip("\n"))
        finally:
            doc.close()
        job.add_output(dst)
        return f"{chars:,} characters"


# ====================================================================
#  SECURITY
# ====================================================================
@register
class ProtectTool(Tool):
    key, name, group = "protect", "Protect PDF", "Security"
    color = "#2C3E50"
    desc = "Lock PDFs with a password using strong AES-256 encryption."
    suffix = "_protected"
    action = "Protect PDFs"
    fields = [
        dict(key="user_pw", label="Password to open the file", type="text", secret=True, default=""),
        dict(key="owner_pw", label="Owner password (optional)", type="text", secret=True, default="",
             help=lambda o: "Empty = a random one is created, so the limits below cannot be removed by the reader."),
        dict(key="allow_print", label="Allow printing", type="bool", default=True),
        dict(key="allow_copy", label="Allow copying text and images", type="bool", default=True),
        dict(key="allow_edit", label="Allow editing and annotating", type="bool", default=False),
    ]

    def validate(self, opts, files):
        if not str(opts["user_pw"]):
            raise ToolError("Password", "Enter a password")

    def process_one(self, src, job):
        o = job.opts
        doc = open_pdf(src)
        try:
            perm = fitz.PDF_PERM_ACCESSIBILITY
            if o["allow_print"]:
                perm |= fitz.PDF_PERM_PRINT | fitz.PDF_PERM_PRINT_HQ
            if o["allow_copy"]:
                perm |= fitz.PDF_PERM_COPY
            if o["allow_edit"]:
                perm |= (fitz.PDF_PERM_MODIFY | fitz.PDF_PERM_ANNOTATE
                         | fitz.PDF_PERM_FORM | fitz.PDF_PERM_ASSEMBLE)
            job.report(0.3, "Encrypting")
            dst = job.out_path(src, self.suffix)
            save_pdf(doc, dst, encryption=fitz.PDF_ENCRYPT_AES_256, permissions=perm,
                     user_pw=str(o["user_pw"]), owner_pw=str(o["owner_pw"]) or secrets.token_urlsafe(18))
        finally:
            doc.close()
        job.add_output(dst)
        return "Locked (AES-256)"


@register
class UnlockTool(Tool):
    key, name, group = "unlock", "Unlock PDF", "Security"
    color = "#7F8C8D"
    desc = "Remove a password or print/copy restrictions from a PDF you own."
    suffix = "_unlocked"
    action = "Unlock PDFs"
    fields = [
        dict(key="password", label="Password (if the file asks for one)", type="text", secret=True, default=""),
    ]

    def process_one(self, src, job):
        try:
            doc = fitz.open(src)
        except Exception as e:
            short, full = friendly_error(e)
            raise ToolError(short, full)
        try:
            was_locked = doc.needs_pass
            if was_locked and not doc.authenticate(str(job.opts["password"])):
                raise ToolError("Wrong password", "That password does not open this file")
            job.report(0.4, "Removing protection")
            dst = job.out_path(src, self.suffix)
            save_pdf(doc, dst, encryption=fitz.PDF_ENCRYPT_NONE)
        finally:
            doc.close()
        job.add_output(dst)
        return "Password removed" if was_locked else "Restrictions removed"


@register
class RepairTool(Tool):
    key, name, group = "repair", "Repair PDF", "Security"
    color = "#C0392B"
    desc = "Rebuild damaged PDFs so they open again, and clean up wasted data."
    suffix = "_repaired"
    action = "Repair PDFs"
    fields: list[dict] = []

    def process_one(self, src, job):
        doc = open_pdf(src)
        try:
            job.report(0.3, "Rebuilding")
            dst = job.out_path(src, self.suffix)
            save_pdf(doc, dst, strong=True, clean=True)
            pages = doc.page_count
        finally:
            doc.close()
        job.add_output(dst)
        return f"Rebuilt - {pages} pages"


# ====================================================================
#  RUNNER (worker thread)
# ====================================================================
def run_tool(tool: Tool, files, opts, out_dir, cancel, emit, workers) -> None:
    pool = None
    if tool.uses_pool and workers > 1 and opts.get("turbo", True):
        try:
            pool = ProcessPoolExecutor(max_workers=workers)
            for _ in range(workers):
                pool.submit(_noop)
        except Exception:
            pool = None
    job = Job(files, opts, out_dir, pool, cancel, emit)
    ok = failed = 0
    cancelled = False
    try:
        if tool.per_file:
            sizes = []
            for p in files:
                try:
                    sizes.append(float(max(os.path.getsize(p), 1)))
                except OSError:
                    sizes.append(1.0)
            job._total = sum(sizes) or 1.0
            for i, src in enumerate(files):
                if cancel.is_set():
                    cancelled = True
                    break
                job.cur, job._base, job._w = i, sum(sizes[:i]), sizes[i]
                emit(("file", i, os.path.basename(src), len(files)))
                try:
                    detail = tool.process_one(src, job)
                    emit(("row", i, detail or "Done", "", "done"))
                    ok += 1
                except Cancelled:
                    emit(("row", i, "Cancelled", "", "err"))
                    cancelled = True
                    break
                except ToolError as e:
                    emit(("row", i, e.short, "", "err"))
                    emit(("status", f"{os.path.basename(src)}: {e.full}"))
                    failed += 1
                except Exception as e:
                    short, full = friendly_error(e)
                    emit(("row", i, short, "", "err"))
                    emit(("status", f"{os.path.basename(src)}: {full}"))
                    failed += 1
                emit(("prog", (job._base + job._w) / job._total, "", i, 1.0))
        else:
            job.cur, job._base, job._w, job._total = -1, 0.0, 1.0, 1.0
            emit(("file", -1, f"{len(files)} files", 1))
            try:
                detail = tool.process_all(files, job)
                emit(("row", -1, "Done", "", "done"))
                emit(("status", detail))
                ok = 1
            except Cancelled:
                emit(("row", -1, "Cancelled", "", "err"))
                cancelled = True
            except ToolError as e:
                emit(("row", -1, e.short, "", "err"))
                emit(("status", e.full))
                failed = 1
            except Exception as e:
                short, full = friendly_error(e)
                emit(("row", -1, short, "", "err"))
                emit(("status", full))
                failed = 1
    finally:
        if pool is not None:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                pool.shutdown(wait=False)
            except Exception:
                pass
    emit(("finish", ok, failed, cancelled, tool.summary(job), job.outputs))

