Create a small Three.js web application that displays a simplified interactive solar system.

The scene should contain the Sun, Earth, and Moon. Earth should orbit the Sun, the Moon should orbit Earth, and the user should be able to rotate and zoom the camera using mouse controls.

The implementation should be intentionally small and easy to understand. The implementation should not require a build system unless necessary.
Prefer a simple browser-runnable HTML/JavaScript solution.

WORKSPACE:
./threejs-solar

SUCCESS:
- The project runs locally in a web browser.
- Three.js is used for rendering.
- The scene contains a Sun, Earth, and Moon.
- Earth visibly orbits the Sun.
- The Moon visibly orbits Earth.
- The user can rotate and zoom the camera.
- Objects use clearly different materials or colors.
- The browser console contains no runtime errors.
- The project includes a short README with instructions for running it.
- Relevant functionality is verified before completion.

AGENTS:
- Supervisor who coordinates the project, maintains the task graph, and verifies that the final application satisfies the project goals (Supervisor.md:Qwen/Qwen3-8B)
- Software Architect who designs the minimal project structure and Three.js scene architecture (Architect.md:Qwen/Qwen3-8B)
- Three.js Coder who implements the Three.js application, animation, camera controls, and project files (Coder.md:Qwen/Qwen3-8B)
- Quality Control who reviews the implementation, checks requirements, and identifies bugs or missing functionality (Tester.md:Qwen/Qwen3-8B)
