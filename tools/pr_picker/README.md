# PR Picker

This tool scans open CPython pull requests, keeps only PRs whose checks are
green and whose diffs touch compiled-source files, then uses `gpt-5.4` through
the OpenAI Responses API to rank which PRs look most crash-prone for fuzzing.

It also skips any PR whose `projects/pr-<number>` directory already exists and
can create new pyfuzz projects for the top-ranked PRs.

## Environment

- `OPENAI_API_KEY`
- `GITHUB_TOKEN` or `GH_TOKEN`

## Usage

```bash
python tools/pr_picker/pick_prs.py --dry-run --output /tmp/pr-picker.json
```

Useful flags:

- `--candidate-count 100`
- `--group-size 20`
- `--stage1-max-picks 5`
- `--create-top 10`
- `--model gpt-5.4`
