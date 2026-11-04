PROJECT:
Write two short suggestions for a reading club: one focused on fiction and one on nonfiction. Each suggestion should explain its appeal in at most 80 words. Put them into separate files, then combine both into a concise comparison with a recommendation in comparison.txt. The two suggestions can be written independently.

WORKSPACE:
./crewai-reading

INTELLIGENCE:
server: http://localhost:8012/v1
model: Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8

AGENTS:
- name: Supervisor
  role: ../roles/Supervisor.md
- name: Writer One
  role: ../roles/Writer.md
- name: Writer Two
  role: ../roles/Writer.md
- name: QA One
  role: ../roles/Tester.md
- name: QA Two
  role: ../roles/Tester.md

OWNER:
Supervisor

QA:
QA Two

INPUT:
files: []
data: {}

OUTPUT:
files: [fiction.txt, nonfiction.txt, comparison.txt]
data: {}

ACCEPTANCE:
- fiction.txt suggests fiction and explains its appeal in at most 80 words.
- nonfiction.txt suggests nonfiction and explains its appeal in at most 80 words.
- comparison.txt compares the two saved suggestions and makes a clear recommendation.
