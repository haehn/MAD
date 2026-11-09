# Contract

- id: unique identifier
- role: configured worker name
- input: supplied context and file references
- task: concrete work and scope
- output: relative file paths, or an empty list for inline delivery
- acceptance: explicit conditions QC checks
- dependencies: prerequisite contract IDs

State: OPEN → DELIVERED → QC → ACCEPTED. QC failure returns to OPEN for correction.
