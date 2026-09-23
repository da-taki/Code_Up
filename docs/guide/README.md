# CodeUp guides

Editable sources and generated PDFs for the two learner and instructor guides.

| Guide | Editable source | Generated PDF |
|---|---|---|
| Quick How-To Guide | `quick-how-to-guide.html` | `CodeUp_How_To_Use_Guide.pdf` |
| Complete Flagship How-To Guide (September 2026) | `full-how-to-guide.html` | `CodeUp_How_To_Guide_September_2026.pdf` |

## Regenerate both PDFs

```bash
py -m pip install -r requirements-dev.txt
py -m playwright install chromium
py scripts/build_guide_pdf.py
```

Use `python` instead of `py` outside Windows. Pass `quick` or `full` to build only one guide.

## Rules

- Edit the HTML source, then rebuild. Never edit the PDFs directly.
- The guides must describe the product as it behaves. If a guide and CodeUp disagree, fix whichever one is wrong and keep them in sync.
- `tests/test_how_to_guide_contract.py` checks guide claims against the app, including the editor Tab behavior (Tab and Shift+Tab leave the editor by default; Ctrl+] and Ctrl+[ indent and outdent; turning off Tab Leaves Editor restores Tab indentation). Run it after any guide change.
