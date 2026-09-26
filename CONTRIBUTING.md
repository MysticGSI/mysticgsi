# Contributing

Thanks for helping out. Bug reports, new ROM patch sets and firmware format
fixes are all welcome.

## Getting started

```sh
./setup_host.py --dev
.venv/bin/python -m pytest tests -q
.venv/bin/python -m flake8
```

Both must pass before you open a pull request.

## Guidelines

- Keep pull requests focused: one fix or feature per PR.
- Follow PEP 8 (79-character lines). Fix nearby code that doesn't, rather than
  copying its style.
- Only comment what the code can't say itself (why, not what), and only log
  what someone running a build needs to see.
- Add tests for logic that's easy to get wrong, like format parsing or offset
  math. Trivial code doesn't need them.
- Files over 50 MB under `patches/` go through `./tools/assets.py pack`; see
  the README.
- Only add vendor files (APKs, libraries, APEXes) you are able to share.
- Contributions are licensed under the [Apache License 2.0](LICENSE). Add
  yourself to [AUTHORS](AUTHORS) in your first PR.

## Using LLMs

AI assistants are welcome. They're a tool, not the brain: you are responsible
for everything you submit. Read and understand every change, test it on real
firmware where it matters, and don't open PRs or issues you haven't reviewed
yourself. "The AI wrote it" is not an answer to review feedback.
