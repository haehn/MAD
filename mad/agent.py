# mad/agent.py

class Agent:
    def __init__(self, name, description, template, model):
        self.name = name
        self.description = description
        self.template = template
        self.model = model

    def __repr__(self):
        return (
            f"Agent("
            f"name={self.name!r}, "
            f"template={self.template!r}, "
            f"model={self.model!r}"
            f")"
        )
