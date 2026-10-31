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
- Supervisor who plans the full project and supervises all other agents (Supervisor.md:Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8)
- Software Architect who designs the technical architecture and interfaces (Architect.md:Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8)
- Researcher who studies relevant existing approaches and informs the Supervisor (Researcher.md:Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8)
- Backend Coder who implements the backend and API (Coder.md:Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8)
- Frontend Coder who implements the user interface and integrates it with the API (Coder.md:Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8)
- Quality Control who independently tests and verifies the completed work (Tester.md:Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8)
