# Evals

An eval set checks that one agent (role card) still does its job after you change a prompt, a skill or a script.
The harness is `scripts/kernel/evals.py`; its module docstring defines the case format and every check kind.

This repo ships the harness only. Write your own sets: one folder per role, `evals/<role>/cases.yaml`
with at least three cases (happy path, hard case, adversarial or safety). Use synthetic fixtures only; never
copy real records (people, companies, jobs, messages) into a case.

```
evals/
  <role>/cases.yaml            the cases
  <role>/fixtures/<case-id>/   files copied into the sandbox before the run
  <role>/results/              written by `evals.py grade` (append-only history)
```

See `evals/example-role/cases.yaml.example` for a starting point. Run: `python3 scripts/kernel/evals.py list`.
