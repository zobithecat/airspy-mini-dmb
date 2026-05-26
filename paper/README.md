# Paper source

Source for the arXiv preprint *"An End-to-End Open-Source Software
Receiver for Korean Terrestrial DMB on Low-Cost SDR Hardware"* (English,
`main.tex`) and its Korean companion (`main_ko.tex`).

## Build on Overleaf

1. Create a new project on Overleaf (Blank Project).
2. Upload `main.tex`, `main_ko.tex`, `refs.bib`, and the `figures/`
   directory together.
3. **Menu → Compiler → XeLaTeX** (required — `main_ko.tex` uses the
   `kotex` package for Korean typesetting; `main.tex` is XeLaTeX-
   compatible via the `iftex` shim in its preamble).
4. **Menu → Main document →** pick `main.tex` for the English version
   or `main_ko.tex` for the Korean version.  Recompile after switching.

The two files share `refs.bib` and `figures/` so any edit to a figure
or reference applies to both.  Bibliography is built by Overleaf with
`biber` automatically; for arXiv submission of the English version,
upload the resulting `main.bbl` file as well (arXiv does not run
`biber`).

## Build locally

```sh
cd paper/
# English (XeLaTeX)
latexmk -xelatex -bibtex -interaction=nonstopmode main.tex

# Korean (XeLaTeX + kotex)
latexmk -xelatex -bibtex -interaction=nonstopmode main_ko.tex
```

Tested with TeX Live 2024 on macOS.  The Korean version needs the
`kotex` package and a Korean font; both are pre-installed on
Overleaf and in TeX Live's `texlive-lang-korean` collection.

## Author info

- Single author: **Seonggeun Yoo** (Independent Researcher, Seoul).
- AI assistance (Anthropic's Claude) is disclosed in the
  "Acknowledgement of AI assistance" section near the end of the
  paper, per arXiv content moderation policy and IEEE/ACM disclosure
  guidelines.  Claude is **not** listed as a co-author — that would
  be incompatible with IEEE, ACM, Springer, Nature, and most other
  publisher policies adopted from 2023 onward.
- Contact email: `orcogre@gmail.com`.
- Code & data availability section points to
  https://github.com/zobithecat/airspy-mini-dmb .  Make sure that
  repository is public and the reference capture / metadata sidecars
  are uploaded before submission.

If submitting under a corporate affiliation rather than
"Independent Researcher" (e.g. VENDIT Inc.), edit the `\author`
block accordingly and obtain employer approval beforehand.

## Figures

- `figures/pipeline.tex` — TikZ source for the system overview diagram
  (loaded via `\input` from `main.tex`).
- `figures/idr_sample_1.png`, `figures/idr_sample_2.png` — sample IDR
  frames decoded by our pipeline from the K8B reference capture.
  (Currently the paper text does not yet `\includegraphics{}` these;
  add a figure in the verification section if you want to ship them
  in the camera-ready.)

## Reference captures cited

| Capture file | FIB rate | Used in |
|---|---|---|
| `data/captures/k8b_100pct.eti` | 100% | Tables I and II, all stage-3 results |
| `data/long_captures/k8b_5min.eti` | 75% | Time-variation discussion |

Both captures carry a JSON metadata sidecar
(`*.json`) recording the exact RF/SDR/environment parameters per the
schema in `tools/capture_logged.py`.
