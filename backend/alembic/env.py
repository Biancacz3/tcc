import logging

from alembic import context

from app.db.engine import database_url, engine
from app.models import Base
from app.migration_defaults import compare_server_default


logging.basicConfig(
    format="%(levelname)s [%(name)s] %(message)s"
)
logging.getLogger("alembic").setLevel(logging.INFO)

target_metadata = Base.metadata


if context.is_offline_mode():
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=compare_server_default,
    )

    with context.begin_transaction():
        context.run_migrations()
else:
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=compare_server_default,
        )

        with context.begin_transaction():
            context.run_migrations()
