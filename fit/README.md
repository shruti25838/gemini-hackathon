# ethyneon-fit

Room-fit geometry for Ethyneon. Decides whether a piece of furniture fits in
a room, and says exactly why when it does not.

Pure Python. No web, database or network imports, so it runs unchanged inside
a sandbox.

```bash
python -m fit check --input case.json --output result.json
python -m fit version
```

Exit codes: `0` for a result of any kind, including `does_not_fit` and
`unknown`; `2` for malformed input, with a JSON error and no traceback.

See `docs/FIT_ENGINE.md` in the Ethyneon repository for the input and output
shapes, worked examples, and the rules about tentative and unknown results.
