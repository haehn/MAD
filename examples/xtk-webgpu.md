Study the existing XTK WebGL framework and convert its rendering pipeline to WebGPU while preserving the minimal public API and existing functionality. Fix transformation errors encountered during the conversion and maintain compatibility with existing file loading, mesh processing, volume processing, interaction, and camera behavior.

WORKSPACE:
./XTK

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
- Director who plans the full conversion and supervises all other agents (Director.md:Qwen/Qwen3-8B)
- Software Architect who designs the WebGPU architecture and migration strategy (Architect.md:Qwen/Qwen3-8B)
- Researcher who studies the current XTK implementation, WebGPU requirements, and relevant migration approaches (Researcher.md:Qwen/Qwen3-8B)
- Rendering Coder who converts rendering, shaders, interaction, camera, and GPU resource management to WebGPU (Coder.md:Qwen/Qwen3-8B)
- Data Coder who maintains and adapts file loading, mesh processing, volume processing, and data preparation (Coder.md:Qwen/Qwen3-8B)
- Debugging Expert who investigates transformation and rendering errors during the migration (Expert.md:Qwen/Qwen3-8B)
- Quality Control who independently verifies rendering behavior, regressions, compatibility, and tests (SoftwareTester.md:Qwen/Qwen3-8B)
