This folder houses the evals used to verify migration results.

The `corpora` directory stores YAML files describing projects for migration testing.

Run the Java 17/Spring Boot 3.2 corpus end to end with AI build repair enabled (the default):

```bash
uv run refinery run-corpus \
  evals/corpora/refinery-java8-spring-boot-test-corpus.yaml
```

Each repository is cloned into a separate directory. The command writes a continuously updated
`summary.json` and one complete log per project under `.refinery/corpus-runs/<timestamp>/`. A
failed clone or conversion is recorded and the remaining projects continue. The command exits
nonzero when any selected project fails.

Use deterministic repair only, or stage a smaller run by project ID, tier, or count:

```bash
uv run refinery run-corpus \
  evals/corpora/refinery-java8-spring-boot-test-corpus.yaml \
  --no-ai-patching

uv run refinery run-corpus \
  evals/corpora/refinery-java8-spring-boot-test-corpus.yaml \
  --tier easy --limit 3

uv run refinery run-corpus \
  evals/corpora/refinery-java8-spring-boot-test-corpus.yaml \
  --project spring-thymeleaf-shop --project spring-boot-jpa-auditing
```

Use `--output-directory` to choose a durable result location and `--project-timeout` to adjust
the four-hour per-project safety limit. A corpus entry may include an optional `ref` field to pin
a tag, branch, or commit after cloning.

Projects marked with `test_configuration_needed: true` are skipped by default because their
baseline tests require external services or project-specific setup. Include them after configuring
their test environment with `--include-test-configuration-needed`.

Rerun only the unsuccessful projects from an earlier corpus run after improving deterministic
repairs:

```bash
uv run refinery run-corpus \
  evals/corpora/refinery-java8-spring-boot-test-corpus.yaml \
  --rerun-failed-from .refinery/corpus-runs/<prior-timestamp> \
  --no-ai-patching
```

This creates a new timestamped run and uses the current corpus configuration. Projects currently
marked `test_configuration_needed: true` remain excluded unless explicitly included.
