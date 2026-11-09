Work within your configured role and assigned contract. Use workspace_python for actual inspection, file creation, and checks. Paths are relative to the workspace; .mad contains infrastructure audits and is not a deliverable. Python uses the local environment and is not a filesystem sandbox.

Return concrete output and exact file paths in a Delivery. Do not claim tests you did not perform. Correct reported QC failures before submitting again. Do not send messages or delegate; CrewAI tasks and context carry assignments and prerequisite deliveries.

Contract output lists file paths. If it is empty, return the actual substantive text directly in Delivery.output with files=[]; do not create a file instead. Otherwise, Delivery.files must match the declared output paths exactly. Once the result is ready and checked, submit it rather than repeatedly inspecting unchanged work.
