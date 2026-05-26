# ICCE 2027 short paper

4-page IEEE conference-format submission targeting the
**IEEE International Conference on Consumer Electronics** (ICCE),
Las Vegas, NV, USA — January 2027.

Submission deadline (estimated, confirm on the official site):
**~September 2026** (full paper).

## Build

```sh
cd paper/icce2027/
pdflatex main && bibtex main && pdflatex main && pdflatex main
```

`refs.bib` is a copy of the master bibliography from
`paper/refs.bib` (synchronise occasionally).
`figures/pipeline.tex` is the TikZ source shared with the arXiv
long version.

## Page budget (target ≤ 4)

| Section | Approx. column space |
|---|---|
| Title + author + abstract  | 0.5 col |
| I. Introduction            | 0.5 col |
| II. System overview + Fig. 1 | 0.7 col |
| III. Sync-aligned outer FEC + Table I | 1.5 col |
| IV. SL demultiplexing & AVCC recovery | 1.5 col |
| V. Experimental verification + Table II | 1.0 col |
| VI. Conclusion             | 0.3 col |
| Acknowledgement + AI use disclosure | 0.3 col |
| References (~10)           | 0.7 col |

That sums to roughly 7 columns = 3.5 pages, leaving headroom for
margins and tables.  If we overflow, drop the AVCC code listing
and tighten paragraphs in Section IV.

## Pre-submission checklist

- [ ] Confirm exact ICCE 2027 submission deadline at
      <https://icce.org/2027/> when the site goes live
      (typically ~6 months before the conference)
- [ ] Replace placeholder `figures/pipeline.tex` rendering if the
      figure overflows the column (resize via `\resizebox`)
- [ ] Run a final 4-page check with
      `pdfinfo main.pdf | grep Pages`
- [ ] Suggested reviewers list (3–5 names from the SP / broadcast
      community)
- [ ] Disclosure: companion full version on arXiv plus extended
      version in submission to ETRI Journal / IEEE BMSB
- [ ] IEEE PDF eXpress validation before final upload
