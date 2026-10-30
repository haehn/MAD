class Project:
    def __init__(
        self,
        objective="",
        workspace="",
        success=None,
        agents=None,
    ):
        self.objective = objective
        self.workspace = workspace
        self.success = success or []
        self.agents = agents or []

    @classmethod
    def from_file(cls, path):
        from mad.parser import ProjectParser
        return ProjectParser(path).parse()

    def __repr__(self):
        return (
            f"Project("
            f"objective={self.objective!r}, "
            f"workspace={self.workspace!r}, "
            f"success={len(self.success)}, "
            f"agents={len(self.agents)}"
            f")"
        )
