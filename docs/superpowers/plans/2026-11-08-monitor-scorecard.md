# Monitor modernization scorecard implementation

Goal: compare starting and latest assessments from the selected run.

1. Add journal coverage for assessment score, categories, timestamp,
   completeness, profile fingerprint and largest deductions. Use the
   assessment already returned by Analyze. Preserve old journal reads.
2. Test score selection, zero scores, single/duplicate assessments and
   incompatible scoring coverage. Build an expandable React card with
   first/latest readings and a category comparison table.
3. Run journal and UI regression tests, static checks and the asset build.
   Review the patch and keep it isolated in the feature worktree.
