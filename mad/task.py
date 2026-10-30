class Task:
    VALID_STATUSES = {
        "pending",
        "running",
        "review",
        "completed",
        "blocked",
        "cancelled",
    }

    def __init__(
        self,
        id,
        title,
        description="",
        owner=None,
        dependencies=None,
        acceptance=None,
        status="pending",
    ):
        self.id = id
        self.title = title
        self.description = description
        self.owner = owner
        self.dependencies = dependencies or []
        self.acceptance = acceptance or []

        if status not in self.VALID_STATUSES:
            raise ValueError(
                f"Invalid task status: {status}"
            )

        self.status = status

    @classmethod
    def from_dict(
        cls,
        data,
    ):
        return cls(
            id=data["id"],
            title=data["title"],
            description=data.get(
                "description",
                "",
            ),
            owner=data.get(
                "owner"
            ),
            dependencies=data.get(
                "dependencies",
                [],
            ),
            acceptance=data.get(
                "acceptance",
                [],
            ),
            status=data.get(
                "status",
                "pending",
            ),
        )

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "owner": self.owner,
            "dependencies": list(
                self.dependencies
            ),
            "acceptance": list(
                self.acceptance
            ),
            "status": self.status,
        }

    def __repr__(self):
        return (
            f"Task("
            f"id={self.id!r}, "
            f"title={self.title!r}, "
            f"owner={self.owner!r}, "
            f"status={self.status!r}"
            f")"
        )
