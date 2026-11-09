# Contract

- id: unique identifier
- owner: exact configured worker/agent name from project AGENTS, not a role type or template
- input: supplied context and file references
- task: concrete work and scope
- output: relative file paths, or an empty list for inline delivery
- acceptance: conditions as {id: "A1", criterion: "explicit condition", verification: "inspection" or "execution"}; IDs are unique within the Contract; use execution for required observable behavior
- dependencies: prerequisite contract IDs

State: OPEN → DELIVERED → QC → ACCEPTED. QC failure returns to OPEN for correction.
