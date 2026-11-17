# Contributing to LithoMask

Thank you for helping make open photomask tooling better! Bug reports, documentation fixes, new examples and code are all welcome.

## Ways to contribute
- **Report a bug** — use the [bug report form](https://github.com/AtharvSagare/LithoMask/issues/new?template=bug_report.md). A small `.mop` or `.gds` file that reproduces the problem is the most useful thing you can attach.
- **Suggest a feature** — use the [feature request form](https://github.com/AtharvSagare/LithoMask/issues/new?template=feature_request.md).
- **Improve the docs** — typos, clearer explanations and new worked examples are very welcome.
- **Share measurements** — anonymised calibration data (CD / space / EP gauges) helps validate the resist models.

## Development setup
```bash
git clone https://github.com/AtharvSagare/LithoMask.git
cd LithoMask
install.bat                      # Windows; or: python -m venv .venv && pip install -r requirements.txt
python -m pytest -q              # full suite (~10 min); run a single file while developing
```

## Ground rules
1. **Units and conventions.** Internal geometry is in **nanometres (float)**. State units in docstrings and name variables with units where it helps (`pitch_nm`).
2. **No fabricated formulas or file-format details.** Cite the source for every equation (see [`docs/technical-notes`](docs/technical-notes)).
   If something is unknown, mark it `TODO-VERIFY` rather than guessing — this is especially important for proprietary formats such as JEOL `.v30`.
3. **Tests.** New behaviour needs a test in `tests/`. Keep GUI logic thin: the GUI calls the engine, never the other way round.
4. **No new heavy dependencies** without discussing it in an issue first.
5. **Keep pull requests focused** — one change per PR, with a short description of *why*.

## Pull request checklist
- [ ] `python -m pytest -q` passes locally (or the relevant test files do)
- [ ] New or changed behaviour has a test
- [ ] Docs / `CHANGELOG.md` updated if users will notice the change
- [ ] No generated files committed (`build/`, `dist/`, `.venv/`, `*_out/`)

By contributing you agree that your contribution is licensed under the project's [MIT License](LICENSE).
Please follow our [Code of Conduct](CODE_OF_CONDUCT.md).