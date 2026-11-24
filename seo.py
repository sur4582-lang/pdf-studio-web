"""Page copy / SEO data for every tool (English + Hinglish keywords)."""

GROUP_LABELS = {
    "Optimize": "Optimize",
    "Organize": "Organize PDF",
    "Edit": "Edit PDF",
    "Convert": "Convert",
    "Security": "PDF Security",
}


def _faq(*pairs):
    return [dict(q=q, a=a) for q, a in pairs]


PRIVACY_FAQ = ("Is my file safe?",
               "Yes. Your file is processed on our server only to do the job you asked for. "
               "Uploaded originals are deleted right after processing and results are removed "
               "automatically within minutes. We never read, share or sell your documents.")

SEO = {
    "compress": dict(
        slug="compress-pdf", icon="🗜️",
        title="Compress PDF Online Free – Reduce PDF Size up to 99% | {site}",
        h1="Compress PDF",
        meta="Compress PDF online for free. Reduce PDF file size to 100KB, 200KB or 1MB without losing "
             "readable quality. PDF ka size kam karein – fast, no signup.",
        keywords="compress pdf, reduce pdf size, pdf compressor, compress pdf to 100kb, compress pdf to 200kb, "
                 "compress pdf to 1mb, pdf size kam kare, pdf compress online",
        steps=["Upload one or more PDF files.",
               "Pick a level, or set a target size in MB (for example 1 for email or form limits).",
               "Click Compress PDFs and download the smaller file."],
        about="Shrink scanned documents, marksheets, Aadhaar copies and large reports. Text stays sharp while "
              "heavy images are re-encoded intelligently. Set a target size and the tool automatically tries "
              "stronger settings until your file fits.",
        faq=_faq(("How do I compress a PDF to under 1 MB?",
                  "Set “Target size” to 1 and click Compress. The tool tries stronger settings step by step "
                  "until the file is as close to your target as possible."),
                 ("Will quality drop?",
                  "Balanced mode keeps text crisp and images clear. Use Maximum Quality for print, or "
                  "Extreme for the smallest file."),
                 PRIVACY_FAQ)),
    "merge": dict(
        slug="merge-pdf", icon="🔗",
        title="Merge PDF Online Free – Combine PDF Files | {site}",
        h1="Merge PDF",
        meta="Merge multiple PDF files into one PDF online for free. Drag to reorder, add bookmarks. "
             "PDF jodne wala tool – no signup.",
        keywords="merge pdf, combine pdf, join pdf files, pdf jodne wala, pdf merge online free",
        steps=["Upload the PDFs you want to combine.",
               "Drag files into the order you want.",
               "Click Merge and download one combined PDF."],
        about="Combine several PDFs into a single document in the exact order you choose. Optionally add a "
              "bookmark for every source file so long documents are easy to navigate.",
        faq=_faq(("Can I change the order?", "Yes, drag the files in the list or use the arrow buttons."),
                 PRIVACY_FAQ)),
    "split": dict(
        slug="split-pdf", icon="✂️",
        title="Split PDF Online Free – Separate PDF Pages | {site}",
        h1="Split PDF",
        meta="Split a PDF into single pages, fixed-size parts or custom page ranges. Free online PDF splitter – "
             "PDF todne wala tool.",
        keywords="split pdf, separate pdf pages, pdf todna, pdf splitter, split pdf by page range",
        steps=["Upload your PDF.", "Choose: every page, every N pages, or custom ranges like 1-3, 4-6.",
               "Download all parts as a ZIP."],
        about="Break one big PDF into smaller files. Use custom ranges such as “1-3, 4-6, 7-” to create exactly "
              "the files you need.",
        faq=_faq(("How do ranges work?", "Write ranges separated by commas. “7-” means page 7 to the end."),
                 PRIVACY_FAQ)),
    "pages": dict(
        slug="extract-delete-pdf-pages", icon="📑",
        title="Extract, Delete & Reorder PDF Pages Online Free | {site}",
        h1="Extract / Delete Pages",
        meta="Extract pages from PDF, delete unwanted pages, reverse or reorder pages online for free.",
        keywords="extract pdf pages, delete pdf pages, remove pages from pdf, reorder pdf pages, reverse pdf",
        steps=["Upload your PDF.", "Choose Extract, Delete, Reverse or Custom order and enter the pages.",
               "Download the new PDF."],
        about="Keep only the pages you need, remove the rest, or rearrange them. Supports lists like "
              "“1-3, 5, 8-”, plus odd, even and last.",
        faq=_faq(("What page formats are supported?", "Numbers, ranges, odd, even and last."), PRIVACY_FAQ)),
    "rotate": dict(
        slug="rotate-pdf", icon="🔄",
        title="Rotate PDF Pages Online Free | {site}",
        h1="Rotate PDF",
        meta="Rotate PDF pages 90°, 180° or 270° online for free. Rotate all pages or only selected ones.",
        keywords="rotate pdf, rotate pdf pages, pdf rotate online, pdf ghumana",
        steps=["Upload your PDF.", "Pick the angle and pages.", "Download the rotated PDF."],
        about="Fix sideways scans quickly. Rotate every page or just the pages you list.",
        faq=_faq(PRIVACY_FAQ)),
    "numbers": dict(
        slug="add-page-numbers-to-pdf", icon="🔢",
        title="Add Page Numbers to PDF Online Free | {site}",
        h1="Add Page Numbers",
        meta="Add page numbers to a PDF online. Choose position, format (Page 1 of N), roman numerals, font and colour.",
        keywords="add page numbers to pdf, pdf page numbering, number pdf pages online",
        steps=["Upload your PDF.", "Choose format, position, font and starting number.",
               "Download the numbered PDF."],
        about="Number reports, books and assignments. Supports Roman numerals, letters, custom text and "
              "book-style outside positions.",
        faq=_faq(PRIVACY_FAQ)),
    "watermark": dict(
        slug="watermark-pdf", icon="💧",
        title="Add Watermark to PDF Online Free | {site}",
        h1="Watermark PDF",
        meta="Add a text watermark to your PDF online. Control size, opacity, angle, colour and tiling.",
        keywords="watermark pdf, add watermark to pdf, pdf watermark online, text watermark",
        steps=["Upload your PDF.", "Type the watermark text and style it.", "Download the watermarked PDF."],
        about="Stamp CONFIDENTIAL, DRAFT or your name on every page, single or tiled, in front of or behind "
              "the content.",
        faq=_faq(PRIVACY_FAQ)),
    "img2pdf": dict(
        slug="jpg-to-pdf", icon="🖼️",
        title="JPG to PDF – Convert Images to PDF Online Free | {site}",
        h1="Images to PDF",
        meta="Convert JPG, PNG and other images to a single PDF online for free. Choose A4, Letter or fit-to-image.",
        keywords="jpg to pdf, image to pdf, png to pdf, photo to pdf, convert images to pdf",
        steps=["Upload your images.", "Arrange the order and choose page size.", "Download one PDF."],
        about="Turn photos and scans into a clean PDF. JPEGs are embedded without extra quality loss.",
        faq=_faq(("Which formats work?", "JPG, PNG, BMP, TIFF, WEBP and GIF."), PRIVACY_FAQ)),
    "pdf2img": dict(
        slug="pdf-to-jpg", icon="📷",
        title="PDF to JPG / PNG – Convert PDF to Images Online Free | {site}",
        h1="PDF to Images",
        meta="Convert PDF pages to high-quality JPG or PNG images online for free.",
        keywords="pdf to jpg, pdf to png, pdf to image, convert pdf to images",
        steps=["Upload your PDF.", "Choose PNG or JPG and resolution.", "Download images as a ZIP."],
        about="Export every page (or selected pages) as pictures at up to 300 DPI.",
        faq=_faq(PRIVACY_FAQ)),
    "pdf2txt": dict(
        slug="pdf-to-text", icon="📝",
        title="PDF to Text – Extract Text from PDF Online Free | {site}",
        h1="PDF to Text",
        meta="Extract text from a PDF into a .txt file online for free.",
        keywords="pdf to text, extract text from pdf, pdf to txt",
        steps=["Upload your PDF.", "Choose pages and options.", "Download the text file."],
        about="Pull selectable text out of a PDF. Scanned image-only PDFs have no text layer to extract.",
        faq=_faq(PRIVACY_FAQ)),
    "protect": dict(
        slug="protect-pdf", icon="🔒",
        title="Protect PDF with Password Online Free (AES-256) | {site}",
        h1="Protect PDF",
        meta="Add a password to your PDF with AES-256 encryption. Restrict printing, copying and editing.",
        keywords="protect pdf, password protect pdf, encrypt pdf, lock pdf, pdf password lagana",
        steps=["Upload your PDF.", "Enter a password and choose permissions.", "Download the protected PDF."],
        about="Encrypt a PDF with AES-256 and control whether others can print, copy or edit it.",
        faq=_faq(("Do you store my password?", "No. It is used only for this one operation."), PRIVACY_FAQ)),
    "unlock": dict(
        slug="unlock-pdf", icon="🔓",
        title="Unlock PDF – Remove PDF Password (You Know) Online Free | {site}",
        h1="Unlock PDF",
        meta="Remove the password from a PDF you own by entering its current password.",
        keywords="unlock pdf, remove pdf password, decrypt pdf",
        steps=["Upload the protected PDF.", "Enter its current password.", "Download the unlocked PDF."],
        about="Removes the password from PDFs you are allowed to open. You must know the current password; "
              "this tool does not crack passwords.",
        faq=_faq(PRIVACY_FAQ)),
    "repair": dict(
        slug="repair-pdf", icon="🛠️",
        title="Repair PDF Online Free – Fix Corrupted PDF | {site}",
        h1="Repair PDF",
        meta="Try to repair a damaged or corrupted PDF and recover its pages online for free.",
        keywords="repair pdf, fix corrupted pdf, recover pdf",
        steps=["Upload the damaged PDF.", "Click Repair.", "Download the recovered file."],
        about="Rebuilds the structure of a broken PDF so it opens again whenever recovery is possible.",
        faq=_faq(PRIVACY_FAQ)),
}
