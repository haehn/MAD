Create a gamified collaboration platform with a minimal user interface. Different configurable roles can complete tasks to earn points toward a leaderboard ranking. An admin can create and configure tasks and must approve claimed progress before points are awarded.

WORKSPACE:
./test-project

SUCCESS:
- Roles can be configured.
- Admins can create, edit, and manage tasks.
- Users can view available tasks and claim completion.
- Admins can approve or reject claimed progress.
- Approved tasks award the configured number of points.
- A leaderboard ranks users by approved points.
- The interface remains minimal and easy to use.
- Relevant functionality is tested.

AGENTS:
- Director who plans the full project and supervises all other agents (Director.md:Qwen/Qwen3-8B)
- Software Architect who designs the technical architecture and interfaces (Architect.md:Qwen/Qwen3-8B)
- Researcher who studies relevant existing approaches and informs the Director (Researcher.md:Qwen/Qwen3-8B)
- Backend Coder who implements the backend and API (Coder.md:Qwen/Qwen3-8B)
- Frontend Coder who implements the user interface and integrates it with the API (Coder.md:Qwen/Qwen3-8B)
- Quality Control who independently tests and verifies the completed work (SoftwareTester.md:Qwen/Qwen3-8B)
