"""End-to-end tests: every tool through the real HTTP API."""
import io, os, random, zipfile
import fitz
from PIL import Image
import app as A

c = A.app.test_client()


def make_pdf(pages=6, big=True):
    doc = fitz.open()
    for i in range(pages):
        p = doc.new_page()
        p.insert_text((72, 72), f"Hello page {i+1} नमस्ते", fontsize=20)
        if big:
            im = Image.effect_noise((900, 900), 60).convert("RGB")
            b = io.BytesIO(); im.save(b, "PNG")
            p.insert_image(fitz.Rect(72, 120, 520, 560), stream=b.getvalue())
    return doc.tobytes()


def img(fmt="PNG", color=(200, 30, 30)):
    b = io.BytesIO(); Image.new("RGB", (400, 300), color).save(b, fmt); return b.getvalue()


def run(key, files, **form):
    data = {k: str(v) for k, v in form.items()}
    data["files"] = [(io.BytesIO(b), n) for n, b in files]
    r = c.post(f"/api/run/{key}", data=data, content_type="multipart/form-data")
    return r, r.get_json()


def dl(j):
    r = c.get(j["download"]); assert r.status_code == 200
    return r.data


pdf = make_pdf()
print("source pdf", len(pdf)//1024, "KB")
ok = []

r, j = run("compress", [("a.pdf", pdf)], level="Strong Compression")
assert r.status_code == 200, j; out = dl(j); assert len(out) < len(pdf); ok.append(f"compress {len(pdf)//1024}KB->{len(out)//1024}KB ({j['summary']})")
r, j = run("compress", [("a.pdf", pdf)], level="Balanced (Recommended)", target=0.15)
assert r.status_code == 200, j; ok.append("compress target " + j["summary"])
r, j = run("merge", [("a.pdf", make_pdf(2, False)), ("b.pdf", make_pdf(3, False))], outname="both")
assert r.status_code == 200, j; assert fitz.open(stream=dl(j)).page_count == 5; assert j["name"].startswith("both"); ok.append("merge 2+3=5 pages")
r, j = run("split", [("a.pdf", pdf)], mode="Every page")
assert r.status_code == 200, j; z = zipfile.ZipFile(io.BytesIO(dl(j))); assert len(z.namelist()) == 6; ok.append("split -> 6 files zip")
r, j = run("split", [("a.pdf", pdf)], mode="Custom ranges", ranges="1-2, 3-")
assert r.status_code == 200, j; assert len(zipfile.ZipFile(io.BytesIO(dl(j))).namelist()) == 2; ok.append("split ranges")
r, j = run("pages", [("a.pdf", pdf)], mode="Extract pages", pages="1-3, 5")
assert fitz.open(stream=dl(j)).page_count == 4; ok.append("extract 4 pages")
r, j = run("pages", [("a.pdf", pdf)], mode="Delete pages", pages="1")
assert fitz.open(stream=dl(j)).page_count == 5; ok.append("delete page")
r, j = run("rotate", [("a.pdf", pdf)], angle="180°", pages="all")
assert fitz.open(stream=dl(j))[0].rotation == 180; ok.append("rotate 180")
r, j = run("numbers", [("a.pdf", pdf)], **{"format": "Page 1 of N", "position": "Bottom center", "pages": "all", "start": 1, "size": 11, "color": "#000000", "margin": 30, "font": "Helvetica", "style": "1, 2, 3", "custom": "x"})
assert r.status_code == 200, j; assert "Page 1 of 6" in fitz.open(stream=dl(j))[0].get_text(); ok.append("page numbers")
r, j = run("watermark", [("a.pdf", pdf)], text="DRAFT", layout="Tiled", size=40, opacity=30, angle=45, color="#ff0000", font="Helvetica", bold=1, over=1, pages="all")
assert r.status_code == 200, j; assert "DRAFT" in fitz.open(stream=dl(j))[0].get_text(); ok.append("watermark")
r, j = run("img2pdf", [("1.png", img()), ("2.jpg", img("JPEG")), ("3.webp", img("WEBP"))], size="A4", orient="Auto", margin=10, outname="photos")
assert r.status_code == 200, j; assert fitz.open(stream=dl(j)).page_count == 3; ok.append("images->pdf 3 pages")
r, j = run("pdf2img", [("a.pdf", pdf)], format="JPG", dpi=100, quality=80, pages="1-2")
assert r.status_code == 200, j; assert len(zipfile.ZipFile(io.BytesIO(dl(j))).namelist()) == 2; ok.append("pdf->jpg zip")
r, j = run("pdf2txt", [("a.pdf", pdf)], markers=1, pages="all")
assert r.status_code == 200, j; assert b"Hello page 1" in dl(j); ok.append("pdf->text")
r, j = run("protect", [("a.pdf", pdf)], user_pw="secret123", owner_pw="", allow_print=1, allow_copy=0, allow_edit=0)
assert r.status_code == 200, j; prot = dl(j); d = fitz.open(stream=prot); assert d.needs_pass; ok.append("protect (needs password)")
r, j = run("unlock", [("locked.pdf", prot)], password="secret123")
assert r.status_code == 200, j; assert not fitz.open(stream=dl(j)).needs_pass; ok.append("unlock")
r, j = run("repair", [("a.pdf", pdf[:-20])])
print("repair truncated ->", r.status_code, (j or {}).get("summary") or (j or {}).get("error"))
r, j = run("repair", [("a.pdf", pdf)])
assert r.status_code == 200, j; ok.append("repair")

# negative / security tests
r, j = run("compress", [("evil.exe", b"MZ....")]); assert r.status_code == 400, (r.status_code, j); ok.append("rejects .exe")
r, j = run("compress", [("fake.pdf", b"not a pdf at all")]); assert r.status_code == 400; ok.append("rejects fake pdf")
r, j = run("compress", [("../../etc/passwd.pdf", pdf)]); assert r.status_code == 200; assert ".." not in j["name"]; ok.append("path traversal name sanitised")
r, j = run("protect", [("a.pdf", pdf)], user_pw="", owner_pw="", allow_print=1, allow_copy=1, allow_edit=1)
print("protect empty pw ->", r.status_code, (j or {}).get("summary") or (j or {}).get("error"))
r = c.get("/api/download/../../etc/passwd"); assert r.status_code == 404; ok.append("download traversal blocked")
r = c.get("/api/download/" + "A"*20); assert r.status_code == 404; ok.append("unknown job 404")
r, j = run("compress", [("a.pdf", make_pdf(1, False))], level="Custom", dpi=9999, quality=1); assert r.status_code == 200; ok.append("option clamping")
big = A.MAX_PAGES + 1
r, j = run("compress", [("many.pdf", make_pdf(big, False))]); assert r.status_code == 400 and "pages" in j["error"]; ok.append(f"page limit ({big} pages) rejected")
# visibility endpoint
r = c.post("/api/visible/compress", data={"level": "Custom"}); assert r.get_json()["visible"]["dpi"] is True
r = c.post("/api/visible/compress", data={"level": "Maximum Quality"}); assert r.get_json()["visible"]["dpi"] is False; ok.append("conditional options")
# delete endpoint
r, j = run("repair", [("a.pdf", pdf)]); c.post(f"/api/delete/{j['id']}"); assert c.get(j["download"]).status_code == 404; ok.append("delete endpoint")
print(*["✔ " + o for o in ok], sep="\n"); print(len(ok), "checks passed")
