Create a small browser-runnable Three.js solar system with the Sun, Earth, and Moon. Earth must orbit the Sun, the Moon must orbit Earth, and mouse controls must rotate and zoom the camera. Give the objects distinct colors or materials. Keep the code compact and readable, with no build system unless necessary.

Use exactly one implementation task assigned to the Coder. That task includes creating the files, debugging, verification, and short running instructions. Do not create separate architecture, setup, documentation, or testing tasks. Do not create planning or test-report deliverables.

The Coder may inspect, edit, and verify the entire application within this task. Use actual browser errors to diagnose problems, make a concrete correction, and check again. If a module cannot be resolved, inspect the imports and their dependencies rather than repeating the same check. A passing browser load alone does not verify orbital motion or mouse controls; report which behaviors were actually checked and any verification limitations.

Deliver index.html, main.js, and README.md. Keep styling inside index.html unless a separate file is necessary. The README only needs concise local running instructions.

WORKSPACE:
./threejs-solar-simple

SUCCESS:
- The application runs in a browser using Three.js with no runtime errors.
- Sun, Earth, and Moon are visible with distinct colors or materials.
- Earth visibly orbits the Sun and the Moon visibly orbits Earth.
- Mouse controls rotate and zoom the camera.
- README.md explains how to run locally.
- Verification distinguishes observed behavior from untested claims.

AGENTS:
- Supervisor who assigns one complete implementation task to the Coder and reviews its actual deliverables without adding intermediate tasks (Supervisor.md:Qwen/Qwen3.5-27B-GPTQ-Int4)
- Coder who implements, debugs, and verifies the complete application in one task, then submits the files and verification evidence (Coder.md:Qwen/Qwen3.5-27B-GPTQ-Int4)
