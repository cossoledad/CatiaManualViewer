"""Functional scopes retain physical modules plus explicit shared-page membership."""


class FunctionalScope(list):
    def __init__(self, modules, manuals):
        super().__init__(dict.fromkeys(modules))
        self.manuals = tuple(dict.fromkeys(manuals))


def scope_sql(connection, modules, alias="d"):
    if modules is None:
        return "1", []
    if not modules:
        return "0", []
    values = list(dict.fromkeys(modules))
    clause = f"{alias}.module IN (" + ",".join("?" for _ in values) + ")"
    manuals = getattr(modules, "manuals", ())
    if (
        manuals
        and connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='manual_membership' AND type='table'"
        ).fetchone()
    ):
        clause = (
            "("
            + clause
            + " OR EXISTS(SELECT 1 FROM manual_membership ms "
            + f"WHERE ms.path={alias}.path AND ms.manual IN ("
        )
        clause += ",".join("?" for _ in manuals) + ")))"
        values.extend(manuals)
    return clause, values
