# Monitor node details

Keep the existing workflow graph and details panel. Give Analyze and Migrate
their own summaries so clicking a node answers what was discovered or which
recipe is underway.

- **Analyze:** Show the latest Analyze visit and the project Java version,
  Spring Boot version, build system, and detected frameworks from its recorded
  discovery snapshot. Omit unavailable fields; distinguish an unfinished
  analysis from an older run without a summary.
- **Migrate:** Show only the current recipe from the latest Migrate visit,
  or the last recipe reached if that visit has ended. Include the display
  name, position in the plan, recipe ID, and latest recorded status. Show a
  concise recorded failure reason when available and a link to the matching
  child run for deeper inspection. Before a recipe starts, show that no
  recipe has been recorded yet. Do not show the entire planned list here.
- **Plan** keeps its ordered list. Other nodes keep their existing details.

## Approaches

Use small, optional summaries in the run journals, as agreed. Capture the
analysis facts from the snapshot already gathered by Analyze. Associate
recipe progress and failure details with the parent visit and recipe step
that produced them. This supports both live updates and reopening a run.
Persist only structured facts and concise workflow failure messages.

Reading saved assessment and migration files on click could recover more
details from older runs, but depends on those files still being present
and correctly matched to the visit. Leave that out of this change. Existing
journals remain readable and show the details they already contain.
