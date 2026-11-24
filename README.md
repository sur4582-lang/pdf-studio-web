# PDF Studio Pro – Web Edition

Aapke desktop app (`pdf_studio_pro.py`) ka web version. Original engine (`engine.py`) bilkul wahi hai, bas Tkinter GUI hata kar
Flask web interface lagaya gaya hai. **13 tools:** Compress, Merge, Split, Extract/Delete Pages, Rotate, Page Numbers,
Watermark, Images→PDF, PDF→Images, PDF→Text, Protect, Unlock, Repair.

## Local par chalao
```bash
pip install -r requirements.txt
python app.py            # http://localhost:8000
python tests.py          # 25 automatic checks
```

## LIVE karne ke 2 aasan raaste

### Raasta A – Render.com (sabse aasan, free se shuru)
1. GitHub par naya repo banao (public) aur is folder ki saari files upload/push karo.
2. render.com par signup → **New → Blueprint** (ya Web Service → Docker) → apna repo chuno. `render.yaml` apne aap detect hoga.
3. Environment variables me `SITE_URL`, `CONTACT_EMAIL`, `SOURCE_URL` (apne GitHub repo ka link) bharo → Deploy.
4. 3–5 minute me `https://pdf-studio.onrender.com` jaisa link mil jayega.
5. Apna domain: Render → Settings → Custom Domains. HTTPS free milta hai.

> Free plan 15 min idle ke baad so jata hai (pehli request slow). Real traffic ke liye Starter plan (~$7/mo) lo.

### Raasta B – VPS (Hetzner / DigitalOcean / Hostinger, ~₹400–800/mo)
```bash
# Ubuntu server par
curl -fsSL https://get.docker.com | sh
git clone https://github.com/YOURNAME/pdf-studio-web && cd pdf-studio-web
cp .env.example .env && nano .env
docker build -t pdfstudio .
docker run -d --restart=always --env-file .env -p 8000:8000 --name pdfstudio pdfstudio
```
HTTPS ke liye Caddy lagao (auto Let's Encrypt). `/etc/caddy/Caddyfile`:
```
yourdomain.com {
    request_body { max_size 60MB }
    reverse_proxy localhost:8000
}
```

## Live hone ke baad zaroori steps
1. **Google Search Console** me site add karo → `https://yourdomain.com/sitemap.xml` submit karo (token ko `GSC_VERIFY` me daalo).
2. **Google Analytics** ID ko `ANALYTICS_ID` me daalo.
3. **AdSense** apply karo (privacy/terms/about/contact pages already bane hain). Approval ke baad `ADSENSE_CLIENT` set karo;
   `/ads.txt` apne aap ban jata hai.
4. `templates/pages/*.html` me apna naam/email check karo.

## ⚠️ Licence – zaroor padho
`PyMuPDF` **AGPL-3.0** licence me hai. Website par use karne ka matlab: aapko users ko is web app ka **source code dena** hoga
(GitHub repo public rakho aur `SOURCE_URL` set karo – footer me "Source code" link aa jayega). Agar code private rakhna hai to
Artifex se commercial licence lena padega, ya `pikepdf`/`pypdf` par engine likhna padega.

## Privacy & safety jo already laga hai
- Upload sirf PDF/image (extension + content check), size/page/file-count limit
- Original upload processing ke turant baad delete; result 10 min me auto-delete
- Random unguessable download links, path-traversal blocked
- IP rate limit, parallel-job limit, 4-minute job timeout
- Server par Turbo (multi-process) band hai taaki shared server crash na ho

## Paise kamane ke agle steps
- AdSense (traffic aane par) · Freemium: free limit 50 MB, premium 200 MB + no ads (Razorpay) · API access
- Har tool ka alag SEO page already hai (`/compress-pdf`, `/merge-pdf`, `/jpg-to-pdf` …). Content `seo.py` me edit karo.
