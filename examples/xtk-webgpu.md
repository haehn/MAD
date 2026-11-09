Study the existing XTK WebGL framework (https://goxtk.com) and convert its rendering pipeline to WebGPU while preserving the minimal public API and existing functionality. Fix transformation errors encountered during the conversion and maintain compatibility with existing file loading, mesh processing, volume processing, interaction, and camera behavior.

Clone the repo. Inspect its source, examples, and tests before planning the migration; preserve unrelated local changes.

The Supervisor divides the migration into a compact set of Contracts with explicit owners, file outputs, acceptance criteria, and dependencies. Research and design should produce concise saved findings that implementation Contracts consume. Independent work may run in parallel; work on shared rendering and data interfaces must follow accepted prerequisites. Each Delivery receives independent QC before acceptance. Do not create separate QC Contracts.

Keep the existing public API and data pipeline wherever possible. Implement a working migration rather than only a proposal or scaffold. Include a small runnable demonstration using local test data and concise running instructions. Verify representative rendering, transforms, loading, and interaction against the existing behavior. Distinguish source inspection from observed runtime behavior. If WebGPU is unavailable, report the actual capability or adapter error and the remaining unverified behavior; a WebGL fallback is not evidence that WebGPU works.


WORKSPACE:
/tmp/X/

SUCCESS:
- The migrated rendering pipeline uses WebGPU while preserving the existing minimal public API.
- Existing file loading, mesh and volume processing, transforms, camera behavior, and interaction remain compatible.
- A local demonstration exercises representative migrated functionality, with reproducible checks and running instructions.
- QC independently checks each Contract; required runtime behavior has recorded execution evidence, and verification limitations are explicit.

AGENTS:
- Supervisor who plans the full conversion and supervises all other agents (Supervisor.md:Qwen/Qwen3-Coder-Next)
- Software Architect who designs the WebGPU architecture and migration strategy (Architect.md:Qwen/Qwen3-Coder-Next)
- Researcher who studies the current XTK implementation, WebGPU requirements, and relevant migration approaches (Researcher.md:Qwen/Qwen3-Coder-Next)
- Rendering Coder who converts rendering, shaders, interaction, camera, and GPU resource management to WebGPU (Coder.md:Qwen/Qwen3-Coder-Next)
- Data Coder who maintains and adapts file loading, mesh processing, volume processing, and data preparation (Coder.md:Qwen/Qwen3-Coder-Next)
- Debugging Expert who investigates transformation and rendering errors during the migration (Expert.md:Qwen/Qwen3-Coder-Next)
- Quality Control who independently verifies rendering behavior, regressions, compatibility, and tests (Tester.md:Qwen/Qwen3-Coder-Next)
