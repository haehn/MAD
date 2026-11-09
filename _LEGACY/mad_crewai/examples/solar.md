PROJECT:
Create a small browser-runnable Three.js solar system with the Sun, Earth, and Moon. Earth orbits the Sun; the Moon orbits Earth. Mouse controls rotate and zoom the camera. Use distinct colors, CDN-linked Three.js, and no build system.
After a working version exists, polish its visual presentation while preserving all functionality. Keep the code compact and readable. Deliver index.html and concise running instructions in README.md.

WORKSPACE:
./crewai-solar

INTELLIGENCE:
server: http://localhost:8012/v1
model: Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8

AGENTS:
- name: Supervisor
  role: ../roles/Supervisor.md
- name: Architect
  role: ../roles/Architect.md
- name: Coder One
  role: ../roles/Coder.md
- name: Coder Two
  role: ../roles/Coder.md
- name: Tester One
  role: ../roles/Tester.md
- name: Tester Two
  role: ../roles/Tester.md

OWNER:
Supervisor

QA:
Tester Two

INPUT:
files: []
data: {}

OUTPUT:
files: [index.html, README.md]
data: {}

ACCEPTANCE:
- The final application displays the Sun, Earth, and Moon distinctly.
- Earth moves around the Sun and the Moon moves around Earth.
- Mouse dragging rotates the camera and scrolling changes its distance.
- The final application loads without JavaScript errors and requires no build system.
- The final visual improvements preserve the required objects, motion, and controls.
- README.md provides concise working instructions for local use.
