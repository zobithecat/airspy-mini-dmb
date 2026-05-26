# Paper source

Source for the arXiv preprint *"An End-to-End Open-Source Software
Receiver for Korean Terrestrial DMB on Low-Cost SDR Hardware."*

## Build on Overleaf

1. Create a new project on Overleaf (Blank Project).
2. Upload `main.tex`, `refs.bib`, and the `figures/` directory.
3. Overleaf auto-detects `pdflatex` + `biber` from `main.tex`.
4. Click Compile.

If you prefer LuaLaTeX (for full Korean glyph support in any future
appendices), change the magic comment in `main.tex` line 1 to
`% !TEX program = lualatex` and add `\usepackage{fontspec}`.

## Build locally

```sh
cd paper/
latexmk -pdf -bibtex -interaction=nonstopmode main.tex
```

Tested with TeX Live 2024 on macOS.

## Author info to fill in

In `main.tex`:

- Authors are set to **Seonggeun Yoo** (primary, Independent Researcher,
  Seoul) and **Claude** (Anthropic, AI assistant — see footnote and
  Author Contributions section for disclosure).
- Contact email: `orcogre@vendit.co.kr`.
- Code & data availability section points to
  \url{https://github.com/zobithecat/airspy-mini-dmb}.  Make sure that
  repository is public and the reference capture / metadata sidecars
  are uploaded before you click Submit on arXiv.

If you'd prefer a different affiliation for the primary author (e.g.\
Vendit Inc.\ rather than ``Independent Researcher''), edit the
\texttt{\textbackslash author} block accordingly.

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
