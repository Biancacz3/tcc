"""Compare PostgreSQL JSON defaults without changing the stored column type.

PostgreSQL has equality for jsonb, not json. Alembic's built-in PostgreSQL
comparison executes the reflected and model defaults with the = operator;
reflection adds ::json, so even equivalent [] defaults can fail the check.
This callback compares only JSON/JSONB defaults as jsonb expressions. Other
types and dialects retain Alembic's normal server-default comparison.
"""
from sqlalchemy import JSON, cast, literal, literal_column, select
from sqlalchemy.dialects.postgresql import JSONB


def compare_server_default(context, inspected_column, metadata_column,
                           inspected_default, metadata_default,
                           rendered_metadata_default):
    if context.dialect.name != "postgresql":
        return None
    if not (isinstance(inspected_column.type, JSON)
            and isinstance(metadata_column.type, JSON)):
        return None
    if inspected_default is None or metadata_default is None:
        return (inspected_default is None) != (metadata_default is None)

    # These are schema defaults (database reflection and application metadata),
    # not request input. Preserve SQL expressions. A string DefaultClause.arg
    # is a SQL literal value, so bind it instead of treating it as SQL code.
    declared = metadata_default.arg
    if isinstance(declared, str):
        declared = literal(declared)
    reflected = literal_column(inspected_default)
    statement = select(
        cast(reflected, JSONB).is_distinct_from(cast(declared, JSONB))
    )
    result = context.connection.scalar(statement)
    if not isinstance(result, bool):
        raise RuntimeError("O banco nao retornou uma comparacao JSON valida.")
    return result
