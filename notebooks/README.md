# Notebooks

Exploratory work only. Don't import notebooks from production code under `backend/`.

If a function or pattern from a notebook is good enough to ship, port it into `backend/` and write tests for it there.

Notebooks I've used during the build (kept locally, gitignored):

- `01_xbrl_concept_exploration.ipynb`: surveyed GAAP concept names across the 20 tickers and built the synonym mapping in `xbrl_parser.py`.
- `02_chunk_size_ablation.ipynb`: evaluated retrieval recall vs chunk size for 10-K Item 1A. Settled on 2048 chars target with 200-char overlap.
- `03_calculator_edge_cases.ipynb`: interactive playground for the AST walker. Used while building `calculator.py`.
- `04_eval_calibration.ipynb`: human-vs-LLM-judge agreement on the 30-question calibration set.
