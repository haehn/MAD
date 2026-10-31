Study the existing XTK WebGL framework (https://goxtk.com) and convert its rendering pipeline to WebGPU while preserving the minimal public API and existing functionality. Fix transformation errors encountered during the conversion and maintain compatibility with existing file loading, mesh processing, volume processing, interaction, and camera behavior.

WORKSPACE:
/tmp/X/

SUCCESS:
- Rendering uses WebGPU instead of WebGL.
- Existing public APIs remain compatible unless a change is strictly necessary.
- Existing examples continue to function.
- Transformation errors are identified and fixed.
- Camera and interaction behavior remain correct.
- Mesh rendering remains functional.
- Volume rendering remains functional.
- Existing file loading and data processing continue to work.
- Relevant existing tests pass.
- New regression tests cover important conversion issues and transformation fixes.
- Performance is evaluated for obvious regressions.

AGENTS:
- Supervisor who plans the full conversion and supervises all other agents (Supervisor.md:Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8)
- Software Architect who designs the WebGPU architecture and migration strategy (Architect.md:Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8)
- Researcher who studies the current XTK implementation, WebGPU requirements, and relevant migration approaches (Researcher.md:Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8)
- Rendering Coder who converts rendering, shaders, interaction, camera, and GPU resource management to WebGPU (Coder.md:Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8)
- Data Coder who maintains and adapts file loading, mesh processing, volume processing, and data preparation (Coder.md:Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8)
- Debugging Expert who investigates transformation and rendering errors during the migration (Expert.md:Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8)
- Quality Control who independently verifies rendering behavior, regressions, compatibility, and tests (Tester.md:Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8)
